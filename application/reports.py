"""
Reports & Exports (Phase 10).

The report list, and every KPI/metric inside each report, comes from
PROJECT_PLAN.md's Canonical KPI List and Scored/Calculated Features section —
this module doesn't redefine anything Phase 6/7/8/9 already defined, it reuses
those modules' functions (vendors.compute_vendor_stats, risk.calculate_risk,
analytics.facility_health/detect_recurring_issues, costs.cost_rollup) and
adds a thin, tabular, CSV-exportable view over them.

Every report is one entry in REPORTS: a title, a group, the column this
report's date filter applies to (for the UI hint), which of the common
filter widgets are relevant, and a `rows(filters)` function returning
(headers, rows) — rows are lists of plain values (str/int/float/date/None),
so the exact same data renders in the HTML table and the CSV file. There is
one source of truth per report, never a separate "display version."

Per PROJECT_PLAN.md's performance baseline, every builder queries with
filters applied in SQL (never "load everything, filter in Python") and caps
its result at MAX_EXPORT_ROWS, flagging truncation rather than silently
dropping rows or loading an unbounded set.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from application import analytics, workflow

MAX_EXPORT_ROWS = 20000
DISPLAY_ROWS = 200
HISTORY_SCAN_LIMIT = 5000

PRESETS = ('week', 'month', 'quarter', 'year', 'all', 'custom')
PRESET_LABELS = {'week': 'This Week', 'month': 'This Month', 'quarter': 'This Quarter',
                 'year': 'This Year', 'all': 'All Time', 'custom': 'Custom Range'}


def _today():
    return datetime.now(timezone.utc).date()


def parse_filters(args, visible_site_ids, default_preset='quarter', today=None):
    """Filter dict shared by every report. A superset of analytics.parse_filters'
    shape (site_ids/facility_id/team/technician_id/category_id/priority_id/
    status/start/end) plus vendor_id/asset_id, which analytics ignores
    harmlessly — so facility-condition and recurring-problems reports can
    hand this dict straight to analytics' functions."""
    today = today or _today()
    preset = args.get('preset') or default_preset
    if preset not in PRESETS:
        preset = default_preset
    if preset == 'all':
        start, end = None, None
    else:
        start, end, preset = analytics.date_range(preset, today, analytics.parse_date(args.get('start')),
                                                   analytics.parse_date(args.get('end')))

    def _int(name):
        value = args.get(name, type=int)
        return value if value else None

    site_id = _int('site_id') if visible_site_ids is None else None
    status = args.get('status') or None
    if status not in workflow.ALL_STATUSES:
        status = None

    return {
        'preset': preset, 'start': start, 'end': end,
        'period_label': 'all time' if preset == 'all' else PRESET_LABELS[preset].lower(),
        'site_id': site_id,
        'site_ids': visible_site_ids if visible_site_ids is not None else ([site_id] if site_id else None),
        'facility_id': _int('facility_id'),
        'team': None,
        'technician_id': _int('technician_id'),
        'category_id': _int('category_id'),
        'priority_id': _int('priority_id'),
        'vendor_id': _int('vendor_id'),
        'asset_id': _int('asset_id'),
        'status': status,
        'requester_id': None,
        'dimension': args.get('dimension') or 'facility',
    }


def _dt_bounds(f):
    if not (f.get('start') and f.get('end')):
        return None, None
    return (datetime.combine(f['start'], datetime.min.time()),
            datetime.combine(f['end'] + timedelta(days=1), datetime.min.time()))


def _fmt(value):
    """One formatting rule for every cell, so the HTML table and the CSV
    file always agree: Decimal/float -> 2dp number, date/datetime -> ISO
    date, None -> ''."""
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return float(round(value, 2))
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, date):
        return value.isoformat()
    return value


def _row(*values):
    return [_fmt(v) for v in values]


def _cap(rows):
    if len(rows) > MAX_EXPORT_ROWS:
        return rows[:MAX_EXPORT_ROWS], True
    return rows, False


# ---------------------------------------------------------------------------
# Work Order family (Work Order / Open Work Order / Overdue Work Order)
# ---------------------------------------------------------------------------

