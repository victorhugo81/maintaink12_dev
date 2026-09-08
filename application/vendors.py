"""
Vendor performance aggregation and contract/insurance/license expiration
status. Kept in a plain module, same reasoning as application/pm.py and
application/inspections.py: no Flask request-context assumptions, so the
math is directly unit-testable.
"""
from datetime import datetime, timedelta, timezone

EXPIRING_SOON_DAYS = 30


def expiration_status(exp_date, today=None):
    """Return 'expired' | 'expiring_soon' | 'ok' | None (no date set)."""
    if exp_date is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    if exp_date < today:
        return 'expired'
    if exp_date <= today + timedelta(days=EXPIRING_SOON_DAYS):
        return 'expiring_soon'
    return 'ok'


def compute_vendor_stats(work_orders):
    """
    Aggregate performance stats for one vendor from its linked WorkOrders.
    avg_completion_days is None when none of the work orders has been
    completed yet (nothing to average).
    """
    from application import workflow

    total_count = len(work_orders)
    open_count = sum(1 for wo in work_orders if wo.status in workflow.OPEN_STATUSES)
    total_cost = sum((wo.actual_cost or 0) for wo in work_orders)
    completion_days = [
        (wo.completed_at - wo.created_at).total_seconds() / 86400
        for wo in work_orders if wo.completed_at
    ]
    avg_completion_days = round(sum(completion_days) / len(completion_days), 1) if completion_days else None

    return {
        'total_count': total_count,
        'open_count': open_count,
        'total_cost': total_cost,
        'avg_completion_days': avg_completion_days,
    }
