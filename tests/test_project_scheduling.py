"""
Project scheduling (Gantt + Board) tests: cycle-safe dependency creation,
task editing (start/due dates, % complete, milestone), board status updates
(the percent_complete snap rules), and the Gantt JSON payload shape.
"""
from datetime import date, timedelta
import pytest


def _project(app, name):
    with app.app_context():
        from application.models import Project
        from main import db
        p = Project.query.filter_by(name=name).first()
        if p is None:
            p = Project(site_id=1, name=name, status='Planning')
            db.session.add(p)
            db.session.commit()
        return p.id


def _task(app, project_id, name, **kwargs):
    with app.app_context():
        from application.models import ProjectTask
        from main import db
        t = ProjectTask.query.filter_by(project_id=project_id, name=name).first()
        if t is None:
            t = ProjectTask(project_id=project_id, name=name, **kwargs)
            db.session.add(t)
            db.session.commit()
        return t.id


class TestDependencyCycleDetection:
    def test_direct_cycle_rejected(self, app):
        pid = _project(app, 'Cycle Project A')
        a = _task(app, pid, 'A')
        b = _task(app, pid, 'B')
        with app.app_context():
            from application import projects as projects_module
            from application.models import ProjectTaskDependency
            from main import db
            assert projects_module.would_create_cycle(pid, a, b) is False
            db.session.add(ProjectTaskDependency(predecessor_id=a, successor_id=b))
            db.session.commit()
            # B -> A would close A->B->A
            assert projects_module.would_create_cycle(pid, b, a) is True

    def test_indirect_cycle_rejected(self, app):
        pid = _project(app, 'Cycle Project B')
        a = _task(app, pid, 'A2')
        b = _task(app, pid, 'B2')
        c = _task(app, pid, 'C2')
        with app.app_context():
            from application import projects as projects_module
            from application.models import ProjectTaskDependency
            from main import db
            db.session.add(ProjectTaskDependency(predecessor_id=a, successor_id=b))
            db.session.add(ProjectTaskDependency(predecessor_id=b, successor_id=c))
            db.session.commit()
            # C -> A would close A->B->C->A
            assert projects_module.would_create_cycle(pid, c, a) is True
            # Unrelated new edge (A -> C, already reachable but not a cycle
            # back to A) is NOT itself the thing under test here, but a
            # fresh, non-reachable pair must still be allowed:
            d = _task(app, pid, 'D2')
            assert projects_module.would_create_cycle(pid, d, a) is False

    def test_self_dependency_rejected(self, app):
        pid = _project(app, 'Cycle Project C')
        a = _task(app, pid, 'Solo')
        with app.app_context():
            from application import projects as projects_module
            assert projects_module.would_create_cycle(pid, a, a) is True


