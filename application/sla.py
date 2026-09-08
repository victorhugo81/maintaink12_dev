"""
SLA rules and breach/warning detection (Phase 11).

Response and resolution are two independently tracked metrics, each computed
as `created_at + <rule hours>` from the WorkOrder's SLARule (looked up by
priority — one admin-configured rule per priority; a priority with none has
no SLA state at all, never a fabricated deadline). "Response" reuses the
same started_at-based proxy Phase 9's `analytics.work_order_kpis()` already
uses for Avg Response Time (time to IN_PROGRESS), rather than introducing a
second, competing definition of "responded to" — see docs/PHASE_11_REPORT.md
for why a separate first-response timestamp wasn't added instead.

Kept in a plain module, same reasoning as pm.py/costs.py/risk.py: no Flask
request-context assumptions, so the state machine is directly unit-testable.
"""
from datetime import datetime, timedelta, timezone

from application import workflow

WARNING_FRACTION = 0.2  # the last 20% of the allotted window counts as "warning"

MET = 'met'
BREACHED = 'breached'
WARNING = 'warning'
PENDING = 'pending'

SLA_SCAN_LIMIT = 5000


def rules_by_priority(rules=None):
    """{priority_id: SLARule} for every active rule, or from a given iterable (tests)."""
    if rules is None:
        from application.models import SLARule
        rules = SLARule.query.filter_by(is_active=True).all()
    return {r.priority_id: r for r in rules}


def response_due_at(wo, rule):
    return wo.created_at + timedelta(hours=rule.response_hours)


def resolution_due_at(wo, rule):
    return wo.created_at + timedelta(hours=rule.resolution_hours)


def _state(due_at, completed_marker, hours, now):
    """Shared state machine for both the response and resolution metrics."""
    if completed_marker is not None:
        return MET if completed_marker <= due_at else BREACHED
    if now > due_at:
        return BREACHED
    warning_start = due_at - timedelta(hours=hours * WARNING_FRACTION)
    if now >= warning_start:
        return WARNING
    return PENDING


def evaluate(wo, rule, now=None):
    """
    {'response': state, 'response_due_at', 'resolution': state,
    'resolution_due_at'} for one work order against its priority's rule.
    Callers should skip Cancelled work orders before calling this — a
    cancelled item never had a real SLA outcome either way.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    r_due = response_due_at(wo, rule)
    s_due = resolution_due_at(wo, rule)
    return {
        'response': _state(r_due, wo.started_at, rule.response_hours, now),
        'response_due_at': r_due,
        'resolution': _state(s_due, wo.completed_at, rule.resolution_hours, now),
        'resolution_due_at': s_due,
    }


def scan(work_orders, rules=None, now=None):
    """
    Evaluate every non-Cancelled work order that has a matching rule.
    Returns {'breaches': [...], 'warnings': [...]} of {'work_order',
    'metric' ('response'|'resolution'), 'due_at'} dicts — a work order
    breaching both metrics appears once per metric, not merged into one row.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    by_priority = rules_by_priority(rules)
    breaches, warnings = [], []
    for wo in work_orders:
        if wo.status == workflow.CANCELLED:
            continue
        rule = by_priority.get(wo.priority_id)
        if rule is None:
            continue
        result = evaluate(wo, rule, now)
        for metric in ('response', 'resolution'):
            state = result[metric]
            if state == BREACHED:
                breaches.append({'work_order': wo, 'metric': metric, 'due_at': result[f'{metric}_due_at']})
            elif state == WARNING:
                warnings.append({'work_order': wo, 'metric': metric, 'due_at': result[f'{metric}_due_at']})
    return {'breaches': breaches, 'warnings': warnings}


def aggregate_compliance(work_orders, rules=None):
    """
    SLA Compliance using real SLARule targets: of completed work orders with
    a matching rule, how many met their resolution deadline. Returns
    (met, total) — total 0 means no rule-tracked completions were found
    (e.g. no SLARule has been configured yet), the signal
    `analytics.work_order_kpis()` uses to fall back to its due-date-based
    approximation rather than reporting a false 0%/100%.
    """
    by_priority = rules_by_priority(rules)
    met = total = 0
    for wo in work_orders:
        if wo.status not in (workflow.COMPLETED, workflow.CLOSED) or wo.completed_at is None:
            continue
        rule = by_priority.get(wo.priority_id)
        if rule is None:
            continue
        total += 1
        if wo.completed_at <= resolution_due_at(wo, rule):
            met += 1
    return met, total
