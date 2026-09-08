"""
Work order status workflow — the single place status transitions are defined
and applied. Statuses and rules come from docs/PROJECT_PLAN.md.
"""
from datetime import datetime, timezone

NEW = 'New'
ASSIGNED = 'Assigned'
SCHEDULED = 'Scheduled'
IN_PROGRESS = 'In Progress'
WAITING_PARTS = 'Waiting for Parts'
WAITING_VENDOR = 'Waiting for Vendor'
WAITING_APPROVAL = 'Waiting for Approval'
ON_HOLD = 'On Hold'
COMPLETED = 'Completed'
CANCELLED = 'Cancelled'
CLOSED = 'Closed'

ALL_STATUSES = (NEW, ASSIGNED, SCHEDULED, IN_PROGRESS, WAITING_PARTS, WAITING_VENDOR,
                WAITING_APPROVAL, ON_HOLD, COMPLETED, CANCELLED, CLOSED)
WAITING_STATUSES = (WAITING_PARTS, WAITING_VENDOR, WAITING_APPROVAL, ON_HOLD)
TERMINAL_STATUSES = (COMPLETED, CANCELLED, CLOSED)
OPEN_STATUSES = tuple(s for s in ALL_STATUSES if s not in TERMINAL_STATUSES)

_ACTIVE_WORK = (ASSIGNED, SCHEDULED, IN_PROGRESS)
_PAUSED = (WAITING_PARTS, WAITING_VENDOR, WAITING_APPROVAL, ON_HOLD)

TRANSITIONS = {
    NEW: set(_ACTIVE_WORK) | {WAITING_APPROVAL, ON_HOLD, CANCELLED},
    ASSIGNED: {SCHEDULED, IN_PROGRESS, *_PAUSED, CANCELLED},
    SCHEDULED: {ASSIGNED, IN_PROGRESS, *_PAUSED, CANCELLED},
    IN_PROGRESS: {*_PAUSED, COMPLETED, CANCELLED},
    WAITING_PARTS: {*_ACTIVE_WORK, ON_HOLD, CANCELLED},
    WAITING_VENDOR: {*_ACTIVE_WORK, ON_HOLD, CANCELLED},
    WAITING_APPROVAL: {*_ACTIVE_WORK, ON_HOLD, CANCELLED},
    ON_HOLD: {*_ACTIVE_WORK, CANCELLED},
    COMPLETED: {CLOSED, IN_PROGRESS},   # IN_PROGRESS = reopen
    CANCELLED: {NEW},                   # reopen
    CLOSED: {IN_PROGRESS},              # reopen
}

SOURCE_REQUEST = 'Request'
SOURCE_MANUAL = 'Manual'
SOURCE_PM = 'PM'
SOURCE_INSPECTION = 'Inspection'
SOURCES = (SOURCE_REQUEST, SOURCE_MANUAL, SOURCE_PM, SOURCE_INSPECTION)

STATUS_BADGE = {
    NEW: 'warning', ASSIGNED: 'info', SCHEDULED: 'info', IN_PROGRESS: 'primary',
    WAITING_PARTS: 'secondary', WAITING_VENDOR: 'secondary', WAITING_APPROVAL: 'secondary',
    ON_HOLD: 'dark', COMPLETED: 'success', CANCELLED: 'danger', CLOSED: 'success',
}


class WorkflowError(ValueError):
    """An invalid transition or a missing data-quality field."""


def can_transition(from_status, to_status):
    return to_status in TRANSITIONS.get(from_status, set())


def allowed_transitions(from_status):
    return [s for s in ALL_STATUSES if can_transition(from_status, s)]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def apply_transition(work_order, to_status, user=None, note=None, completed_at=None, resolution=None):
    """
    Move a work order to `to_status`, enforcing the transition matrix and the
    data-quality rules, stamping lifecycle timestamps, and appending a
    WorkOrderStatusHistory row. Does not commit. Raises WorkflowError.
    """
    from main import db
    from application.models import WorkOrderStatusHistory

    from_status = work_order.status
    if to_status not in ALL_STATUSES:
        raise WorkflowError(f'Unknown status "{to_status}".')
    if not can_transition(from_status, to_status):
        raise WorkflowError(f'Cannot move a work order from "{from_status}" to "{to_status}".')

    now = _now()
    if to_status == COMPLETED:
        completed_at = completed_at or now
        if completed_at > now:
            raise WorkflowError('Completion date cannot be in the future.')
        work_order.completed_at = completed_at
        if resolution:
            work_order.resolution = resolution
    elif to_status == CLOSED:
        if resolution:
            work_order.resolution = resolution
        if not (work_order.resolution or '').strip():
            raise WorkflowError('A resolution is required to close a work order.')
        if not work_order.completed_at:
            work_order.completed_at = now
        work_order.closed_at = now
    elif to_status == IN_PROGRESS:
        if work_order.started_at is None:
            work_order.started_at = now
        # reopening clears the terminal stamps
        work_order.completed_at = None
        work_order.closed_at = None
    elif to_status == CANCELLED:
        work_order.closed_at = now
    elif to_status == NEW:
        work_order.closed_at = None
        work_order.completed_at = None

    work_order.status = to_status
    work_order.updated_at = now
    db.session.add(WorkOrderStatusHistory(
        work_order_id=work_order.id,
        from_status=from_status,
        to_status=to_status,
        changed_at=now,
        changed_by_id=user.id if user else None,
        note=note,
    ))
    return work_order


def record_initial_status(work_order, user=None):
    """Append the creating history row (from_status=None). Call after flush."""
    from main import db
    from application.models import WorkOrderStatusHistory
    db.session.add(WorkOrderStatusHistory(
        work_order_id=work_order.id,
        from_status=None,
        to_status=work_order.status,
        changed_at=_now(),
        changed_by_id=user.id if user else None,
        note=f'Created ({work_order.source})',
    ))