class TestDependencyRoutes:
    def test_regular_user_cannot_add_dependency(self, app, user_client):
        pid = _project(app, 'Dep Route Project')
        a = _task(app, pid, 'Dep A')
        b = _task(app, pid, 'Dep B')
        r = user_client.post(f'/add_project_task_dependency/{pid}', data={
            'predecessor_id': str(a), 'successor_id': str(b), 'lag_days': '0',
        })
        assert r.status_code == 403

    def test_add_and_delete_dependency(self, app, admin_client):
        pid = _project(app, 'Dep Route Project 2')
        a = _task(app, pid, 'Dep A2')
        b = _task(app, pid, 'Dep B2')
        r = admin_client.post(f'/add_project_task_dependency/{pid}', data={
            'predecessor_id': str(a), 'successor_id': str(b), 'lag_days': '2',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTaskDependency
            dep = ProjectTaskDependency.query.filter_by(predecessor_id=a, successor_id=b).first()
            assert dep is not None and dep.lag_days == 2
            dep_id = dep.id

        r = admin_client.post(f'/delete_project_task_dependency/{dep_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTaskDependency
            assert ProjectTaskDependency.query.get(dep_id) is None

    def test_cyclic_dependency_rejected_via_route(self, app, admin_client):
        pid = _project(app, 'Dep Route Project 3')
        a = _task(app, pid, 'Dep A3')
        b = _task(app, pid, 'Dep B3')
        admin_client.post(f'/add_project_task_dependency/{pid}', data={
            'predecessor_id': str(a), 'successor_id': str(b), 'lag_days': '0',
        }, follow_redirects=True)
        r = admin_client.post(f'/add_project_task_dependency/{pid}', data={
            'predecessor_id': str(b), 'successor_id': str(a), 'lag_days': '0',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'circular dependency' in r.data
        with app.app_context():
            from application.models import ProjectTaskDependency
            assert ProjectTaskDependency.query.filter_by(predecessor_id=b, successor_id=a).first() is None


class TestEditProjectTask:
    def test_edit_task_persists_schedule_fields(self, app, admin_client):
        pid = _project(app, 'Edit Task Project')
        tid = _task(app, pid, 'Schedule Me')
        r = admin_client.post(f'/edit_project_task/{tid}', data={
            'name': 'Schedule Me', 'start_date': '2026-05-01', 'due_date': '2026-05-10',
            'percent_complete': '40', 'status': 'In Progress', 'sort_order': '0',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, tid)
            assert task.start_date == date(2026, 5, 1)
            assert task.due_date == date(2026, 5, 10)
            assert task.percent_complete == 40
            assert task.status == 'In Progress'
            assert task.completed_at is None

    def test_edit_task_milestone_flag(self, app, admin_client):
        pid = _project(app, 'Milestone Task Project')
        tid = _task(app, pid, 'Go Live')
        r = admin_client.post(f'/edit_project_task/{tid}', data={
            'name': 'Go Live', 'due_date': '2026-06-01', 'is_milestone': 'y',
            'percent_complete': '0', 'status': 'Not Started', 'sort_order': '0',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, tid)
            assert task.is_milestone is True

    def test_regular_user_cannot_edit_task(self, app, user_client):
        pid = _project(app, 'User Edit Task Project')
        tid = _task(app, pid, 'Protected Task')
        assert user_client.get(f'/edit_project_task/{tid}').status_code == 403


class TestBoardStatusUpdate:
    def test_moving_to_completed_snaps_percent_complete(self, app, admin_client):
        pid = _project(app, 'Board Project')
        tid = _task(app, pid, 'Board Task 1', percent_complete=50)
        r = admin_client.post(f'/update_project_task_status/{tid}', data={'status': 'Completed'}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, tid)
            assert task.status == 'Completed'
            assert task.percent_complete == 100
            assert task.completed_at is not None

    def test_reopening_to_not_started_resets_percent_complete(self, app, admin_client):
        pid = _project(app, 'Board Project 2')
        tid = _task(app, pid, 'Board Task 2', status='Completed', percent_complete=100)
        r = admin_client.post(f'/update_project_task_status/{tid}', data={'status': 'Not Started'}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, tid)
            assert task.status == 'Not Started'
            assert task.percent_complete == 0
            assert task.completed_at is None

    def test_moving_to_in_progress_leaves_percent_complete_alone(self, app, admin_client):
        pid = _project(app, 'Board Project 3')
        tid = _task(app, pid, 'Board Task 3', percent_complete=35)
        admin_client.post(f'/update_project_task_status/{tid}', data={'status': 'In Progress'}, follow_redirects=True)
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, tid)
            assert task.status == 'In Progress'
            assert task.percent_complete == 35

    def test_invalid_status_rejected(self, app, admin_client):
        pid = _project(app, 'Board Project 4')
        tid = _task(app, pid, 'Board Task 4')
        r = admin_client.post(f'/update_project_task_status/{tid}', data={'status': 'Bogus'})
        assert r.status_code == 400

    def test_regular_user_cannot_update_status(self, app, user_client):
        pid = _project(app, 'Board Project 5')
        tid = _task(app, pid, 'Board Task 5')
        assert user_client.post(f'/update_project_task_status/{tid}', data={'status': 'In Progress'}).status_code == 403


class TestGanttPayload:
    def test_milestone_collapses_to_a_point(self, app):
        pid = _project(app, 'Gantt Project')
        _task(app, pid, 'Milestone Task', due_date=date(2026, 7, 1), is_milestone=True)
        with app.app_context():
            from application import projects as projects_module
            from application.models import Project
            from main import db
            project = Project.query.get(pid)
            rows = projects_module.gantt_payload(project)
            row = next(r for r in rows if r['name'] == 'Milestone Task')
            assert row['start'] == row['end'] == '2026-07-01'
            assert row['custom_class'] == 'bar-milestone'

    def test_normal_task_end_nudged_past_start(self, app):
        pid = _project(app, 'Gantt Project 2')
        _task(app, pid, 'Same Day Task', start_date=date(2026, 7, 5), due_date=date(2026, 7, 5))
        with app.app_context():
            from application import projects as projects_module
            from application.models import Project
            project = Project.query.get(pid)
            rows = projects_module.gantt_payload(project)
            row = next(r for r in rows if r['name'] == 'Same Day Task')
            assert row['start'] == '2026-07-05'
            assert row['end'] == '2026-07-06'  # nudged so the bar has positive width

    def test_task_with_no_due_date_skipped(self, app):
        pid = _project(app, 'Gantt Project 3')
        _task(app, pid, 'No Dates Task')
        with app.app_context():
            from application import projects as projects_module
            from application.models import Project
            project = Project.query.get(pid)
            rows = projects_module.gantt_payload(project)
            assert all(r['name'] != 'No Dates Task' for r in rows)

    def test_dependency_included_by_id(self, app):
        pid = _project(app, 'Gantt Project 4')
        a = _task(app, pid, 'Gantt A', due_date=date(2026, 8, 1))
        b = _task(app, pid, 'Gantt B', due_date=date(2026, 8, 5))
        with app.app_context():
            from application.models import ProjectTaskDependency, Project
            from application import projects as projects_module
            from main import db
            db.session.add(ProjectTaskDependency(predecessor_id=a, successor_id=b))
            db.session.commit()
            project = Project.query.get(pid)
            rows = projects_module.gantt_payload(project)
            row_b = next(r for r in rows if r['name'] == 'Gantt B')
            assert row_b['dependencies'] == str(a)


class TestEditProjectPageRendersNewSections:
    def test_gantt_and_board_render(self, app, admin_client):
        pid = _project(app, 'Render Check Project')
        _task(app, pid, 'Render Task', due_date=date(2026, 9, 1), percent_complete=10)
        r = admin_client.get(f'/edit_project/{pid}')
        assert r.status_code == 200
        assert b'PROJECT_GANTT' in r.data
        assert b'Render Task' in r.data
        assert b'id="board-pane"' in r.data
        assert b'Not Started' in r.data and b'In Progress' in r.data and b'Completed' in r.data
