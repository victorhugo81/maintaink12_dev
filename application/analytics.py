"""
Dashboard KPIs, Facility Health Score, Recurring Issue Detection and
Operational Insights (Phase 9).

Two layers, same reasoning as application/pm.py / costs.py / risk.py:

* Pure functions (date_range, detect_recurring_issues, facility_health,
  build_insights) take plain values/rows and have no Flask or DB dependency,
  so the math is directly unit-testable.
* Query functions (work_order_kpis, chart_data, facility_health_scores,
  build_dashboard, ...) aggregate in SQL — GROUP BY / SUM(CASE ...) over the
  indexed work_order columns — rather than loading work orders into Python,
  per PROJECT_PLAN.md's performance baseline. The only row-level scan is the
  recurring-issue detector, which is capped at RECURRING_SCAN_LIMIT rows of
  a few lightweight columns (never ORM objects), and every list handed to a
  template is LIMITed.

Every KPI here is one from PROJECT_PLAN.md's Canonical KPI List; where the
plan names a KPI without defining its computation (Backlog, Completion Rate,
SLA Compliance %, Repeat Work %, Assets Requiring Attention) the definition
used is spelled out on the function that computes it.
"""
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re

from application import workflow

VIEWS = ('executive', 'manager', 'technician', 'school_staff')
VIEW_LABELS = {
    'executive': 'Executive', 'manager': 'M&O Manager',
    'technician': 'Technician', 'school_staff': 'School Staff',
}
PRESETS = ('today', 'week', 'month', 'quarter', 'year', 'custom')
PRESET_LABELS = {
    'today': 'Today', 'week': 'This Week', 'month': 'This Month',
    'quarter': 'This Quarter', 'year': 'This Year', 'custom': 'Custom Range',
}
PERIOD_PHRASES = {
    'today': 'today', 'week': 'this week', 'month': 'this month',
    'quarter': 'this quarter', 'year': 'this year', 'custom': 'in the selected period',
}

RECURRING_THRESHOLD = 3       # same asset/room/facility/description this many times = recurring
RECURRING_SCAN_LIMIT = 5000   # newest N work orders in the filter window are scanned
CHART_TOP_N = 10
LIST_LIMIT = 20
BACKLOG_AGE_DAYS = 30

CONDITION_LABELS = ('Excellent', 'Good', 'Fair', 'Poor', 'Critical')
HEALTH_FACTORS = ('Asset Condition', 'Open/Overdue Work Orders', 'Recurring Problems',
                  'PM Compliance', 'Inspection Failures', 'Facility Age', 'Maintenance Cost')


