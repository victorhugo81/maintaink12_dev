"""
Preventive maintenance schedule generation — the business logic behind the
scheduled job in application/scheduled_jobs.py. Kept in a plain module (no
Flask request context assumptions, just an app context) so tests and an
admin "run now" action can call it directly without going through
APScheduler.

Duplicate-prevention: generate_due_work_orders() advances a schedule's
next_due_date past `today` in the SAME transaction that creates the work
order, so calling it again the same day (a re-run, a second cron fire after
a restart) sees next_due_date already in the future and does nothing for
that schedule. There is deliberately no separate "already generated today"
flag to check — the advanced date IS that guard.

Overdue catch-up: if a schedule's next_due_date is more than one cycle in
the past (the job didn't run for a while), exactly ONE work order is still
generated, and next_due_date is advanced cycle-by-cycle from its original
value until it lands in the future — this fast-forwards the cadence without
generating a backlog of one work order per missed cycle.
"""
import calendar
from datetime import date, datetime, timedelta, timezone

DAILY = 'Daily'
WEEKLY = 'Weekly'
MONTHLY = 'Monthly'
QUARTERLY = 'Quarterly'
SEMIANNUAL = 'Semiannual'
ANNUAL = 'Annual'
CUSTOM = 'Custom'
FREQUENCIES = (DAILY, WEEKLY, MONTHLY, QUARTERLY, SEMIANNUAL, ANNUAL, CUSTOM)

_MONTHS_BY_FREQUENCY = {MONTHLY: 1, QUARTERLY: 3, SEMIANNUAL: 6, ANNUAL: 12}


def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_due_after(current_due_date, plan):
    """The next due date after `current_due_date`, per the plan's frequency."""
    if plan.frequency == DAILY:
        return current_due_date + timedelta(days=1)
    if plan.frequency == WEEKLY:
        return current_due_date + timedelta(days=7)
    if plan.frequency in _MONTHS_BY_FREQUENCY:
        return _add_months(current_due_date, _MONTHS_BY_FREQUENCY[plan.frequency])
    if plan.frequency == CUSTOM:
        return current_due_date + timedelta(days=plan.custom_interval_days or 1)
    raise ValueError(f'Unknown maintenance frequency "{plan.frequency}".')


def _target_assets(plan):
    from application.models import Asset
    if plan.asset_id:
        return [plan.asset] if plan.asset.is_active else []
    return Asset.query.filter_by(asset_type_id=plan.asset_type_id, is_active=True).all()


def _ensure_schedules(plan, today):
    """Create a MaintenanceSchedule for any of the plan's target assets that don't have one yet."""
    from main import db
    from application.models import MaintenanceSchedule

    existing_asset_ids = {s.asset_id for s in plan.schedules}
    first_due = plan.start_date or today
    created = []
    for asset in _target_assets(plan):
        if asset.id not in existing_asset_ids:
            sched = MaintenanceSchedule(maintenance_plan_id=plan.id, asset_id=asset.id, next_due_date=first_due)
            db.session.add(sched)
            created.append(sched)
    if created:
        db.session.flush()
    return created


def generate_due_work_orders(today=None, user=None):
    """
    Ensure every active MaintenancePlan has a schedule for each of its
    target assets, then generate a WorkOrder (source=PM) for any schedule
    whose next_due_date has arrived. Returns the list of created WorkOrders.
    Commits on success.
    """
    from main import db
    from application.models import MaintenancePlan, MaintenanceSchedule, WorkOrder
    from application import workflow

    today = today or datetime.now(timezone.utc).date()
    created_work_orders = []

    for plan in MaintenancePlan.query.filter_by(is_active=True).all():
        _ensure_schedules(plan, today)

    due_schedules = (
        MaintenanceSchedule.query
        .join(MaintenancePlan, MaintenanceSchedule.maintenance_plan_id == MaintenancePlan.id)
        .filter(MaintenancePlan.is_active.is_(True))
        .filter(MaintenanceSchedule.next_due_date <= today)
        .all()
    )

    for sched in due_schedules:
        plan = sched.plan
        asset = sched.asset
        if not asset.is_active:
            continue

        due_date = sched.next_due_date
        wo = WorkOrder(
            site_id=asset.site_id,
            facility_id=asset.facility_id,
            room_id=asset.room_id,
            asset_id=asset.id,
            title=f"PM: {plan.name} — {asset.asset_tag}",
            description=plan.description,
            source=workflow.SOURCE_PM,
            status=workflow.NEW,
            priority_id=plan.priority_id,
            category_id=plan.category_id,
            assigned_to_id=plan.assigned_to_id,
            assigned_team=plan.assigned_team,
            due_date=due_date,
        )
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        workflow.record_initial_status(wo, user)
        if wo.assigned_to_id:
            workflow.apply_transition(wo, workflow.ASSIGNED, user, note='Assigned at PM generation')

        sched.last_work_order_id = wo.id
        sched.last_generated_at = datetime.now(timezone.utc)
        next_date = next_due_after(due_date, plan)
        while next_date <= today:
            next_date = next_due_after(next_date, plan)
        sched.next_due_date = next_date

        created_work_orders.append(wo)

    db.session.commit()
    return created_work_orders


def dashboard_buckets(schedules, today=None):
    """
    Split an iterable of (active) MaintenanceSchedule rows into the four
    PROJECT_PLAN.md PM dashboard buckets. Returns a dict of lists.
    """
    today = today or datetime.now(timezone.utc).date()
    week_end = today + timedelta(days=7)
    buckets = {'overdue': [], 'due_today': [], 'due_this_week': [], 'upcoming': []}
    for sched in schedules:
        if sched.next_due_date < today:
            buckets['overdue'].append(sched)
        elif sched.next_due_date == today:
            buckets['due_today'].append(sched)
        elif sched.next_due_date <= week_end:
            buckets['due_this_week'].append(sched)
        else:
            buckets['upcoming'].append(sched)
    return buckets
