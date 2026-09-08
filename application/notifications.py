"""
Notification detection and dispatch (Phase 11): PM due/overdue, inspection
due/failed, vendor contract expiration, asset warranty expiration, and SLA
warning/breach — the eight events named in PROJECT_PLAN.md's notification
scope, each user-configurable via NotificationPreference and each
deduplicated via NotificationLog so a persistent condition (an overdue PM
schedule, an expiring contract) reminds on a cadence rather than every time
the sweep runs.

Two kinds of trigger:
* Scan-based (everything except inspection_failed): `run_notification_sweep()`
  is the one daily job (registered in scheduled_jobs.py, same pattern as
  pm.generate_due_work_orders) that finds the current condition set for each
  category and notifies once per (condition, recipient, bucket).
* Event-based (inspection_failed): `notify_inspection_failed()` is called
  once, right after an inspection with a failed item is recorded (routes.py),
  not from the sweep — the "bucket" for this one is always 'once' keyed by
  inspection id, which is naturally exactly-once since an Inspection can only
  be completed a single time.

Kept in a plain module, same reasoning as pm.py/costs.py/risk.py/sla.py: the
detection functions take plain values and are directly unit-testable; only
the dispatch functions touch Flask/DB.
"""
from datetime import datetime, timedelta, timezone

from application import workflow

EVENT_CADENCE = {
    'pm_due': 'day', 'pm_overdue': 'week',
    'inspection_due': 'day', 'inspection_failed': 'once',
    'vendor_contract_expiring': 'week', 'asset_warranty_expiring': 'week',
    'sla_warning': 'day', 'sla_breach': 'week',
}


def _today():
    return datetime.now(timezone.utc).date()


def bucket_key(event_type, today=None):
    today = today or _today()
    cadence = EVENT_CADENCE[event_type]
    if cadence == 'day':
        return today.isoformat()
    if cadence == 'week':
        year, week, _ = today.isocalendar()
        return f'{year}-W{week:02d}'
    return 'once'


def preference_allows(user, event_type):
    """A user with no NotificationPreference row is treated as 'everything
    on' — see the NotificationPreference model docstring in models.py."""
    pref = getattr(user, 'notification_preference', None)
    return True if pref is None else bool(getattr(pref, event_type))


def staff_recipients():
    from application.models import User
    return User.query.filter(User.role_id.in_((1, 2)), User.status == 'Active').all()


def _already_sent(event_type, entity_type, entity_id, user_id, bucket):
    from application.models import NotificationLog
    return NotificationLog.query.filter_by(event_type=event_type, entity_type=entity_type,
                                           entity_id=entity_id, user_id=user_id, bucket=bucket).first() is not None


def _dispatch(event_type, entity_type, entity_id, recipients, bucket, subject, body):
    """The one place every category funnels through: preference check, dedup
    check+record, then the actual (mail-gated) send. Returns how many
    recipients were newly notified."""
    from main import db
    from application.models import NotificationLog
    from application.email_utils import send_generic_notification

    sent = 0
    for user in recipients:
        if not preference_allows(user, event_type):
            continue
        if _already_sent(event_type, entity_type, entity_id, user.id, bucket):
            continue
        db.session.add(NotificationLog(event_type=event_type, entity_type=entity_type,
                                       entity_id=entity_id, user_id=user.id, bucket=bucket))
        send_generic_notification(user, subject, body)
        sent += 1
    return sent


# ---------------------------------------------------------------------------
# Scan-based categories
# ---------------------------------------------------------------------------

def _notify_pm(due_today, today):
    from application.models import MaintenanceSchedule, MaintenancePlan, Asset

    event_type = 'pm_due' if due_today else 'pm_overdue'
    bucket = bucket_key(event_type, today)
    cond = MaintenanceSchedule.next_due_date == today if due_today else MaintenanceSchedule.next_due_date < today
    schedules = MaintenanceSchedule.query.join(MaintenancePlan, MaintenanceSchedule.maintenance_plan_id == MaintenancePlan.id) \
        .join(Asset, MaintenanceSchedule.asset_id == Asset.id) \
        .filter(MaintenancePlan.is_active.is_(True), Asset.is_active.is_(True), cond).all()

    recipients = staff_recipients()
    count = 0
    for sched in schedules:
        subject = f'PM {"Due Today" if due_today else "Overdue"}: {sched.asset.asset_tag}'
        body = (f'Maintenance plan "{sched.plan.name}" is {"due today" if due_today else "overdue"} '
               f'for {sched.asset.asset_tag} — {sched.asset.name} (due {sched.next_due_date}).')
        count += _dispatch(event_type, 'maintenance_schedule', sched.id, recipients, bucket, subject, body)
    return count


def _notify_inspections_due(today):
    from application.models import Inspection

    event_type = 'inspection_due'
    bucket = bucket_key(event_type, today)
    inspections = Inspection.query.filter_by(status='Scheduled', due_date=today).all()
    recipients = staff_recipients()
    count = 0
    for insp in inspections:
        subject = f'Inspection Due Today: {insp.template.name}'
        body = f'Inspection "{insp.template.name}" is due today for {insp.target_label}.'
        count += _dispatch(event_type, 'inspection', insp.id, recipients, bucket, subject, body)
    return count