def _today():
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def date_range(preset, today=None, start=None, end=None):
    """
    Resolve a preset to an inclusive (start, end) date pair. Calendar-based:
    'week' is Monday..today, 'month' is the 1st..today, etc. 'custom' uses the
    given start/end, falling back to 'month' if they're missing or inverted.
    Returns (start, end, effective_preset).
    """
    today = today or _today()
    if preset == 'custom' and start and end and start <= end:
        return start, end, 'custom'
    if preset == 'today':
        return today, today, 'today'
    if preset == 'week':
        return today - timedelta(days=today.weekday()), today, 'week'
    if preset == 'quarter':
        q_month = 3 * ((today.month - 1) // 3) + 1
        return date(today.year, q_month, 1), today, 'quarter'
    if preset == 'year':
        return date(today.year, 1, 1), today, 'year'
    return date(today.year, today.month, 1), today, 'month'


def _parse_date(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def parse_filters(args, visible_site_ids, today=None):
    """
    Build the filter dict every dashboard widget shares from request args.
    visible_site_ids is None for users who may pick any site, otherwise the
    list they're locked to (the site_id arg is then ignored).
    """
    today = today or _today()
    preset = args.get('preset', 'month')
    if preset not in PRESETS:
        preset = 'month'
    start, end, preset = date_range(preset, today, _parse_date(args.get('start')), _parse_date(args.get('end')))

    def _int(name):
        value = args.get(name, type=int)
        return value if value else None

    site_id = _int('site_id') if visible_site_ids is None else None
    status = args.get('status') or None
    if status not in workflow.ALL_STATUSES:
        status = None
    team = (args.get('team') or '').strip() or None

    return {
        'preset': preset, 'start': start, 'end': end,
        'period_label': PERIOD_PHRASES[preset],
        'site_id': site_id,
        'site_ids': visible_site_ids if visible_site_ids is not None else ([site_id] if site_id else None),
        'facility_id': _int('facility_id'),
        'team': team,
        'technician_id': _int('technician_id'),
        'category_id': _int('category_id'),
        'priority_id': _int('priority_id'),
        'status': status,
        'requester_id': None,
    }


def _range_bounds(f):
    start_dt = datetime.combine(f['start'], datetime.min.time())
    end_dt = datetime.combine(f['end'] + timedelta(days=1), datetime.min.time())
    return start_dt, end_dt


def _wo_clauses(f, dated=True, column=None):
    """SQLAlchemy clauses for WorkOrder from the filter dict. `column` is the
    datetime column the date range applies to (default created_at)."""
    from application.models import WorkOrder
    clauses = []
    if f.get('site_ids'):
        clauses.append(WorkOrder.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        clauses.append(WorkOrder.facility_id == f['facility_id'])
    if f.get('team'):
        clauses.append(WorkOrder.assigned_team == f['team'])
    if f.get('technician_id'):
        clauses.append(WorkOrder.assigned_to_id == f['technician_id'])
    if f.get('category_id'):
        clauses.append(WorkOrder.category_id == f['category_id'])
    if f.get('priority_id'):
        clauses.append(WorkOrder.priority_id == f['priority_id'])
    if f.get('status'):
        clauses.append(WorkOrder.status == f['status'])
    if f.get('requester_id'):
        clauses.append(WorkOrder.requester_id == f['requester_id'])
    if dated:
        column = column if column is not None else WorkOrder.created_at
        start_dt, end_dt = _range_bounds(f)
        clauses.append(column >= start_dt)
        clauses.append(column < end_dt)
    return clauses


def _asset_clauses(f):
    from application.models import Asset
    clauses = [Asset.is_active.is_(True)]
    if f.get('site_ids'):
        clauses.append(Asset.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        clauses.append(Asset.facility_id == f['facility_id'])
    return clauses


def _days_between(later, earlier):
    """Fractional days between two DateTime columns, per dialect (SQLAlchemy
    has no portable datetime subtraction)."""
    from main import db
    from sqlalchemy import func, literal_column
    dialect = db.engine.dialect.name
    if dialect == 'sqlite':
        return func.julianday(later) - func.julianday(earlier)
    if dialect in ('mysql', 'mariadb'):
        return func.timestampdiff(literal_column('SECOND'), earlier, later) / 86400.0
    return func.extract('epoch', later - earlier) / 86400.0


def _num(value, digits=2):
    if value is None:
        return None
    return round(float(value), digits)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


# ---------------------------------------------------------------------------
# Work Order KPIs
# ---------------------------------------------------------------------------

def work_order_kpis(f, today=None):
    """
    Canonical Work Order KPIs. Point-in-time ones (Total Open, New Requests,
    Unassigned, In Progress, Waiting, Overdue, Critical, Emergency, Backlog)
    ignore the date range — they describe the current state of the filtered
    scope. Period ones use it: created-in-period counts, Completion Rate,
    Avg Response/Resolution Time, SLA Compliance %.

    Definitions the plan leaves open:
      Backlog          = open work orders created more than BACKLOG_AGE_DAYS ago.
      Completion Rate  = of work orders created in the period, % now Completed/Closed.
      Avg Response     = mean days from created_at to started_at (In Progress).
      Avg Resolution   = mean days from created_at to completed_at, over work
                         orders completed in the period.
      SLA Compliance % = of work orders completed in the period that had a due
                         date, % completed on or before it. (Placeholder until
                         Phase 11's per-priority SLA targets exist.)
      Repeat Work %    = of work orders created in the period, % belonging to a
                         detected recurring-issue group (filled in by
                         build_dashboard from the detector's output).
    """
    from main import db
    from sqlalchemy import func, case
    from application.models import WorkOrder, Priority

    today = today or _today()
    week_end = today + timedelta(days=7)
    backlog_before = datetime.combine(today - timedelta(days=BACKLOG_AGE_DAYS), datetime.min.time())
    pri = {p.name: p.id for p in Priority.query.filter(Priority.name.in_(('Emergency', 'Critical'))).all()}

    def flag(cond):
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    open_row = db.session.query(
        func.count(WorkOrder.id),
        flag(WorkOrder.status == workflow.NEW),
        flag(WorkOrder.assigned_to_id.is_(None)),
        flag(WorkOrder.status == workflow.IN_PROGRESS),
        flag(WorkOrder.status.in_(workflow.WAITING_STATUSES)),
        flag(WorkOrder.due_date < today),
        flag(WorkOrder.due_date == today),
        flag(WorkOrder.due_date.between(today + timedelta(days=1), week_end)),
        flag(WorkOrder.priority_id == pri.get('Critical', -1)),
        flag(WorkOrder.priority_id == pri.get('Emergency', -1)),
        flag(WorkOrder.created_at < backlog_before),
    ).filter(*_wo_clauses(f, dated=False), WorkOrder.status.in_(workflow.OPEN_STATUSES)).one()

    created_row = db.session.query(
        func.count(WorkOrder.id),
        flag(WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED))),
        func.avg(_days_between(WorkOrder.started_at, WorkOrder.created_at)),
    ).filter(*_wo_clauses(f, dated=True)).one()

    completed_row = db.session.query(
        func.count(WorkOrder.id),
        func.avg(_days_between(WorkOrder.completed_at, WorkOrder.created_at)),
        flag(WorkOrder.due_date.isnot(None)),
        flag(db.and_(WorkOrder.due_date.isnot(None), func.date(WorkOrder.completed_at) <= WorkOrder.due_date)),
    ).filter(*_wo_clauses(f, dated=True, column=WorkOrder.completed_at),
             WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED))).one()

    created = int(created_row[0] or 0)
    done_of_created = int(created_row[1] or 0)
    completed = int(completed_row[0] or 0)
    with_due = int(completed_row[2] or 0)
    met_due = int(completed_row[3] or 0)

    return {
        'total_open': int(open_row[0] or 0),
        'new_requests': int(open_row[1]),
        'unassigned': int(open_row[2]),
        'in_progress': int(open_row[3]),
        'waiting': int(open_row[4]),
        'overdue': int(open_row[5]),
        'due_today': int(open_row[6]),
        'due_this_week': int(open_row[7]),
        'critical': int(open_row[8]),
        'emergency': int(open_row[9]),
        'backlog': int(open_row[10]),
        'created_in_period': created,
        'completed': completed,
        'completion_rate': _pct(done_of_created, created),
        'avg_response_days': _num(created_row[2], 1),
        'avg_resolution_days': _num(completed_row[1], 1),
        'sla_compliance': _pct(met_due, with_due),
        'sla_sample': with_due,
        'repeat_work_pct': None,
    }


# ---------------------------------------------------------------------------
# Facility / Maintenance / Cost KPIs
# ---------------------------------------------------------------------------

