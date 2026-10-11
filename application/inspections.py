"""
Inspection due/overdue bucketing and failed-item work-order generation.
Kept in a plain module, same reasoning as application/pm.py: no Flask
request-context assumptions, so tests can call it directly.
"""
from datetime import datetime, timedelta, timezone


def dashboard_buckets(inspections, today=None):
    """
    Split an iterable of still-Scheduled Inspection rows into the same four
    buckets as application/pm.py's dashboard_buckets(), computed purely from
    due_date — Overdue is never a stored flag, same reasoning as PM.
    """
    today = today or datetime.now(timezone.utc).date()
    week_end = today + timedelta(days=7)
    buckets = {'overdue': [], 'due_today': [], 'due_this_week': [], 'upcoming': []}
    for insp in inspections:
        if insp.due_date < today:
            buckets['overdue'].append(insp)
        elif insp.due_date == today:
            buckets['due_today'].append(insp)
        elif insp.due_date <= week_end:
            buckets['due_this_week'].append(insp)
        else:
            buckets['upcoming'].append(insp)
    return buckets


def generate_work_order_for_failures(inspection, user=None):
    """
    Create one combined WorkOrder (source=Inspection) covering every Fail
    result on `inspection`, using its template's category/priority. Returns
    the WorkOrder, or None if there are no Fail results. Does not commit —
    caller commits alongside the InspectionResult rows and the Completed
    status in the same transaction.
    """
    from main import db
    from application.models import WorkOrder
    from application import workflow

    failed = [r for r in inspection.results if r.result == 'Fail']
    if not failed:
        return None

    template = inspection.template
    lines = [f"- {r.item.question}" + (f" ({r.notes})" if r.notes else '') for r in failed]
    description = (
        f"Generated from a failed inspection: {template.name} — {inspection.target_label}.\n\n"
        f"Failed items:\n" + "\n".join(lines)
    )

    wo = WorkOrder(
        site_id=inspection.site_id,
        facility_id=inspection.facility_id or (inspection.room.facility_id if inspection.room_id else
                                                (inspection.asset.facility_id if inspection.asset_id else None)),
        room_id=inspection.room_id,
        asset_id=inspection.asset_id,
        title=f"Inspection Follow-up: {template.name} — {inspection.target_label}",
        description=description,
        source=workflow.SOURCE_INSPECTION,
        status=workflow.OPEN,
        priority_id=template.priority_id,
        category_id=template.category_id,
    )
    db.session.add(wo)
    db.session.flush()
    wo.assign_number()
    workflow.record_initial_status(wo, user)
    inspection.generated_work_order_id = wo.id
    return wo


def start_walkthrough(facility, cycle, template, user=None):
    """
    Create one Scheduled Inspection per active Room in `facility` for this
    (cycle, template) pair, skipping any room that already has one — lazy,
    idempotent creation, same precedent as pm.py's _ensure_schedules. Does
    not commit; caller commits. Returns the number of Inspections created.
    """
    from main import db
    from application.models import Room, Inspection

    existing_room_ids = {
        room_id for (room_id,) in db.session.query(Inspection.room_id)
        .join(Room, Inspection.room_id == Room.id)
        .filter(Room.facility_id == facility.id, Inspection.cycle_id == cycle.id,
                Inspection.template_id == template.id)
        .all()
    }
    due = cycle.end_date or datetime.now(timezone.utc).date()
    created = 0
    for room in Room.query.filter_by(facility_id=facility.id, is_active=True).all():
        if room.id in existing_room_ids:
            continue
        db.session.add(Inspection(
            template_id=template.id, cycle_id=cycle.id, site_id=room.site_id, room_id=room.id,
            due_date=due, status='Scheduled', created_by_id=user.id if user else None,
        ))
        created += 1
    return created


def pending_facility_ids(cycle_id, template_id, facility_ids):
    """
    Facility ids (within facility_ids) that still have at least one active
    room without a Completed Inspection for this exact (cycle, template)
    pair — i.e. still worth starting/resuming a walkthrough for.

    Deliberately template-scoped, unlike inspection_progress()'s cycle-only
    rollup used by the dashboard/facilities page: facility_inspections.html
    lets the user change the Checklist Template before picking a School/
    Building, and a facility fully audited under one template may still
    have every room pending under another — so "pending" has to be
    recomputed per (cycle, template) combo, not just per cycle.
    """
    from main import db
    from sqlalchemy import func
    from application.models import Room, Inspection

    if not facility_ids:
        return set()
    room_totals = dict(
        db.session.query(Room.facility_id, func.count(Room.id))
        .filter(Room.facility_id.in_(facility_ids), Room.is_active.is_(True))
        .group_by(Room.facility_id).all()
    )
    inspected = dict(
        db.session.query(Room.facility_id, func.count(Inspection.id))
        .select_from(Inspection).join(Room, Inspection.room_id == Room.id)
        .filter(Room.facility_id.in_(facility_ids), Inspection.cycle_id == cycle_id,
                Inspection.template_id == template_id, Inspection.status == 'Completed')
        .group_by(Room.facility_id).all()
    )
    return {fid for fid in facility_ids if room_totals.get(fid, 0) > 0 and inspected.get(fid, 0) < room_totals[fid]}


