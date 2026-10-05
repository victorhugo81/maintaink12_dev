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
        status=workflow.NEW,
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


def next_incomplete_room_inspection(inspection):
    """
    Given a room-targeted Inspection created by a bulk walkthrough, find the
    next still-Scheduled inspection for another room in the same facility,
    cycle and template — ordered by floor sort order then room number.
    Returns None once the walkthrough is finished (or this wasn't a
    walkthrough inspection to begin with).
    """
    if not inspection.room_id or not inspection.cycle_id:
        return None
    from main import db
    from application.models import Room, Floor, Inspection

    facility_id = inspection.room.facility_id
    return (
        Inspection.query
        .join(Room, Inspection.room_id == Room.id)
        .outerjoin(Floor, Room.floor_id == Floor.id)
        .filter(
            Inspection.cycle_id == inspection.cycle_id,
            Inspection.template_id == inspection.template_id,
            Room.facility_id == facility_id,
            Inspection.status == 'Scheduled',
            Inspection.id != inspection.id,
        )
        .order_by(db.func.coalesce(Floor.sort_order, 0), Room.room_number)
        .first()
    )


def inspection_progress(cycle_id, f=None, facility_ids=None):
    """
    Per-facility walkthrough completion for one InspectionCycle: rooms_total
    (active rooms in the facility), rooms_inspected (room-targeted
    Inspections in this cycle with status='Completed'), pct, issues_open
    (non-Pass results on this cycle's inspections), critical_count (Fail
    results) — plus a district-level rollup. Mirrors analytics.
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
        db.session.query(Room.facility_id, func.count(Inspection.id))
        .select_from(Inspection).join(Room, Inspection.room_id == Room.id)
        .filter(Room.facility_id.in_(ids), Inspection.cycle_id == cycle_id, Inspection.status == 'Completed')
        .group_by(Room.facility_id).all()
    )

    issue_rows = (
        db.session.query(
            Room.facility_id,
            func.sum(case((InspectionResult.result != 'Pass', 1), else_=0)),
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