def facility_kpis(f, today=None):
    """
    Canonical Facility KPIs. Assets Requiring Attention = current condition
    Fair or worse (Poor/Critical is the separate, narrower KPI). Assets Past
    Expected Life uses the asset's own expected_life_years, falling back to
    its type's, against install_date — same rule as risk.py's age factor.
    """
    from main import db
    from sqlalchemy import func
    from application.models import Facility, Room, Asset, AssetType, WorkOrder

    today = today or _today()
    fac_clauses = [Facility.is_active.is_(True)]
    room_clauses = [Room.is_active.is_(True)]
    if f.get('site_ids'):
        fac_clauses.append(Facility.site_id.in_(f['site_ids']))
        room_clauses.append(Room.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        fac_clauses.append(Facility.id == f['facility_id'])
        room_clauses.append(Room.facility_id == f['facility_id'])
    asset_clauses = _asset_clauses(f)

    total_facilities = db.session.query(func.count(Facility.id)).filter(*fac_clauses).scalar() or 0
    total_rooms = db.session.query(func.count(Room.id)).filter(*room_clauses).scalar() or 0

    condition = {label: 0 for label in CONDITION_LABELS}
    condition['Unassessed'] = 0
    total_assets = 0
    for label, count in db.session.query(Asset.condition_label, func.count(Asset.id)) \
                                  .filter(*asset_clauses).group_by(Asset.condition_label).all():
        condition[label or 'Unassessed'] = int(count)
        total_assets += int(count)

    past_life = 0
    life_rows = db.session.query(Asset.install_date, Asset.expected_life_years, AssetType.expected_life_years) \
                          .join(AssetType, Asset.asset_type_id == AssetType.id) \
                          .filter(*asset_clauses, Asset.install_date.isnot(None)).all()
    for install_date, own_life, type_life in life_rows:
        life = own_life or type_life
        if life and (today - install_date).days / 365.25 > life:
            past_life += 1

    open_by_facility = db.session.query(Facility.name, func.count(WorkOrder.id)).select_from(WorkOrder) \
        .join(Facility, WorkOrder.facility_id == Facility.id) \
        .filter(*_wo_clauses(f, dated=False), WorkOrder.status.in_(workflow.OPEN_STATUSES)) \
        .group_by(Facility.id, Facility.name).order_by(func.count(WorkOrder.id).desc(), Facility.name) \
        .limit(CHART_TOP_N).all()

    return {
        'total_facilities': int(total_facilities),
        'total_rooms': int(total_rooms),
        'total_assets': total_assets,
        'assets_requiring_attention': condition['Fair'] + condition['Poor'] + condition['Critical'],
        'assets_poor_critical': condition['Poor'] + condition['Critical'],
        'assets_past_expected_life': past_life,
        'condition_distribution': condition,
        'open_issues_by_facility': [(name, int(count)) for name, count in open_by_facility],
    }


def maintenance_kpis(f, today=None):
    """PM Due/Overdue, Inspections Due/Overdue (point-in-time, site/facility
    scoped) and Preventive vs Corrective counts (period). 'Due' = due within
    the next 7 days, matching the PM/Inspection dashboards' week bucket."""
    from main import db
    from sqlalchemy import func, case
    from application.models import (MaintenanceSchedule, MaintenancePlan, Asset, Inspection, Room,
                                    WorkOrder, WorkOrderLabor)

    today = today or _today()
    week_end = today + timedelta(days=7)

    def flag(cond):
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    pm_row = db.session.query(
        flag(MaintenanceSchedule.next_due_date < today),
        flag(MaintenanceSchedule.next_due_date.between(today, week_end)),
    ).select_from(MaintenanceSchedule).join(MaintenancePlan, MaintenanceSchedule.maintenance_plan_id == MaintenancePlan.id) \
     .join(Asset, MaintenanceSchedule.asset_id == Asset.id) \
     .filter(MaintenancePlan.is_active.is_(True), *_asset_clauses(f)).one()

    insp_query = db.session.query(
        flag(Inspection.due_date < today),
        flag(Inspection.due_date.between(today, week_end)),
    ).select_from(Inspection).filter(Inspection.status == 'Scheduled')
    if f.get('site_ids'):
        insp_query = insp_query.filter(Inspection.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        insp_query = insp_query.outerjoin(Room, Inspection.room_id == Room.id) \
                               .outerjoin(Asset, Inspection.asset_id == Asset.id) \
                               .filter(func.coalesce(Inspection.facility_id, Room.facility_id, Asset.facility_id) == f['facility_id'])
    insp_row = insp_query.one()

    by_source = {s: 0 for s in workflow.SOURCES}
    for source, count in db.session.query(WorkOrder.source, func.count(WorkOrder.id)) \
                                   .filter(*_wo_clauses(f, dated=True)).group_by(WorkOrder.source).all():
        by_source[source] = int(count)
    preventive = by_source.get(workflow.SOURCE_PM, 0)
    corrective = sum(by_source.values()) - preventive

    labor_hours = db.session.query(func.coalesce(func.sum(WorkOrderLabor.labor_hours), 0)) \
        .join(WorkOrder, WorkOrderLabor.work_order_id == WorkOrder.id) \
        .filter(*_wo_clauses(f, dated=True)).scalar()

    return {
        'pm_overdue': int(pm_row[0]), 'pm_due': int(pm_row[1]),
        'inspections_overdue': int(insp_row[0]), 'inspections_due': int(insp_row[1]),
        'preventive': preventive, 'corrective': corrective,
        'preventive_pct': _pct(preventive, preventive + corrective),
        'by_source': by_source,
        'labor_hours': _num(labor_hours, 1) or 0.0,
    }


def cost_kpis(f):
    """Maintenance Cost (labor + material + contractor/vendor) and Estimated
    vs Actual over work orders created in the period. Actual prefers the
    CostRecord total, falling back to WorkOrder.actual_cost — the same rule
    as Phase 7's cost rollup."""
    from main import db
    from sqlalchemy import func, case
    from application.models import WorkOrder, CostRecord

    actual_expr = func.coalesce(CostRecord.total_cost, WorkOrder.actual_cost, 0)
    both = db.and_(WorkOrder.estimated_cost.isnot(None),
                   db.or_(CostRecord.total_cost.isnot(None), WorkOrder.actual_cost.isnot(None)))
    row = db.session.query(
        func.count(WorkOrder.id),
        func.coalesce(func.sum(actual_expr), 0),
        func.coalesce(func.sum(CostRecord.labor_cost), 0),
        func.coalesce(func.sum(CostRecord.material_cost), 0),
        func.coalesce(func.sum(CostRecord.vendor_cost), 0),
        func.coalesce(func.sum(WorkOrder.estimated_cost), 0),
        func.coalesce(func.sum(case((both, 1), else_=0)), 0),
        func.coalesce(func.sum(case((both, WorkOrder.estimated_cost), else_=0)), 0),
        func.coalesce(func.sum(case((both, actual_expr), else_=0)), 0),
    ).outerjoin(CostRecord, CostRecord.work_order_id == WorkOrder.id) \
     .filter(*_wo_clauses(f, dated=True)).one()

    count = int(row[0] or 0)
    actual = Decimal(str(row[1] or 0))
    return {
        'work_order_count': count,
        'actual_total': actual,
        'labor_cost': Decimal(str(row[2] or 0)),
        'material_cost': Decimal(str(row[3] or 0)),
        'vendor_cost': Decimal(str(row[4] or 0)),
        'estimated_total': Decimal(str(row[5] or 0)),
        'cost_per_work_order': (actual / count).quantize(Decimal('0.01')) if count else Decimal('0'),
        'both_count': int(row[6] or 0),
        'both_estimated': Decimal(str(row[7] or 0)),
        'both_actual': Decimal(str(row[8] or 0)),
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _month_axis(start, end):
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    labels = [date(y, m, 1).strftime('%b %Y') for y, m in months]
    return months, labels


def chart_data(f, maintenance=None, facility=None):
    """
    Every chart named in the phase, as {key: {'labels': [...], 'datasets':
    [{'label', 'data'}]}} — each a single GROUP BY over the filtered period
    (top-N limited where the axis is unbounded).
    """
    from main import db
    from sqlalchemy import func, extract
    from application.models import WorkOrder, Priority, Category, Facility, User, CostRecord

    dated = _wo_clauses(f, dated=True)
    months, month_labels = _month_axis(f['start'], f['end'])
    month_index = {ym: i for i, ym in enumerate(months)}
    year_col, month_col = extract('year', WorkOrder.created_at), extract('month', WorkOrder.created_at)
    charts = {}

    counts = dict(db.session.query(WorkOrder.status, func.count(WorkOrder.id)).filter(*dated).group_by(WorkOrder.status).all())
    charts['by_status'] = {'labels': list(workflow.ALL_STATUSES),
                           'datasets': [{'label': 'Work Orders', 'data': [int(counts.get(s, 0)) for s in workflow.ALL_STATUSES]}]}

    rows = db.session.query(Priority.name, func.count(WorkOrder.id)).select_from(WorkOrder).join(Priority, WorkOrder.priority_id == Priority.id) \
        .filter(*dated).group_by(Priority.id, Priority.name, Priority.sort_order).order_by(Priority.sort_order).all()
    charts['by_priority'] = _series(rows)

    rows = db.session.query(Facility.name, func.count(WorkOrder.id)).select_from(WorkOrder).join(Facility, WorkOrder.facility_id == Facility.id) \
        .filter(*dated).group_by(Facility.id, Facility.name).order_by(func.count(WorkOrder.id).desc(), Facility.name).limit(CHART_TOP_N).all()
    charts['by_facility'] = _series(rows)

    rows = db.session.query(Category.name, func.count(WorkOrder.id)).select_from(WorkOrder).join(Category, WorkOrder.category_id == Category.id) \
        .filter(*dated).group_by(Category.id, Category.name).order_by(func.count(WorkOrder.id).desc(), Category.name).limit(CHART_TOP_N).all()
    charts['by_category'] = _series(rows)

    rows = db.session.query(User.first_name, User.last_name, func.count(WorkOrder.id)).select_from(WorkOrder) \
        .join(User, WorkOrder.assigned_to_id == User.id).filter(*dated) \
        .group_by(User.id, User.first_name, User.last_name).order_by(func.count(WorkOrder.id).desc()).limit(CHART_TOP_N).all()
    charts['by_technician'] = _series([(f'{first} {last}', count) for first, last, count in rows])

    by_month = [0] * len(months)
    for y, m, count in db.session.query(year_col, month_col, func.count(WorkOrder.id)).filter(*dated).group_by(year_col, month_col).all():
        idx = month_index.get((int(y), int(m)))
        if idx is not None:
            by_month[idx] = int(count)
    charts['by_month'] = {'labels': month_labels, 'datasets': [{'label': 'Work Orders', 'data': by_month}]}

    cost_by_month = [0.0] * len(months)
    actual_expr = func.coalesce(CostRecord.total_cost, WorkOrder.actual_cost, 0)
    for y, m, total in db.session.query(year_col, month_col, func.sum(actual_expr)) \
                                 .outerjoin(CostRecord, CostRecord.work_order_id == WorkOrder.id) \
                                 .filter(*dated).group_by(year_col, month_col).all():
        idx = month_index.get((int(y), int(m)))
        if idx is not None:
            cost_by_month[idx] = _num(total) or 0.0
    charts['cost_over_time'] = {'labels': month_labels, 'datasets': [{'label': 'Actual Cost', 'data': cost_by_month}]}

    response = [None] * len(months)
    resolution = [None] * len(months)
    for y, m, avg_resp, avg_res in db.session.query(
            year_col, month_col,
            func.avg(_days_between(WorkOrder.started_at, WorkOrder.created_at)),
            func.avg(_days_between(WorkOrder.completed_at, WorkOrder.created_at))) \
            .filter(*dated).group_by(year_col, month_col).all():
        idx = month_index.get((int(y), int(m)))
        if idx is not None:
            response[idx] = _num(avg_resp, 1)
            resolution[idx] = _num(avg_res, 1)
    charts['trends'] = {'labels': month_labels, 'datasets': [
        {'label': 'Avg Response (days)', 'data': response},
        {'label': 'Avg Resolution (days)', 'data': resolution},
    ]}

    if maintenance:
        charts['pm_vs_corrective'] = {'labels': ['Preventive (PM)', 'Corrective'],
                                      'datasets': [{'label': 'Work Orders', 'data': [maintenance['preventive'], maintenance['corrective']]}]}
    if facility:
        dist = facility['condition_distribution']
        labels = list(CONDITION_LABELS) + ['Unassessed']
        charts['condition_distribution'] = {'labels': labels, 'datasets': [{'label': 'Assets', 'data': [dist[l] for l in labels]}]}
    return charts


def _series(rows, label='Work Orders'):
    return {'labels': [str(r[0]) for r in rows], 'datasets': [{'label': label, 'data': [int(r[1]) for r in rows]}]}


# ---------------------------------------------------------------------------
# Recurring Issue Detection
# ---------------------------------------------------------------------------

RecurringRow = namedtuple('RecurringRow', 'id wo_number title asset_id room_id facility_id category_id created_at')

_STOPWORDS = frozenset(('a', 'an', 'the', 'in', 'on', 'at', 'of', 'for', 'to', 'is', 'are', 'and', 'or',
                        'with', 'from', 'by', 'please', 'need', 'needs', 'again', 'still', 'has', 'have'))
_KIND_ORDER = {'asset': 0, 'room': 1, 'facility': 2, 'description': 3}


def normalize_title(title):
    """Lowercase, strip punctuation and filler words — 'similar description'
    means the same words in the same order after this normalization."""
    words = re.sub(r'[^a-z0-9]+', ' ', (title or '').lower()).split()
    return ' '.join(w for w in words if w not in _STOPWORDS)


def detect_recurring_issues(rows, threshold=RECURRING_THRESHOLD):
    """
    Pure detector. rows: iterables with id/wo_number/title/asset_id/room_id/
    facility_id/category_id/created_at (RecurringRow or a query Row). A group
    is recurring when at least `threshold` work orders share the same asset,
    the same room + category, the same facility + category, or the same
    normalized title. Groups whose work orders are entirely contained in a
    more specific group already reported (asset ⊂ room ⊂ facility) are
    dropped as redundant. Returns groups sorted by count desc, each:
    {'kind', 'key', 'rows', 'work_order_ids', 'count', 'facility_id',
    'latest', 'label'} — label is filled for description groups here and by
    label_recurring_groups() for the rest.
    """
    groups = {}

    def add(key, row):
        groups.setdefault(key, []).append(row)

    for r in rows:
        if r.asset_id:
            add(('asset', r.asset_id), r)
        if r.room_id and r.category_id:
            add(('room', r.room_id, r.category_id), r)
        if r.facility_id and r.category_id:
            add(('facility', r.facility_id, r.category_id), r)
        norm = normalize_title(r.title)
        if norm:
            add(('description', norm), r)

    candidates = []
    for key, members in groups.items():
        if len(members) < threshold:
            continue
        facility_ids = {m.facility_id for m in members}
        candidates.append({
            'kind': key[0], 'key': key, 'rows': members,
            'work_order_ids': {m.id for m in members}, 'count': len(members),
            'facility_id': next(iter(facility_ids)) if len(facility_ids) == 1 else None,
            'latest': max((m.created_at for m in members if m.created_at), default=None),
            'label': f'"{key[1]}" (similar descriptions)' if key[0] == 'description' else None,
        })
    candidates.sort(key=lambda g: (_KIND_ORDER[g['kind']], -g['count']))

    kept = []
    for g in candidates:
        if any(g['work_order_ids'] <= k['work_order_ids'] for k in kept):
            continue
        kept.append(g)
    kept.sort(key=lambda g: (-g['count'], _KIND_ORDER[g['kind']]))
    return kept


def recurring_rows(f):
    """The newest RECURRING_SCAN_LIMIT work orders in the filter window, as
    lightweight column tuples for the detector."""
    from main import db
    from application.models import WorkOrder
    return db.session.query(WorkOrder.id, WorkOrder.wo_number, WorkOrder.title, WorkOrder.asset_id,
                            WorkOrder.room_id, WorkOrder.facility_id, WorkOrder.category_id, WorkOrder.created_at) \
        .filter(*_wo_clauses(f, dated=True)).order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc()) \
        .limit(RECURRING_SCAN_LIMIT).all()


def label_recurring_groups(groups):
    """Resolve asset/room/facility/category ids in detector output to names
    with one lookup per entity type."""
    from application.models import Asset, Room, Facility, Category

    def lookup(model, ids):
        return {obj.id: obj for obj in model.query.filter(model.id.in_(ids)).all()} if ids else {}

    assets = lookup(Asset, {g['key'][1] for g in groups if g['kind'] == 'asset'})
    rooms = lookup(Room, {g['key'][1] for g in groups if g['kind'] == 'room'})
    facilities = lookup(Facility, {g['key'][1] for g in groups if g['kind'] == 'facility'} | {r.facility_id for r in rooms.values()})
    categories = lookup(Category, {g['key'][2] for g in groups if g['kind'] in ('room', 'facility')})

    for g in groups:
        if g['kind'] == 'asset':
            a = assets.get(g['key'][1])
            g['label'] = f'Asset {a.asset_tag} — {a.name}' if a else f'Asset #{g["key"][1]}'
        elif g['kind'] == 'room':
            r, c = rooms.get(g['key'][1]), categories.get(g['key'][2])
            fac = facilities.get(r.facility_id) if r else None
            place = f'{fac.name} Rm {r.room_number}' if (r and fac) else f'Room #{g["key"][1]}'
            g['label'] = f'{place} — {c.name if c else "Category"}'
        elif g['kind'] == 'facility':
            fac, c = facilities.get(g['key'][1]), categories.get(g['key'][2])
            g['label'] = f'{fac.name if fac else "Facility"} — {c.name if c else "Category"}'
        g['work_orders'] = sorted(((r.id, r.wo_number or f'WO #{r.id}', r.title) for r in g['rows']), key=lambda t: -t[0])
    return groups


def related_recurring_groups(work_order, today=None, days=365):
    """Recurring groups (from the last `days`) that contain this work order —
    the detail page's "Recurring Issue" panel. Candidates are limited to
    work orders sharing its asset, room, or facility + category, so this
    never scans a whole site."""
    from main import db
    from application.models import WorkOrder

    today = today or _today()
    since = datetime.combine(today - timedelta(days=days), datetime.min.time())
    ors = []
    if work_order.asset_id:
        ors.append(WorkOrder.asset_id == work_order.asset_id)
    if work_order.room_id:
        ors.append(db.and_(WorkOrder.room_id == work_order.room_id, WorkOrder.category_id == work_order.category_id))
    if work_order.facility_id:
        ors.append(db.and_(WorkOrder.facility_id == work_order.facility_id, WorkOrder.category_id == work_order.category_id))
    if not ors:
        return []
    rows = db.session.query(WorkOrder.id, WorkOrder.wo_number, WorkOrder.title, WorkOrder.asset_id,
                            WorkOrder.room_id, WorkOrder.facility_id, WorkOrder.category_id, WorkOrder.created_at) \
        .filter(WorkOrder.site_id == work_order.site_id, WorkOrder.created_at >= since, db.or_(*ors)) \
        .order_by(WorkOrder.created_at.desc()).limit(RECURRING_SCAN_LIMIT).all()
    groups = [g for g in detect_recurring_issues(rows) if work_order.id in g['work_order_ids']]
    return label_recurring_groups(groups)


# ---------------------------------------------------------------------------
# Facility Health Score
# ---------------------------------------------------------------------------

def _bucket(value, table):
    """table: ((upper_bound_inclusive, score), ...) ending with (None, score)."""
    for bound, score in table:
        if bound is None or value <= bound:
            return score
    return table[-1][1]


_OPEN_TABLE = ((0, 100), (2, 85), (5, 70), (10, 50), (20, 30), (None, 10))
_RECURRING_TABLE = ((0, 100), (1, 75), (2, 55), (4, 35), (None, 15))
_AGE_TABLE = ((10, 100), (25, 85), (40, 65), (60, 45), (None, 25))
_COST_RATIO_TABLE = ((0.5, 100), (1.0, 80), (1.5, 60), (2.0, 40), (None, 20))


def facility_health(facility, stats, today=None):
    """
    Pure Facility Health Score (0-100, higher = healthier) from the seven
    PROJECT_PLAN.md factors. Each factor is a 0-100 health contribution and
    the score is the mean of the AVAILABLE ones — a factor with no data is
    excluded, never defaulted (same contract as risk.calculate_risk).

    stats keys: avg_condition (float|None), open_wos, overdue_wos,
    recurring_groups, pm_total, pm_overdue, inspection_results,
    inspection_failures, maintenance_cost, district_avg_cost (Decimal|None).

      Asset Condition           mean current condition score of active assets
      Open/Overdue Work Orders  open-count bucket, minus 10 per overdue (floor 0)
      Recurring Problems        recurring-group count bucket
      PM Compliance             % of PM schedules not overdue
      Inspection Failures       100 - % of checklist results that failed (period)
      Facility Age              year_built bucket
      Maintenance Cost          period cost vs the average facility in scope
    """
    today = today or _today()
    factors = {}

    def put(name, value, available, detail):
        factors[name] = {'value': int(round(value)) if available else None, 'available': available, 'detail': detail}

    avg_condition = stats.get('avg_condition')
    put('Asset Condition', avg_condition or 0, avg_condition is not None,
        f'avg condition {round(avg_condition)}' if avg_condition is not None else 'no assessed assets')

    open_wos, overdue = stats.get('open_wos', 0), stats.get('overdue_wos', 0)
    put('Open/Overdue Work Orders', max(0, _bucket(open_wos, _OPEN_TABLE) - 10 * overdue), True,
        f'{open_wos} open, {overdue} overdue')

    recurring = stats.get('recurring_groups', 0)
    put('Recurring Problems', _bucket(recurring, _RECURRING_TABLE), True, f'{recurring} recurring issue(s)')

    pm_total, pm_overdue = stats.get('pm_total', 0), stats.get('pm_overdue', 0)
    put('PM Compliance', 100.0 * (pm_total - pm_overdue) / pm_total if pm_total else 0, pm_total > 0,
        f'{pm_total - pm_overdue} of {pm_total} PM schedules current' if pm_total else 'no PM schedules')

    results, failures = stats.get('inspection_results', 0), stats.get('inspection_failures', 0)
    put('Inspection Failures', 100.0 * (results - failures) / results if results else 0, results > 0,
        f'{failures} of {results} checklist items failed' if results else 'no completed inspections')

    year_built = getattr(facility, 'year_built', None)
    age = (today.year - year_built) if year_built else None
    put('Facility Age', _bucket(age, _AGE_TABLE) if age is not None else 0, age is not None,
        f'built {year_built} ({age} yrs)' if age is not None else 'year built unknown')

    cost, avg_cost = stats.get('maintenance_cost') or 0, stats.get('district_avg_cost')
    cost_available = bool(avg_cost) and avg_cost > 0
    ratio = float(cost) / float(avg_cost) if cost_available else 0
    put('Maintenance Cost', _bucket(ratio, _COST_RATIO_TABLE) if cost_available else 0, cost_available,
        f'${float(cost):,.0f} vs ${float(avg_cost):,.0f} avg' if cost_available else 'no cost data in period')

    available = [fct['value'] for fct in factors.values() if fct['available']]
    score = int(round(sum(available) / len(available))) if available else None
    return {'score': score, 'factors': factors, 'factor_count': len(available)}


def facility_health_scores(f, recurring_groups=(), today=None, facility_ids=None):
    """
    Health score for every active facility in the filter scope (or just
    `facility_ids`), computed from one GROUP BY per factor — the query count
    doesn't grow with the number of facilities.
    """
    from main import db
    from sqlalchemy import func, case
    from application.models import (Facility, Asset, WorkOrder, MaintenanceSchedule, MaintenancePlan,
                                    Inspection, InspectionResult, Room, CostRecord)

    today = today or _today()
    fq = Facility.query.filter(Facility.is_active.is_(True))
    if facility_ids:
        fq = fq.filter(Facility.id.in_(facility_ids))
    else:
        if f.get('site_ids'):
            fq = fq.filter(Facility.site_id.in_(f['site_ids']))
        if f.get('facility_id'):
            fq = fq.filter(Facility.id == f['facility_id'])
    facilities = fq.order_by(Facility.name).all()
    if not facilities:
        return []
    ids = [fac.id for fac in facilities]

    def flag(cond):
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    condition = dict(db.session.query(Asset.facility_id, func.avg(Asset.condition_score))
                     .filter(Asset.facility_id.in_(ids), Asset.is_active.is_(True), Asset.condition_score.isnot(None))
                     .group_by(Asset.facility_id).all())

    wo_scope = [c for c in _wo_clauses(f, dated=False)] if not facility_ids else []
    open_rows = db.session.query(WorkOrder.facility_id, func.count(WorkOrder.id), flag(WorkOrder.due_date < today)) \
        .filter(WorkOrder.facility_id.in_(ids), WorkOrder.status.in_(workflow.OPEN_STATUSES), *wo_scope) \
        .group_by(WorkOrder.facility_id).all()
    open_stats = {fid: (int(n), int(od)) for fid, n, od in open_rows}

    pm_rows = db.session.query(Asset.facility_id, func.count(MaintenanceSchedule.id), flag(MaintenanceSchedule.next_due_date < today)) \
        .select_from(MaintenanceSchedule).join(Asset, MaintenanceSchedule.asset_id == Asset.id) \
        .join(MaintenancePlan, MaintenanceSchedule.maintenance_plan_id == MaintenancePlan.id) \
        .filter(Asset.facility_id.in_(ids), Asset.is_active.is_(True), MaintenancePlan.is_active.is_(True)) \
        .group_by(Asset.facility_id).all()
    pm_stats = {fid: (int(n), int(od)) for fid, n, od in pm_rows}

    start_dt, end_dt = _range_bounds(f)
    fac_expr = func.coalesce(Inspection.facility_id, Room.facility_id, Asset.facility_id)
    insp_rows = db.session.query(fac_expr, func.count(InspectionResult.id), flag(InspectionResult.result == 'Fail')) \
        .select_from(InspectionResult) \
        .join(Inspection, InspectionResult.inspection_id == Inspection.id) \
        .outerjoin(Room, Inspection.room_id == Room.id) \
        .outerjoin(Asset, Inspection.asset_id == Asset.id) \
        .filter(Inspection.status == 'Completed', Inspection.completed_at >= start_dt, Inspection.completed_at < end_dt) \
        .group_by(fac_expr).all()
    insp_stats = {fid: (int(n), int(fails)) for fid, n, fails in insp_rows if fid in ids}

    wo_dated = _wo_clauses(f, dated=True) if not facility_ids else [WorkOrder.created_at >= start_dt, WorkOrder.created_at < end_dt]
    cost_rows = db.session.query(WorkOrder.facility_id, func.sum(func.coalesce(CostRecord.total_cost, WorkOrder.actual_cost, 0))) \
        .outerjoin(CostRecord, CostRecord.work_order_id == WorkOrder.id) \
        .filter(WorkOrder.facility_id.in_(ids), *wo_dated).group_by(WorkOrder.facility_id).all()
    costs = {fid: Decimal(str(total or 0)) for fid, total in cost_rows}
    district_avg = (sum(costs.values(), Decimal('0')) / len(ids)) if costs else None

    recurring_counts = {}
    for g in recurring_groups:
        if g.get('facility_id'):
            recurring_counts[g['facility_id']] = recurring_counts.get(g['facility_id'], 0) + 1

    rows = []
    for fac in facilities:
        open_n, overdue_n = open_stats.get(fac.id, (0, 0))
        pm_total, pm_overdue = pm_stats.get(fac.id, (0, 0))
        results, failures = insp_stats.get(fac.id, (0, 0))
        stats = {
            'avg_condition': float(condition[fac.id]) if condition.get(fac.id) is not None else None,
            'open_wos': open_n, 'overdue_wos': overdue_n,
            'recurring_groups': recurring_counts.get(fac.id, 0),
            'pm_total': pm_total, 'pm_overdue': pm_overdue,
            'inspection_results': results, 'inspection_failures': failures,
            'maintenance_cost': costs.get(fac.id, Decimal('0')), 'district_avg_cost': district_avg,
        }
        rows.append({'facility': fac, 'health': facility_health(fac, stats, today)})
    rows.sort(key=lambda r: (r['health']['score'] is None, r['health']['score'] or 0))
    return rows


# ---------------------------------------------------------------------------
# Operational Insights
# ---------------------------------------------------------------------------

def build_insights(data, period_label):
    """
    Pure, rule-based statements — each emitted only when the numbers in
    `data` support it, and every figure quoted comes from those numbers.
    data keys (all optional): wo (work_order_kpis), prior_created, prior_avg_resolution_days,
    top_category (name, count), total_in_period, facility_vs_avg {facility, category,
    count, avg, facility_count}, overdue_by_facility [(name, n)], maintenance
    (maintenance_kpis), facility (facility_kpis), recurring (groups), cost (cost_kpis).
    Returns [{'level': 'danger'|'warning'|'info', 'text'}] ordered by severity.
    """
    out = []
    wo = data.get('wo') or {}
    total = data.get('total_in_period', wo.get('created_in_period', 0)) or 0

    prior = data.get('prior_created')
    if prior and total:
        change = round(100.0 * (total - prior) / prior)
        if abs(change) >= 10:
            direction = 'up' if change > 0 else 'down'
            out.append({'level': 'warning' if change > 0 else 'info',
                        'text': f'Work order volume is {direction} {abs(change)}% {period_label} versus the previous period ({total} vs {prior}).'})

    fva = data.get('facility_vs_avg')
    if fva and fva.get('facility_count', 0) >= 2 and fva['count'] >= 3 and fva['avg']:
        pct = round(100.0 * (fva['count'] - fva['avg']) / fva['avg'])
        if pct >= 25:
            out.append({'level': 'warning',
                        'text': f"{fva['facility']} has {pct}% more {fva['category']} work orders than the average facility {period_label} ({fva['count']} vs {fva['avg']:.1f})."})

    overdue = wo.get('overdue', 0)
    if overdue:
        by_fac = data.get('overdue_by_facility') or []
        tail = f'; {by_fac[0][0]} has the most ({by_fac[0][1]})' if by_fac else ''
        out.append({'level': 'danger', 'text': f'{overdue} open work order{"s are" if overdue != 1 else " is"} past due{tail}.'})

    unassigned, total_open = wo.get('unassigned', 0), wo.get('total_open', 0)
    if unassigned and total_open:
        out.append({'level': 'warning', 'text': f'{unassigned} of {total_open} open work orders are unassigned ({_pct(unassigned, total_open)}%).'})

    m = data.get('maintenance') or {}
    if (m.get('preventive', 0) + m.get('corrective', 0)) >= 10 and m.get('preventive_pct') is not None:
        level = 'warning' if m['preventive_pct'] < 30 else 'info'
        out.append({'level': level,
                    'text': f"{m['preventive_pct']}% of work orders opened {period_label} were preventive (PM-generated); {m['corrective']} were corrective."})

    top = data.get('top_category')
    if top and total >= 5:
        share = _pct(top[1], total)
        if share >= 30:
            out.append({'level': 'info', 'text': f'{top[0]} accounts for {share}% of work orders opened {period_label} ({top[1]} of {total}).'})

    fac = data.get('facility') or {}
    poor, past = fac.get('assets_poor_critical', 0), fac.get('assets_past_expected_life', 0)
    if poor or past:
        out.append({'level': 'warning' if poor else 'info',
                    'text': f'{poor} asset{"s are" if poor != 1 else " is"} in Poor or Critical condition and {past} {"are" if past != 1 else "is"} past expected life.'})

    recurring = data.get('recurring') or []
    if recurring:
        top_group = recurring[0]
        out.append({'level': 'warning',
                    'text': f'{len(recurring)} recurring issue{"s" if len(recurring) != 1 else ""} detected {period_label}; most frequent: {top_group["label"]} ({top_group["count"]} work orders).'})

    cost = data.get('cost') or {}
    if cost.get('both_count', 0) >= 3 and cost.get('both_estimated'):
        diff = round(100.0 * float(cost['both_actual'] - cost['both_estimated']) / float(cost['both_estimated']))
        if abs(diff) >= 10:
            verb = 'exceeded' if diff > 0 else 'came in under'
            out.append({'level': 'warning' if diff > 0 else 'info',
                        'text': f"Actual cost {verb} estimates by {abs(diff)}% across {cost['both_count']} work orders with both recorded {period_label}."})

    if wo.get('sla_sample', 0) >= 5 and wo.get('sla_compliance') is not None:
        out.append({'level': 'info' if wo['sla_compliance'] >= 80 else 'warning',
                    'text': f"{wo['sla_compliance']}% of work orders completed {period_label} met their due date ({wo['sla_sample']} had one)."})

    res, prior_res = wo.get('avg_resolution_days'), data.get('prior_avg_resolution_days')
    if res is not None and prior_res:
        change = round(100.0 * (res - prior_res) / prior_res)
        if abs(change) >= 10:
            out.append({'level': 'warning' if change > 0 else 'info',
                        'text': f'Average resolution time is {res} days {period_label}, {"up" if change > 0 else "down"} {abs(change)}% from {prior_res} days the previous period.'})

    order = {'danger': 0, 'warning': 1, 'info': 2}
    out.sort(key=lambda i: order[i['level']])
    return out


def _prior_period(f):
    length = (f['end'] - f['start']).days + 1
    prior = dict(f)
    prior['end'] = f['start'] - timedelta(days=1)
    prior['start'] = prior['end'] - timedelta(days=length - 1)
    return prior


def _insight_queries(f, top_category, today):
    """The few extra aggregates insights need beyond the KPI dicts."""
    from main import db
    from sqlalchemy import func
    from application.models import WorkOrder, Facility, Category

    prior = _prior_period(f)
    prior_created = db.session.query(func.count(WorkOrder.id)).filter(*_wo_clauses(prior, dated=True)).scalar() or 0
    prior_res = db.session.query(func.avg(_days_between(WorkOrder.completed_at, WorkOrder.created_at))) \
        .filter(*_wo_clauses(prior, dated=True, column=WorkOrder.completed_at),
                WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED))).scalar()

    overdue_by_facility = db.session.query(Facility.name, func.count(WorkOrder.id)).select_from(WorkOrder) \
        .join(Facility, WorkOrder.facility_id == Facility.id) \
        .filter(*_wo_clauses(f, dated=False), WorkOrder.status.in_(workflow.OPEN_STATUSES), WorkOrder.due_date < today) \
        .group_by(Facility.id, Facility.name).order_by(func.count(WorkOrder.id).desc()).limit(3).all()

    facility_vs_avg = None
    if top_category:
        cat = Category.query.filter_by(name=top_category[0]).first()
        fac_clauses = [Facility.is_active.is_(True)]
        if f.get('site_ids'):
            fac_clauses.append(Facility.site_id.in_(f['site_ids']))
        facility_count = db.session.query(func.count(Facility.id)).filter(*fac_clauses).scalar() or 0
        if cat and facility_count >= 2:
            rows = db.session.query(Facility.name, func.count(WorkOrder.id)).select_from(WorkOrder) \
                .join(Facility, WorkOrder.facility_id == Facility.id) \
                .filter(*_wo_clauses(f, dated=True), WorkOrder.category_id == cat.id) \
                .group_by(Facility.id, Facility.name).order_by(func.count(WorkOrder.id).desc()).all()
            if rows:
                total_cat = sum(int(n) for _, n in rows)
                facility_vs_avg = {'facility': rows[0][0], 'category': cat.name, 'count': int(rows[0][1]),
                                   'avg': total_cat / facility_count, 'facility_count': facility_count}

    return {'prior_created': int(prior_created), 'prior_avg_resolution_days': _num(prior_res, 1),
            'overdue_by_facility': [(n, int(c)) for n, c in overdue_by_facility], 'facility_vs_avg': facility_vs_avg}


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------

