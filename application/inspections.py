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