def _wo_query(f, date_column=None):
    from application.models import WorkOrder
    q = WorkOrder.query
    clauses = []
    if f.get('site_ids'):
        clauses.append(WorkOrder.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        clauses.append(WorkOrder.facility_id == f['facility_id'])
    if f.get('technician_id'):
        clauses.append(WorkOrder.assigned_to_id == f['technician_id'])
    if f.get('vendor_id'):
        clauses.append(WorkOrder.vendor_id == f['vendor_id'])
    if f.get('category_id'):
        clauses.append(WorkOrder.category_id == f['category_id'])
    if f.get('priority_id'):
        clauses.append(WorkOrder.priority_id == f['priority_id'])
    if date_column is not None:
        start_dt, end_dt = _dt_bounds(f)
        if start_dt is not None:
            clauses.extend([date_column >= start_dt, date_column < end_dt])
    return q.filter(*clauses) if clauses else q


def _wo_eager(q):
    from main import db
    from application.models import WorkOrder
    return q.options(db.joinedload(WorkOrder.site), db.joinedload(WorkOrder.facility), db.joinedload(WorkOrder.room),
                     db.joinedload(WorkOrder.asset), db.joinedload(WorkOrder.category), db.joinedload(WorkOrder.priority),
                     db.joinedload(WorkOrder.assigned_to), db.joinedload(WorkOrder.requester), db.joinedload(WorkOrder.vendor),
                     db.joinedload(WorkOrder.cost_record))


WO_HEADERS = ['WO Number', 'Title', 'Site', 'Facility', 'Room', 'Asset', 'Category', 'Priority', 'Status',
             'Source', 'Requester', 'Assigned To', 'Created', 'Due Date', 'Completed', 'Estimated Cost', 'Actual Cost']


def _wo_row(wo):
    actual = wo.cost_record.total_cost if wo.cost_record else wo.actual_cost
    return _row(wo.wo_number, wo.title, wo.site.site_name if wo.site else None,
               wo.facility.name if wo.facility else None, wo.room.room_number if wo.room else None,
               wo.asset.asset_tag if wo.asset else None, wo.category.name if wo.category else None,
               wo.priority.name if wo.priority else None, wo.status, wo.source,
               wo.requester.get_full_name() if wo.requester else None,
               wo.assigned_to.get_full_name() if wo.assigned_to else None,
               wo.created_at, wo.due_date, wo.completed_at, wo.estimated_cost, actual)


def report_work_orders(f):
    from application.models import WorkOrder
    q = _wo_eager(_wo_query(f, WorkOrder.created_at)).order_by(WorkOrder.created_at.desc())
    rows, truncated = _cap(q.limit(MAX_EXPORT_ROWS + 1).all())
    return WO_HEADERS, [_wo_row(w) for w in rows], truncated


def report_open_work_orders(f):
    from application.models import WorkOrder
    q = _wo_eager(_wo_query(f, WorkOrder.created_at).filter(WorkOrder.status.in_(workflow.OPEN_STATUSES))) \
        .order_by(WorkOrder.due_date.is_(None), WorkOrder.due_date)
    rows, truncated = _cap(q.limit(MAX_EXPORT_ROWS + 1).all())
    return WO_HEADERS, [_wo_row(w) for w in rows], truncated


def report_overdue_work_orders(f, today=None):
    from application.models import WorkOrder
    today = today or _today()
    q = _wo_eager(_wo_query(f, WorkOrder.created_at).filter(
        WorkOrder.status.in_(workflow.OPEN_STATUSES), WorkOrder.due_date.isnot(None), WorkOrder.due_date < today,
    )).order_by(WorkOrder.due_date)
    rows, truncated = _cap(q.limit(MAX_EXPORT_ROWS + 1).all())
    return WO_HEADERS, [_wo_row(w) for w in rows], truncated


# ---------------------------------------------------------------------------
# Preventive Maintenance
# ---------------------------------------------------------------------------

def report_preventive_maintenance(f, today=None):
    from main import db
    from application.models import MaintenanceSchedule, MaintenancePlan, Asset

    today = today or _today()
    q = MaintenanceSchedule.query.join(MaintenancePlan, MaintenanceSchedule.maintenance_plan_id == MaintenancePlan.id) \
        .join(Asset, MaintenanceSchedule.asset_id == Asset.id) \
        .options(db.joinedload(MaintenanceSchedule.asset), db.joinedload(MaintenanceSchedule.plan),
                db.joinedload(MaintenanceSchedule.last_work_order)) \
        .filter(MaintenancePlan.is_active.is_(True), Asset.is_active.is_(True))
    if f.get('site_ids'):
        q = q.filter(Asset.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        q = q.filter(Asset.facility_id == f['facility_id'])
    start_dt, end_dt = _dt_bounds(f)
    if start_dt is not None:
        q = q.filter(MaintenanceSchedule.next_due_date >= start_dt.date(), MaintenanceSchedule.next_due_date < end_dt.date())
    q = q.order_by(MaintenanceSchedule.next_due_date)

    headers = ['Asset Tag', 'Asset Name', 'Facility', 'Plan', 'Frequency', 'Next Due', 'Status', 'Last Generated', 'Last Work Order']
    rows = []
    for s in q.limit(MAX_EXPORT_ROWS + 1).all():
        bucket = 'Overdue' if s.next_due_date < today else 'Due Today' if s.next_due_date == today \
            else 'Due This Week' if s.next_due_date <= today + timedelta(days=7) else 'Upcoming'
        rows.append(_row(s.asset.asset_tag, s.asset.name, s.asset.facility.name if s.asset.facility else None,
                         s.plan.target_label if s.plan else None, s.plan.frequency if s.plan else None,
                         s.next_due_date, bucket, s.last_generated_at,
                         s.last_work_order.wo_number if s.last_work_order else None))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Asset Condition
# ---------------------------------------------------------------------------

def report_asset_condition(f, today=None):
    from main import db
    from sqlalchemy import func
    from application.models import Asset, AssetType, AssetConditionHistory

    today = today or _today()
    latest = db.session.query(AssetConditionHistory.asset_id, func.max(AssetConditionHistory.assessed_at).label('latest')) \
        .group_by(AssetConditionHistory.asset_id).subquery()
    q = db.session.query(Asset, latest.c.latest).join(AssetType, Asset.asset_type_id == AssetType.id) \
        .outerjoin(latest, Asset.id == latest.c.asset_id) \
        .options(db.joinedload(Asset.asset_type), db.joinedload(Asset.facility), db.joinedload(Asset.room)) \
        .filter(Asset.is_active.is_(True))
    if f.get('site_ids'):
        q = q.filter(Asset.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        q = q.filter(Asset.facility_id == f['facility_id'])
    if f.get('start') and f.get('end'):
        q = q.filter(latest.c.latest.isnot(None), latest.c.latest >= f['start'], latest.c.latest <= f['end'])
    q = q.order_by(Asset.condition_score.is_(None), Asset.condition_score)

    headers = ['Asset Tag', 'Name', 'Type', 'Facility', 'Room', 'Condition Score', 'Condition',
              'Last Assessed', 'Install Date', 'Expected Life (yrs)', 'Warranty Expiration']
    rows = []
    for asset, last_assessed in q.limit(MAX_EXPORT_ROWS + 1).all():
        rows.append(_row(asset.asset_tag, asset.name, asset.asset_type.name if asset.asset_type else None,
                         asset.facility.name if asset.facility else None, asset.room.room_number if asset.room else None,
                         asset.condition_score, asset.condition_label, last_assessed, asset.install_date,
                         asset.expected_life_years, asset.warranty_expiration))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Facility Condition (Facility Health Score)
# ---------------------------------------------------------------------------

def report_facility_condition(f, today=None):
    today = today or _today()
    groups = analytics.detect_recurring_issues(analytics.recurring_rows(f)) if f.get('start') else \
        analytics.detect_recurring_issues(analytics.recurring_rows({**f, 'start': today - timedelta(days=90), 'end': today}))
    scored = analytics.facility_health_scores(f if f.get('start') else {**f, 'start': today - timedelta(days=90), 'end': today},
                                              groups, today=today)
    headers = ['Facility', 'Site', 'Health Score', 'Factors With Data'] + list(analytics.HEALTH_FACTORS)
    rows = []
    for r in scored:
        h = r['health']
        rows.append(_row(r['facility'].name, r['facility'].site.site_name if r['facility'].site else None,
                         h['score'], h['factor_count'],
                         *[h['factors'][name]['value'] for name in analytics.HEALTH_FACTORS]))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Maintenance Cost
# ---------------------------------------------------------------------------

def report_maintenance_cost(f):
    from application import costs as costs_module
    from application.models import WorkOrder

    dimension = f.get('dimension') if f.get('dimension') in costs_module.ROLLUP_DIMENSIONS else 'facility'
    q = _wo_eager(_wo_query(f, WorkOrder.created_at))
    grouped = costs_module.cost_rollup(q.limit(MAX_EXPORT_ROWS + 1).all(), dimension)
    headers = [costs_module.ROLLUP_LABELS[dimension], 'Work Order Count', 'Estimated Cost', 'Actual Cost']
    rows = [_row(g['label'], g['count'], g['estimated'], g['actual']) for g in grouped]
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Technician Productivity
# ---------------------------------------------------------------------------

def report_technician_productivity(f, today=None):
    from main import db
    from sqlalchemy import func
    from application.models import User, WorkOrder, WorkOrderLabor

    today = today or _today()
    tech_q = User.query.filter(User.role_id.in_((2, 3)), User.status == 'Active')
    if f.get('site_ids'):
        tech_q = tech_q.filter(User.site_id.in_(f['site_ids']))
    if f.get('technician_id'):
        tech_q = tech_q.filter(User.id == f['technician_id'])
    technicians = tech_q.order_by(User.first_name, User.last_name).all()
    if not technicians:
        return ['Technician', 'Site', 'Open Assigned', 'Completed', 'Avg Resolution (days)',
                'Work Orders Touched', 'Labor Hours', 'Labor Cost'], [], False
    ids = [t.id for t in technicians]

    open_counts = dict(db.session.query(WorkOrder.assigned_to_id, func.count(WorkOrder.id))
                       .filter(WorkOrder.assigned_to_id.in_(ids), WorkOrder.status.in_(workflow.OPEN_STATUSES))
                       .group_by(WorkOrder.assigned_to_id).all())

    completed_q = db.session.query(WorkOrder.assigned_to_id, func.count(WorkOrder.id),
                                   func.avg(analytics.days_between(WorkOrder.completed_at, WorkOrder.created_at))) \
        .filter(WorkOrder.assigned_to_id.in_(ids), WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED)))
    start_dt, end_dt = _dt_bounds(f)
    if start_dt is not None:
        completed_q = completed_q.filter(WorkOrder.completed_at >= start_dt, WorkOrder.completed_at < end_dt)
    completed_stats = {tid: (int(n), analytics.num(avg, 1)) for tid, n, avg in completed_q.group_by(WorkOrder.assigned_to_id).all()}

    labor_q = db.session.query(WorkOrderLabor.technician_id, func.sum(WorkOrderLabor.labor_hours),
                               func.sum(WorkOrderLabor.labor_hours * func.coalesce(WorkOrderLabor.hourly_rate, 0)),
                               func.count(func.distinct(WorkOrderLabor.work_order_id))) \
        .filter(WorkOrderLabor.technician_id.in_(ids))
    if start_dt is not None:
        labor_q = labor_q.filter(WorkOrderLabor.created_at >= start_dt, WorkOrderLabor.created_at < end_dt)
    labor_stats = {tid: (analytics.num(hrs, 1) or 0.0, analytics.num(cost, 2) or 0.0, int(n))
                  for tid, hrs, cost, n in labor_q.group_by(WorkOrderLabor.technician_id).all()}

    headers = ['Technician', 'Site', 'Open Assigned', 'Completed', 'Avg Resolution (days)',
              'Work Orders Touched', 'Labor Hours', 'Labor Cost']
    rows = []
    for t in technicians:
        completed, avg_res = completed_stats.get(t.id, (0, None))
        hours, cost, touched = labor_stats.get(t.id, (0.0, 0.0, 0))
        rows.append(_row(t.get_full_name(), t.site.site_name if t.site else None, open_counts.get(t.id, 0),
                         completed, avg_res, touched, hours, cost))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Vendor Performance
# ---------------------------------------------------------------------------

def report_vendor_performance(f):
    from application import vendors as vendors_module
    from application.models import Vendor, WorkOrder

    vq = Vendor.query.filter_by(is_active=True)
    if f.get('vendor_id'):
        vq = vq.filter(Vendor.id == f['vendor_id'])
    vendors = vq.order_by(Vendor.name).all()

    headers = ['Vendor', 'Contact', 'Total Work Orders', 'Open', 'Total Cost', 'Avg Completion (days)',
              'Contract Status', 'Insurance Status', 'License Status']
    rows = []
    for vendor in vendors:
        q = _wo_query(f, WorkOrder.created_at).filter(WorkOrder.vendor_id == vendor.id)
        stats = vendors_module.compute_vendor_stats(q.all())
        rows.append(_row(vendor.name, vendor.contact_name, stats['total_count'], stats['open_count'],
                         stats['total_cost'], stats['avg_completion_days'],
                         vendors_module.expiration_status(vendor.contract_end_date) or 'n/a',
                         vendors_module.expiration_status(vendor.insurance_expiration) or 'n/a',
                         vendors_module.expiration_status(vendor.license_expiration) or 'n/a'))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Asset Maintenance History
# ---------------------------------------------------------------------------

def report_asset_maintenance_history(f):
    from main import db
    from application.models import Asset, AssetConditionHistory, WorkOrder

    hist_q = AssetConditionHistory.query.join(Asset, AssetConditionHistory.asset_id == Asset.id) \
        .options(db.joinedload(AssetConditionHistory.inspector))
    wo_q = WorkOrder.query.filter(WorkOrder.asset_id.isnot(None)) \
        .options(db.joinedload(WorkOrder.asset), db.joinedload(WorkOrder.assigned_to), db.joinedload(WorkOrder.cost_record))
    if f.get('site_ids'):
        hist_q = hist_q.filter(Asset.site_id.in_(f['site_ids']))
        wo_q = wo_q.filter(WorkOrder.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        hist_q = hist_q.filter(Asset.facility_id == f['facility_id'])
        wo_q = wo_q.filter(WorkOrder.facility_id == f['facility_id'])
    if f.get('asset_id'):
        hist_q = hist_q.filter(AssetConditionHistory.asset_id == f['asset_id'])
        wo_q = wo_q.filter(WorkOrder.asset_id == f['asset_id'])
    start_dt, end_dt = _dt_bounds(f)
    if start_dt is not None:
        hist_q = hist_q.filter(AssetConditionHistory.assessed_at >= start_dt.date(), AssetConditionHistory.assessed_at < end_dt.date())
        wo_q = wo_q.filter(WorkOrder.created_at >= start_dt, WorkOrder.created_at < end_dt)

    headers = ['Date', 'Type', 'Asset Tag', 'Facility', 'Detail', 'By / Assigned To', 'Cost']
    combined = []
    for h in hist_q.order_by(AssetConditionHistory.assessed_at.desc()).limit(HISTORY_SCAN_LIMIT).all():
        combined.append((datetime.combine(h.assessed_at, datetime.min.time()), _row(
            h.assessed_at, 'Condition Assessment', h.asset.asset_tag, h.asset.facility.name if h.asset.facility else None,
            f'{h.condition} ({h.score}) — {h.reason or ""}'.strip(' —'), h.inspector.get_full_name() if h.inspector else None, None)))
    for w in wo_q.order_by(WorkOrder.created_at.desc()).limit(HISTORY_SCAN_LIMIT).all():
        actual = w.cost_record.total_cost if w.cost_record else w.actual_cost
        combined.append((w.created_at, _row(
            w.created_at, 'Work Order', w.asset.asset_tag, w.asset.facility.name if w.asset.facility else None,
            f'{w.wo_number} — {w.title} ({w.status})', w.assigned_to.get_full_name() if w.assigned_to else None, actual)))
    combined.sort(key=lambda t: t[0], reverse=True)
    rows, truncated = _cap([r for _, r in combined])
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Capital Replacement
# ---------------------------------------------------------------------------

def report_capital_replacement(f, today=None):
    from main import db
    from sqlalchemy import func
    from application import risk
    from application.models import Asset, WorkOrder

    today = today or _today()
    factor_names = ('Condition', 'Age', 'Failure Frequency', 'Maintenance Cost', 'Safety Impact', 'Operational Importance', 'Warranty Status')
    headers = ['Asset Tag', 'Name', 'Type', 'Facility', 'Risk Score', 'Factors With Data'] + list(factor_names)

    aq = Asset.query.filter(Asset.is_active.is_(True)).options(db.joinedload(Asset.asset_type), db.joinedload(Asset.facility))
    if f.get('site_ids'):
        aq = aq.filter(Asset.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        aq = aq.filter(Asset.facility_id == f['facility_id'])
    assets = aq.all()
    if not assets:
        return headers, [], False

    ids = [a.id for a in assets]
    wo_q = WorkOrder.query.filter(WorkOrder.asset_id.in_(ids)).options(db.joinedload(WorkOrder.cost_record))
    start_dt, end_dt = _dt_bounds(f)
    if start_dt is not None:
        wo_q = wo_q.filter(WorkOrder.created_at >= start_dt, WorkOrder.created_at < end_dt)
    by_asset = {}
    for w in wo_q.all():
        by_asset.setdefault(w.asset_id, []).append(w)

    rows = []
    for asset in assets:
        result = risk.calculate_risk(asset, work_orders=by_asset.get(asset.id, []), today=today)
        rows.append(_row(asset.asset_tag, asset.name, asset.asset_type.name if asset.asset_type else None,
                         asset.facility.name if asset.facility else None, result['score'], result['factor_count'],
                         *[result['factors'][name]['value'] for name in factor_names]))
    rows.sort(key=lambda r: (r[4] is None or r[4] == '', -(r[4] if isinstance(r[4], (int, float)) else 0)))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# SLA Performance
# ---------------------------------------------------------------------------

def report_sla_performance(f):
    """
    Per priority: work orders completed in the period that had a due date,
    and how many were completed on or before it — the same due-date-based
    definition Phase 9's work_order_kpis() uses for SLA Compliance %,
    broken out per priority instead of aggregated. A real per-priority SLA
    target model doesn't exist yet (Phase 11's scope); this is the
    placeholder PROJECT_PLAN.md's KPI list calls "SLA Compliance %."
    """
    from main import db
    from sqlalchemy import func, case
    from application.models import WorkOrder, Priority

    per_priority = db.session.query(
        Priority.name, func.count(WorkOrder.id),
        func.coalesce(func.sum(case((func.date(WorkOrder.completed_at) <= WorkOrder.due_date, 1), else_=0)), 0),
    ).select_from(WorkOrder).join(Priority, WorkOrder.priority_id == Priority.id)
    per_priority = _apply_wo_clauses(per_priority, f, WorkOrder.completed_at)
    per_priority = per_priority.filter(WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED)), WorkOrder.due_date.isnot(None)) \
        .group_by(Priority.id, Priority.name, Priority.sort_order).order_by(Priority.sort_order)

    headers = ['Priority', 'Completed (with due date)', 'Met Due Date', 'SLA Compliance %']
    rows = []
    total_completed = total_met = 0
    for name, completed, met in per_priority.all():
        completed, met = int(completed), int(met)
        total_completed += completed
        total_met += met
        rows.append(_row(name, completed, met, analytics.pct(met, completed)))
    if rows:
        rows.append(_row('All Priorities', total_completed, total_met, analytics.pct(total_met, total_completed)))
    rows, truncated = _cap(rows)
    return headers, rows, truncated


def _apply_wo_clauses(query, f, date_column):
    from application.models import WorkOrder
    if f.get('site_ids'):
        query = query.filter(WorkOrder.site_id.in_(f['site_ids']))
    if f.get('facility_id'):
        query = query.filter(WorkOrder.facility_id == f['facility_id'])
    if f.get('technician_id'):
        query = query.filter(WorkOrder.assigned_to_id == f['technician_id'])
    if f.get('vendor_id'):
        query = query.filter(WorkOrder.vendor_id == f['vendor_id'])
    if f.get('category_id'):
        query = query.filter(WorkOrder.category_id == f['category_id'])
    if f.get('priority_id'):
        query = query.filter(WorkOrder.priority_id == f['priority_id'])
    start_dt, end_dt = _dt_bounds(f)
    if start_dt is not None:
        query = query.filter(date_column >= start_dt, date_column < end_dt)
    return query


# ---------------------------------------------------------------------------
# Recurring Problems
# ---------------------------------------------------------------------------

def report_recurring_problems(f):
    groups = analytics.label_recurring_groups(analytics.detect_recurring_issues(analytics.recurring_rows(f)))
    headers = ['Kind', 'Issue', 'Count', 'Latest Occurrence', 'Work Orders']
    rows = [_row(g['kind'], g['label'], g['count'], g['latest'],
                '; '.join(number for _, number, _ in g['work_orders'])) for g in groups]
    rows, truncated = _cap(rows)
    return headers, rows, truncated


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REPORTS = {
    'work_orders': {'title': 'Work Order Report', 'group': 'Work Orders', 'date_column': 'Created',
                    'filters': ('facility', 'technician', 'vendor', 'category', 'priority'),
                    'default_preset': 'quarter', 'rows': lambda f: report_work_orders(f)},
    'open_work_orders': {'title': 'Open Work Order Report', 'group': 'Work Orders', 'date_column': 'Created',
                         'filters': ('facility', 'technician', 'vendor', 'category', 'priority'),
                         'default_preset': 'all', 'rows': lambda f: report_open_work_orders(f)},
    'overdue_work_orders': {'title': 'Overdue Work Order Report', 'group': 'Work Orders', 'date_column': 'Created',
                            'filters': ('facility', 'technician', 'vendor', 'category', 'priority'),
                            'default_preset': 'all', 'rows': lambda f: report_overdue_work_orders(f)},
    'preventive_maintenance': {'title': 'Preventive Maintenance Report', 'group': 'Maintenance', 'date_column': 'Next Due',
                               'filters': ('facility',), 'default_preset': 'all',
                               'rows': lambda f: report_preventive_maintenance(f)},
    'asset_condition': {'title': 'Asset Condition Report', 'group': 'Assets', 'date_column': 'Last Assessed',
                        'filters': ('facility',), 'default_preset': 'all', 'rows': lambda f: report_asset_condition(f)},
    'facility_condition': {'title': 'Facility Condition Report', 'group': 'Assets', 'date_column': 'Period (health factors)',
                           'filters': ('facility',), 'default_preset': 'quarter', 'rows': lambda f: report_facility_condition(f)},
    'maintenance_cost': {'title': 'Maintenance Cost Report', 'group': 'Cost', 'date_column': 'Created',
                         'filters': ('facility', 'technician', 'vendor', 'category', 'priority', 'dimension'),
                         'default_preset': 'quarter', 'rows': lambda f: report_maintenance_cost(f)},
    'technician_productivity': {'title': 'Technician Productivity Report', 'group': 'Cost', 'date_column': 'Completed / Labor entry',
                                'filters': ('technician',), 'default_preset': 'quarter',
                                'rows': lambda f: report_technician_productivity(f)},
    'vendor_performance': {'title': 'Vendor Performance Report', 'group': 'Cost', 'date_column': 'Created',
                           'filters': ('facility', 'vendor'), 'default_preset': 'quarter',
                           'rows': lambda f: report_vendor_performance(f)},
    'asset_maintenance_history': {'title': 'Asset Maintenance History Report', 'group': 'Assets', 'date_column': 'Assessed / Created',
                                  'filters': ('facility', 'asset'), 'default_preset': 'year',
                                  'rows': lambda f: report_asset_maintenance_history(f)},
    'capital_replacement': {'title': 'Capital Replacement Report', 'group': 'Assets', 'date_column': 'Work order history window',
                            'filters': ('facility',), 'default_preset': 'all', 'rows': lambda f: report_capital_replacement(f)},
    'sla_performance': {'title': 'SLA Performance Report', 'group': 'Work Orders', 'date_column': 'Completed',
                        'filters': ('facility', 'technician', 'vendor', 'category', 'priority'),
                        'default_preset': 'quarter', 'rows': lambda f: report_sla_performance(f)},
    'recurring_problems': {'title': 'Recurring Problems Report', 'group': 'Maintenance', 'date_column': 'Created',
                           'filters': ('facility', 'technician', 'category', 'priority'),
                           'default_preset': 'quarter', 'rows': lambda f: report_recurring_problems(f)},
}

REPORT_ORDER = ['work_orders', 'open_work_orders', 'overdue_work_orders', 'preventive_maintenance',
               'asset_condition', 'facility_condition', 'maintenance_cost', 'technician_productivity',
               'vendor_performance', 'asset_maintenance_history', 'capital_replacement',
               'sla_performance', 'recurring_problems']


def to_csv(headers, rows):
    import csv, io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()