def facilities_ever_inspected_under_template(template_id, facility_ids):
    """
    Facility ids (within facility_ids) that have at least one Inspection
    ever created under this exact template — any cycle, any status, room-
    or facility-targeted. Not every template applies to every building
    (e.g. a Playground Safety checklist only makes sense at schools that
    actually have a playground), and there's no explicit template-to-
    facility scoping in the data model, so this uses "has this template
    been used here before" (via /add_inspection or a prior walkthrough) as
    the signal for "this template is relevant here" — see its use in
    start_walkthrough()'s pending_matrix.
    """
    from main import db
    from application.models import Inspection, Room

    if not facility_ids:
        return set()
    from_room = (
        db.session.query(Room.facility_id)
        .join(Inspection, Inspection.room_id == Room.id)
        .filter(Room.facility_id.in_(facility_ids), Inspection.template_id == template_id)
    )
    from_facility = (
        db.session.query(Inspection.facility_id)
        .filter(Inspection.facility_id.in_(facility_ids), Inspection.template_id == template_id)
    )
    return {fid for (fid,) in from_room.union(from_facility).all()}


def next_incomplete_room_inspection(inspection, site_ids=None):
    """
    Given a room-targeted Inspection created by a bulk walkthrough, find the
    next still-Scheduled inspection for another room — first in the same
    facility (so a walkthrough finishes one building before moving on), then,
    once that building is fully done, in any other facility still pending
    under the same cycle+template, so finishing one building rolls straight
    into the next one instead of dropping back to edit_facility.html.
    `site_ids` scopes that cross-facility fallback to what the current user
    may access (None = unrestricted, same convention as _visible_site_ids())
    — the same-facility check doesn't need it, since the caller already
    reached this inspection through can_access_inspection(). Returns None
    once every pending walkthrough this user can see is done.
    """
    if not inspection.room_id or not inspection.cycle_id:
        return None
    from main import db
    from application.models import Room, Floor, Inspection

    facility_id = inspection.room.facility_id
    base = (
        Inspection.query
        .join(Room, Inspection.room_id == Room.id)
        .outerjoin(Floor, Room.floor_id == Floor.id)
        .filter(
            Inspection.cycle_id == inspection.cycle_id,
            Inspection.template_id == inspection.template_id,
            Inspection.status == 'Scheduled',
            Inspection.id != inspection.id,
        )
    )
    same_facility = (
        base.filter(Room.facility_id == facility_id)
        .order_by(db.func.coalesce(Floor.sort_order, 0), Room.room_number)
        .first()
    )
    if same_facility:
        return same_facility

    other_facilities = base.filter(Room.facility_id != facility_id)
    if site_ids:
        other_facilities = other_facilities.filter(Inspection.site_id.in_(site_ids))
    return (
        other_facilities
        .order_by(Room.facility_id, db.func.coalesce(Floor.sort_order, 0), Room.room_number)
        .first()
    )