def default_view(user):
    if user.role_id == 1:
        return 'executive'
    if user.role_id == 2:
        return 'manager'
    if user.role_id == 3:
        return 'technician'
    return 'school_staff'


def allowed_views(user):
    if user.role_id in (1, 2):
        return ('executive', 'manager')
    if user.role_id == 3:
        return ('technician',)
    return ('school_staff',)


def _work_order_list(f, today, order='due'):
    from main import db
    from application.models import WorkOrder
    q = WorkOrder.query.options(db.joinedload(WorkOrder.facility), db.joinedload(WorkOrder.priority),
                                db.joinedload(WorkOrder.category)).filter(*_wo_clauses(f, dated=False))
    if order == 'due':
        q = q.filter(WorkOrder.status.in_(workflow.OPEN_STATUSES)) \
             .order_by(WorkOrder.due_date.is_(None), WorkOrder.due_date, WorkOrder.created_at)
    else:
        q = q.order_by(WorkOrder.created_at.desc())
    return q.limit(LIST_LIMIT).all()


def build_dashboard(view, f, user, today=None):
    """Assemble everything one role view renders. Technician and School
    Staff views pin the filters to the user (assigned_to / requester) and
    their site before anything is queried."""
    today = today or _today()
    f = dict(f)
    if view == 'technician':
        f['technician_id'] = user.id
        f['site_ids'] = [user.site_id]
    elif view == 'school_staff':
        f['requester_id'] = user.id
        f['site_ids'] = [user.site_id]
        f['technician_id'] = None
        f['team'] = None

    wo = work_order_kpis(f, today)
    data = {'view': view, 'filters': f, 'today': today, 'wo': wo}

    if view in ('executive', 'manager'):
        facility = facility_kpis(f, today)
        maintenance = maintenance_kpis(f, today)
        cost = cost_kpis(f)
        charts = chart_data(f, maintenance=maintenance, facility=facility)
        groups = label_recurring_groups(detect_recurring_issues(recurring_rows(f)))
        recurring_ids = set().union(*(g['work_order_ids'] for g in groups)) if groups else set()
        wo['repeat_work_pct'] = _pct(len(recurring_ids), wo['created_in_period']) if wo['created_in_period'] else None
        health = facility_health_scores(f, groups, today)
        by_cat = charts['by_category']
        top_category = (by_cat['labels'][0], by_cat['datasets'][0]['data'][0]) if by_cat['labels'] else None
        extra = _insight_queries(f, top_category, today)
        insights = build_insights({
            'wo': wo, 'top_category': top_category, 'total_in_period': wo['created_in_period'],
            'maintenance': maintenance, 'facility': facility, 'recurring': groups, 'cost': cost, **extra,
        }, f['period_label'])
        data.update({'facility': facility, 'maintenance': maintenance, 'cost': cost, 'charts': charts,
                     'recurring': groups, 'health': health, 'insights': insights})
    elif view == 'technician':
        maintenance = maintenance_kpis({'site_ids': f['site_ids'], 'facility_id': f.get('facility_id'),
                                        'start': f['start'], 'end': f['end']}, today)
        charts = chart_data(f)
        data.update({'maintenance': maintenance, 'charts': {k: charts[k] for k in ('by_status', 'by_priority', 'by_month')},
                     'my_work_orders': _work_order_list(f, today, order='due')})
    else:
        site_f = dict(f, requester_id=None, status=None)
        charts = chart_data(f)
        data.update({'charts': {'by_status': charts['by_status']},
                     'my_requests': _work_order_list(f, today, order='recent'),
                     'open_issues_by_facility': facility_kpis(site_f, today)['open_issues_by_facility']})
    return data
