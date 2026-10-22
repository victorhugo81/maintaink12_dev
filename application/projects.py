"""
Project cost roll-up: direct ProjectCost line items plus every linked
WorkOrder's cost (CostRecord.total_cost, falling back to WorkOrder.actual_cost
for work orders with nothing logged yet — same fallback as
application/costs.py's cost_rollup, for the same reason).

Also: task-scheduling helpers for the Gantt/Board extension —
would_create_cycle() guards ProjectTaskDependency inserts, dependency_violated()
is a pure read used only to render a warning badge (this app does NOT do
OpenProject-style automatic cascading rescheduling — moving a predecessor's
dates never shifts its successors), and gantt_payload() shapes a project's
tasks for the vendored frappe-gantt library.
"""
from datetime import timedelta


def would_create_cycle(project_id, predecessor_id, successor_id):
    """True if adding predecessor_id -> successor_id would close a cycle in
    this project's dependency graph (including predecessor_id == successor_id,
    the degenerate 1-node cycle). Pure graph traversal — no DB writes."""
    if predecessor_id == successor_id:
        return True
    from application.models import ProjectTaskDependency, ProjectTask

    edges = {}
    rows = (ProjectTaskDependency.query
            .join(ProjectTask, ProjectTaskDependency.predecessor_id == ProjectTask.id)
            .filter(ProjectTask.project_id == project_id).all())
    for dep in rows:
        edges.setdefault(dep.predecessor_id, []).append(dep.successor_id)

    seen, stack = set(), [successor_id]
    while stack:
        node = stack.pop()
        if node == predecessor_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, []))
    return False


def dependency_violated(dep):
    """True if the successor is scheduled to start before the predecessor
    (plus lag) finishes. Informational only — nothing auto-corrects this."""
    if not dep.successor.start_date or not dep.predecessor.due_date:
        return False
    return dep.successor.start_date < dep.predecessor.due_date + timedelta(days=dep.lag_days or 0)


def gantt_payload(project):
    """[{id, name, start, end, progress, dependencies, custom_class}, ...] for
    frappe-gantt — one entry per task that has at least a due_date (a task
    with no dates at all can't be placed on a timeline, so it's skipped).
    A milestone collapses to a single point (start == due_date); a normal
    task with no start_date falls back to due_date too. frappe-gantt needs
    end > start for a normal bar, so a same-day span is nudged by one day."""
    tasks = [t for t in project.tasks if t.due_date]
    by_id = {t.id: t for t in tasks}
    rows = []
    for t in tasks:
        start = t.due_date if t.is_milestone else (t.start_date or t.due_date)
        end = start if t.is_milestone else t.due_date
        if not t.is_milestone and end <= start:
            end = start + timedelta(days=1)
        dep_ids = [str(d.predecessor_id) for d in t.dependencies_as_successor if d.predecessor_id in by_id]
        rows.append({
            'id': str(t.id), 'name': t.name,
            'start': start.isoformat(), 'end': end.isoformat(),
            'progress': t.percent_complete or 0,
            'dependencies': ','.join(dep_ids),
            'custom_class': 'bar-milestone' if t.is_milestone else '',
        })
    return rows


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
