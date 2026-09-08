"""
Cost record maintenance, cost rollups, and technician workload calculation.
Kept in a plain module, same reasoning as application/pm.py,
application/inspections.py and application/vendors.py: no Flask
request-context assumptions, so the math is directly unit-testable.
"""
from datetime import datetime, timedelta, timezone


def refresh_cost_record(work_order):
    """
    Recompute labor_cost/material_cost/total_cost for one WorkOrder from its
    current WorkOrderLabor/WorkOrderMaterial entries, creating the CostRecord
    if it doesn't exist yet. vendor_cost/other_cost are left untouched (they
    have nothing to be summed from — they're entered directly). Does not
    commit; caller commits alongside whatever labor/material change triggered
    this refresh.
    """
    from main import db
    from application.models import CostRecord

    labor_cost = sum((l.labor_hours or 0) * (l.hourly_rate or 0) for l in work_order.labor_entries)
    material_cost = sum((m.quantity or 0) * (m.unit_cost or 0) for m in work_order.materials)

    record = work_order.cost_record
    if record is None:
        record = CostRecord(work_order_id=work_order.id)
        db.session.add(record)

    record.labor_cost = labor_cost
    record.material_cost = material_cost
    record.total_cost = labor_cost + material_cost + (record.vendor_cost or 0) + (record.other_cost or 0)
    record.updated_at = datetime.now(timezone.utc)
    return record


# Rollup dimensions from PROJECT_PLAN.md's Cost KPI list. "Site" stands in
# for the plan's "School" (this schema's Site model IS the school/campus);
# "team" stands in for "Department" — see the models.py docstring for why.
ROLLUP_DIMENSIONS = ('work_order', 'facility', 'site', 'asset', 'category', 'team', 'vendor', 'month', 'year')

ROLLUP_LABELS = {
    'work_order': 'Work Order', 'facility': 'Facility', 'site': 'School (Site)',
    'asset': 'Asset', 'category': 'Category', 'team': 'Department (Team)',
    'vendor': 'Vendor', 'month': 'Month', 'year': 'Year',
}


def _dimension_key(wo, dimension):
    if dimension == 'work_order':
        return wo.wo_number or f'WO #{wo.id}'
    if dimension == 'facility':
        return wo.facility.name if wo.facility else 'No Facility'
    if dimension == 'site':
        return wo.site.site_name if wo.site else 'No Site'
    if dimension == 'asset':
        return wo.asset.asset_tag if wo.asset else 'No Asset'
    if dimension == 'category':
        return wo.category.name if wo.category else 'No Category'
    if dimension == 'team':
        return wo.assigned_team or (wo.assigned_to.get_full_name() if wo.assigned_to else 'Unassigned')
    if dimension == 'vendor':
        return wo.vendor.name if wo.vendor else 'No Vendor'
    if dimension == 'month':
        return wo.created_at.strftime('%Y-%m') if wo.created_at else 'Unknown'
    if dimension == 'year':
        return wo.created_at.strftime('%Y') if wo.created_at else 'Unknown'
    raise ValueError(f'Unknown rollup dimension "{dimension}".')


def cost_rollup(work_orders, dimension):
    """
    Group work_orders by `dimension` (one of ROLLUP_DIMENSIONS), returning a
    list of {'label', 'count', 'estimated', 'actual'} dicts sorted by label.
    'actual' prefers the WorkOrder's CostRecord.total_cost (labor + material
    + vendor + other) and falls back to WorkOrder.actual_cost for work
    orders with no labor/material logged and no CostRecord yet.
    """
    if dimension not in ROLLUP_DIMENSIONS:
        raise ValueError(f'Unknown rollup dimension "{dimension}".')

    buckets = {}
    for wo in work_orders:
        key = _dimension_key(wo, dimension)
        bucket = buckets.setdefault(key, {'label': key, 'count': 0, 'estimated': 0, 'actual': 0})
        bucket['count'] += 1
        bucket['estimated'] += wo.estimated_cost or 0
        bucket['actual'] += wo.cost_record.total_cost if wo.cost_record else (wo.actual_cost or 0)
    return sorted(buckets.values(), key=lambda b: str(b['label']))


def technician_workload(technicians, work_orders_by_tech, today=None):
    """
    technicians: iterable of User. work_orders_by_tech: dict of
    technician id -> list of WorkOrder assigned to them. Returns a list of
    {'technician', 'open', 'in_progress', 'overdue', 'due_today',
    'due_this_week'} dicts, one per technician, in the given order.
    """
    from application import workflow

    today = today or datetime.now(timezone.utc).date()
    week_end = today + timedelta(days=7)
    rows = []
    for tech in technicians:
        wos = work_orders_by_tech.get(tech.id, [])
        open_wos = [w for w in wos if w.status in workflow.OPEN_STATUSES]
        rows.append({
            'technician': tech,
            'open': len(open_wos),
            'in_progress': sum(1 for w in wos if w.status == workflow.IN_PROGRESS),
            'overdue': sum(1 for w in open_wos if w.due_date and w.due_date < today),
            'due_today': sum(1 for w in open_wos if w.due_date == today),
            'due_this_week': sum(1 for w in open_wos if w.due_date and today < w.due_date <= week_end),
        })
    return rows
