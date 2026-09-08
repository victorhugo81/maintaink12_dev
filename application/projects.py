"""
Project cost roll-up: direct ProjectCost line items plus every linked
WorkOrder's cost (CostRecord.total_cost, falling back to WorkOrder.actual_cost
for work orders with nothing logged yet — same fallback as
application/costs.py's cost_rollup, for the same reason).
"""


def project_cost_rollup(project):
    """
    Returns {'direct_cost', 'work_order_cost', 'total_cost', 'work_order_count',
    'estimated_cost'} for one Project. estimated_cost sums the linked work
    orders' WorkOrder.estimated_cost, for an Estimated-vs-Actual comparison
    at the project level (direct ProjectCost entries have no "estimated" side
    — they're already-incurred spend, not a plan).
    """
    direct_cost = sum((c.amount or 0) for c in project.costs)

    work_order_cost = 0
    estimated_cost = 0
    for wo in project.work_orders:
        work_order_cost += wo.cost_record.total_cost if wo.cost_record else (wo.actual_cost or 0)
        estimated_cost += wo.estimated_cost or 0

    return {
        'direct_cost': direct_cost,
        'work_order_cost': work_order_cost,
        'total_cost': direct_cost + work_order_cost,
        'work_order_count': len(project.work_orders),
        'estimated_cost': estimated_cost,
    }