def notify_inspection_failed(inspection):
    """Event-driven, called once right after an inspection with a failed
    item is recorded (routes.py) — not part of the daily sweep."""
    event_type = 'inspection_failed'
    bucket = bucket_key(event_type)
    recipients = staff_recipients()
    subject = f'Inspection Failed Item(s): {inspection.template.name}'
    body = (f'Inspection "{inspection.template.name}" for {inspection.target_label} recorded '
           f'{inspection.failed_item_count} failed item(s).')
    return _dispatch(event_type, 'inspection', inspection.id, recipients, bucket, subject, body)


def _notify_vendor_contracts(today):
    from application.models import Vendor
    from application import vendors as vendors_module

    event_type = 'vendor_contract_expiring'
    bucket = bucket_key(event_type, today)
    recipients = staff_recipients()
    count = 0
    for vendor in Vendor.query.filter_by(is_active=True).all():
        status = vendors_module.expiration_status(vendor.contract_end_date, today)
        if status not in ('expired', 'expiring_soon'):
            continue
        subject = f'Vendor Contract {"Expired" if status == "expired" else "Expiring Soon"}: {vendor.name}'
        body = f'{vendor.name}\'s contract {"expired" if status == "expired" else "expires"} on {vendor.contract_end_date}.'
        count += _dispatch(event_type, 'vendor', vendor.id, recipients, bucket, subject, body)
    return count


def _notify_asset_warranties(today):
    from application.models import Asset
    from application import vendors as vendors_module

    event_type = 'asset_warranty_expiring'
    bucket = bucket_key(event_type, today)
    recipients = staff_recipients()
    count = 0
    for asset in Asset.query.filter_by(is_active=True).filter(Asset.warranty_expiration.isnot(None)).all():
        status = vendors_module.expiration_status(asset.warranty_expiration, today)
        if status not in ('expired', 'expiring_soon'):
            continue
        subject = f'Asset Warranty {"Expired" if status == "expired" else "Expiring Soon"}: {asset.asset_tag}'
        body = f'{asset.asset_tag} — {asset.name}\'s warranty {"expired" if status == "expired" else "expires"} on {asset.warranty_expiration}.'
        count += _dispatch(event_type, 'asset', asset.id, recipients, bucket, subject, body)
    return count


def _notify_sla(today, now):
    from application.models import WorkOrder, SLARule
    from application import sla as sla_module

    if not SLARule.query.filter_by(is_active=True).limit(1).first():
        return 0, 0

    lookback = datetime.combine(today - timedelta(days=14), datetime.min.time())
    candidates = WorkOrder.query.filter(
        WorkOrder.status.notin_((workflow.CANCELLED,)),
    ).filter(
        (WorkOrder.status.in_(workflow.OPEN_STATUSES)) |
        ((WorkOrder.status.in_((workflow.COMPLETED, workflow.CLOSED))) & (WorkOrder.completed_at >= lookback))
    ).limit(sla_module.SLA_SCAN_LIMIT).all()

    result = sla_module.scan(candidates, now=now)
    warn_sent = sum(_notify_sla_item('sla_warning', item, today) for item in result['warnings'])
    breach_sent = sum(_notify_sla_item('sla_breach', item, today) for item in result['breaches'])
    return warn_sent, breach_sent


def _notify_sla_item(event_type, item, today):
    wo = item['work_order']
    bucket = bucket_key(event_type, today)
    recipients = list(staff_recipients())
    if wo.assigned_to and wo.assigned_to.status == 'Active' and wo.assigned_to.id not in {u.id for u in recipients}:
        recipients.append(wo.assigned_to)
    entity_type = f'work_order_{item["metric"]}'
    verb = 'approaching its' if event_type == 'sla_warning' else 'has missed its'
    subject = f'SLA {"Warning" if event_type == "sla_warning" else "Breach"}: {wo.wo_number}'
    body = f'{wo.wo_number} — {wo.title} is {verb} {item["metric"]} deadline ({item["due_at"]}).'
    return _dispatch(event_type, entity_type, wo.id, recipients, bucket, subject, body)


def run_notification_sweep(today=None, now=None):
    """
    The one daily job. Returns {event_type: count_notified} regardless of
    whether mail is actually configured — dedup logging happens whenever the
    sweep decides a notification is due, matching every other "log the
    decision, let the mail gate control only the send" pattern in this
    project (email_utils._is_mail_configured()). Commits once at the end.
    """
    from main import db

    today = today or _today()
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    warn_sent, breach_sent = _notify_sla(today, now)
    summary = {
        'pm_due': _notify_pm(True, today),
        'pm_overdue': _notify_pm(False, today),
        'inspection_due': _notify_inspections_due(today),
        'inspection_failed': 0,  # event-driven, not part of the sweep
        'vendor_contract_expiring': _notify_vendor_contracts(today),
        'asset_warranty_expiring': _notify_asset_warranties(today),
        'sla_warning': warn_sent,
        'sla_breach': breach_sent,
    }
    db.session.commit()
    return summary