def inspection_progress(cycle_id, f=None, facility_ids=None):
    """
    Per-facility walkthrough completion for one InspectionCycle: rooms_total
    (active rooms in the facility), rooms_inspected (room-targeted
    Inspections in this cycle with status='Completed'), pct, issues_open
    (Fail or Needs Attention results on this cycle's inspections — N/A is
    excluded, same as Pass), critical_count (Fail results) — plus a
    district-level rollup. Mirrors analytics.
    facility_health_scores()'s one-GROUP-BY-per-factor shape; f is the same
    filter dict analytics.parse_filters() builds (site_ids/facility_id only).
    """
    from main import db
    from sqlalchemy import func, case
    from application.models import Facility, Room, Inspection, InspectionResult
    from application.analytics import pct

    fq = Facility.query.filter(Facility.is_active.is_(True))
    if facility_ids:
        fq = fq.filter(Facility.id.in_(facility_ids))
    else:
        f = f or {}
        if f.get('site_ids'):
            fq = fq.filter(Facility.site_id.in_(f['site_ids']))
        if f.get('facility_id'):
            fq = fq.filter(Facility.id == f['facility_id'])
    facilities = fq.order_by(Facility.name).all()
    empty_district = {'rooms_total': 0, 'rooms_inspected': 0, 'pct': None, 'issues_open': 0, 'critical_count': 0}
    if not facilities or not cycle_id:
        return {'facilities': [], 'district': empty_district,
                'completed_count': 0, 'not_started_count': 0, 'in_progress_count': 0}
    ids = [fac.id for fac in facilities]

    room_totals = dict(
        db.session.query(Room.facility_id, func.count(Room.id))
        .filter(Room.facility_id.in_(ids), Room.is_active.is_(True))
        .group_by(Room.facility_id).all()
    )

    inspected = dict(
        db.session.query(Room.facility_id, func.count(func.distinct(Room.id)))
        .select_from(Inspection).join(Room, Inspection.room_id == Room.id)
        .filter(Room.facility_id.in_(ids), Inspection.cycle_id == cycle_id, Inspection.status == 'Completed')
        .group_by(Room.facility_id).all()
    )

    issue_rows = (
        db.session.query(
            Room.facility_id,
            func.sum(case((InspectionResult.result.in_(('Fail', 'Needs Attention')), 1), else_=0)),
            func.sum(case((InspectionResult.result == 'Fail', 1), else_=0)),
        )
        .select_from(InspectionResult)
        .join(Inspection, InspectionResult.inspection_id == Inspection.id)
        .join(Room, Inspection.room_id == Room.id)
        .filter(Room.facility_id.in_(ids), Inspection.cycle_id == cycle_id)
        .group_by(Room.facility_id).all()
    )
    issues = {fid: (int(open_n or 0), int(crit_n or 0)) for fid, open_n, crit_n in issue_rows}

    rows = []
    for fac in facilities:
        total = room_totals.get(fac.id, 0)
        done = inspected.get(fac.id, 0)
        open_n, crit_n = issues.get(fac.id, (0, 0))
        rows.append({
            'facility': fac, 'rooms_total': total, 'rooms_inspected': done,
            'pct': pct(done, total), 'issues_open': open_n, 'critical_count': crit_n,
        })

    district = {
        'rooms_total': sum(r['rooms_total'] for r in rows),
        'rooms_inspected': sum(r['rooms_inspected'] for r in rows),
        'issues_open': sum(r['issues_open'] for r in rows),
        'critical_count': sum(r['critical_count'] for r in rows),
    }
    district['pct'] = pct(district['rooms_inspected'], district['rooms_total'])
    return {
        'facilities': rows,
        'district': district,
        'completed_count': sum(1 for r in rows if r['pct'] == 100.0),
        'not_started_count': sum(1 for r in rows if r['pct'] == 0.0),
        'in_progress_count': sum(1 for r in rows if r['pct'] is not None and 0 < r['pct'] < 100.0),
    }


def issue_resolution_health(f=None):
    """
    District-wide gauge (point-in-time, like inspection_progress()'s own
    gauge — no date-range scoping, since this describes the CURRENT state
    of every completed inspection's results, not activity in a period):
    100% minus every live, un-actioned inspection issue.

    A checklist result (N/A excluded, same convention as everywhere else)
    counts as "clean" if it's a Pass, OR if it's a Fail/Needs Attention
    whose inspection already has a generated work order that's since been
    resolved (status in workflow.TERMINAL_STATUSES). It counts against the
    score if it's a Fail/Needs Attention with no work order yet, or one
    that's still open — i.e. a real, found problem nobody's finished fixing.
    Needs Attention never auto-generates a work order (see
    generate_work_order_for_failures()), so those always count against the
    score until someone manually opens and resolves one.

    Any OTHER currently-open WorkOrder (one not already linked as some
    inspection's generated_work_order_id, e.g. a manual or requester-
    submitted ticket) also counts as a live issue — it's added to both
    `total` and `issues_outstanding` so it pulls the percentage down too,
    without double-counting work orders already represented via an
    inspection result above.
    """
    from main import db
    from sqlalchemy import case, func
    from application.models import Inspection, InspectionResult, WorkOrder
    from application.analytics import pct
    from application import workflow

    f = f or {}
    q = (
        db.session.query(
            func.count(InspectionResult.id),
            func.sum(case((InspectionResult.result == 'Pass', 1), else_=0)),
            func.sum(case(
                (db.and_(InspectionResult.result.in_(('Fail', 'Needs Attention')),
                          WorkOrder.status.in_(workflow.TERMINAL_STATUSES)), 1),
                else_=0,
            )),
        )
        .select_from(InspectionResult)
        .join(Inspection, InspectionResult.inspection_id == Inspection.id)
        .outerjoin(WorkOrder, Inspection.generated_work_order_id == WorkOrder.id)
        .filter(InspectionResult.result != 'N/A')
    )
    if f.get('site_ids'):
        q = q.filter(Inspection.site_id.in_(f['site_ids']))
    total, passed, resolved = q.one()
    total, passed, resolved = int(total or 0), int(passed or 0), int(resolved or 0)

    linked_wo_ids = db.session.query(Inspection.generated_work_order_id).filter(
        Inspection.generated_work_order_id.isnot(None))
    open_tickets_q = WorkOrder.query.filter(
        WorkOrder.status.notin_(workflow.TERMINAL_STATUSES),
        ~WorkOrder.id.in_(linked_wo_ids),
    )
    if f.get('site_ids'):
        open_tickets_q = open_tickets_q.filter(WorkOrder.site_id.in_(f['site_ids']))
    open_tickets = open_tickets_q.count()

    total += open_tickets
    clean = passed + resolved
    return {'pct': pct(clean, total), 'total': total, 'clean': clean,
            'issues_outstanding': total - clean, 'open_tickets': open_tickets}
