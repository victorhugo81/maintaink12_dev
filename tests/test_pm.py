"""
Preventive maintenance tests: plan CRUD, schedule generation, the no-duplicate
guarantee for a single due cycle, overdue catch-up across date boundaries,
and the PM dashboard buckets.
"""
from datetime import date, timedelta
import pytest


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='HVAC').first().id)


def _asset(app, tag, asset_type_name='PM Test Type'):
    """A facility + room + asset type + asset for PM tests. Reuses rows across calls."""
    with app.app_context():
        from application.models import Facility, Room, AssetType, Asset
        from main import db
        f = Facility.query.filter_by(name='PM Test Building').first()
        if f is None:
            f = Facility(site_id=1, name='PM Test Building')
            db.session.add(f)
            db.session.flush()
            db.session.add(Room(site_id=1, facility_id=f.id, room_number='P-1'))
            db.session.commit()
        room = Room.query.filter_by(facility_id=f.id, room_number='P-1').first()

        at = AssetType.query.filter_by(name=asset_type_name).first()
        if at is None:
            at = AssetType(name=asset_type_name)
            db.session.add(at)
            db.session.commit()

        a = Asset.query.filter_by(asset_tag=tag).first()
        if a is None:
            a = Asset(site_id=1, facility_id=f.id, room_id=room.id, asset_type_id=at.id,
                      asset_tag=tag, name=f'Asset {tag}')
            db.session.add(a)
            db.session.commit()
        return a.id, at.id


class TestMaintenancePlanCRUD:
    def test_regular_user_cannot_view_plans(self, user_client):
        assert user_client.get('/maintenance_plans').status_code == 403

    def test_add_plan_for_specific_asset(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-ASSET-1')
        pri, cat = _ids(app)
        r = admin_client.post('/add_maintenance_plan', data={
            'name': 'Quarterly Filter Change', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Quarterly', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import MaintenancePlan
            plan = MaintenancePlan.query.filter_by(name='Quarterly Filter Change').first()
            assert plan is not None
            assert plan.asset_id == asset_id and plan.asset_type_id is None

    def test_choosing_both_or_neither_target_rejected(self, app, admin_client):
        asset_id, asset_type_id = _asset(app, 'PM-ASSET-1')
        pri, cat = _ids(app)
        r = admin_client.post('/add_maintenance_plan', data={
            'name': 'Bad Plan A', 'asset_id': str(asset_id), 'asset_type_id': str(asset_type_id),
            'frequency': 'Monthly', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert b'exactly one' in r.data
        r = admin_client.post('/add_maintenance_plan', data={
            'name': 'Bad Plan B', 'asset_id': '0', 'asset_type_id': '0',
            'frequency': 'Monthly', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert b'exactly one' in r.data

    def test_custom_frequency_requires_interval(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-ASSET-1')
        pri, cat = _ids(app)
        r = admin_client.post('/add_maintenance_plan', data={
            'name': 'Custom No Interval', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Custom', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert b'Custom interval' in r.data or b'custom interval' in r.data


class TestScheduleGeneration:
    def test_asset_type_plan_creates_schedules_for_all_matching_assets(self, app, admin_client):
        a1_id, type_id = _asset(app, 'PM-TYPE-A', asset_type_name='PM Type Fan')
        a2_id, _ = _asset(app, 'PM-TYPE-B', asset_type_name='PM Type Fan')
        pri, cat = _ids(app)
        admin_client.post('/add_maintenance_plan', data={
            'name': 'Fan Belt Check', 'asset_id': '0', 'asset_type_id': str(type_id),
            'frequency': 'Monthly', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)

        with app.app_context():
            from application.pm import generate_due_work_orders
            from application.models import MaintenanceSchedule, WorkOrder
            from main import db
            today = date(2026, 1, 15)
            created = generate_due_work_orders(today=today)
            # Other plans from earlier tests may also fire on this call (shared
            # session-scoped DB) — scope the assertion to this plan's assets.
            created_for_plan = [w for w in created if w.asset_id in (a1_id, a2_id)]
            assert len(created_for_plan) == 2  # one per matching asset
            schedules = MaintenanceSchedule.query.join(
                MaintenanceSchedule.plan
            ).filter_by(name='Fan Belt Check').all()
            assert {s.asset_id for s in schedules} == {a1_id, a2_id}
            for s in schedules:
                assert s.next_due_date == date(2026, 2, 15)  # advanced one month past due
                assert s.last_work_order_id is not None
            for wo in created_for_plan:
                assert wo.source == 'PM'
                assert wo.due_date == today

    def test_no_duplicate_work_order_for_same_due_cycle(self, app):
        asset_id, _ = _asset(app, 'PM-ASSET-1')
        with app.app_context():
            from application.pm import generate_due_work_orders
            from application.models import WorkOrder
            from main import db

            today = date(2026, 1, 20)
            first = generate_due_work_orders(today=today)
            count_after_first = WorkOrder.query.filter_by(source='PM', asset_id=asset_id).count()
            assert count_after_first >= 1

            # Re-run the SAME day: must not create another one for this schedule.
            second = generate_due_work_orders(today=today)
            count_after_second = WorkOrder.query.filter_by(source='PM', asset_id=asset_id).count()
            assert count_after_second == count_after_first

            # And again a day later, before the next cycle: still nothing new.
            third = generate_due_work_orders(today=today + timedelta(days=1))
            assert WorkOrder.query.filter_by(source='PM', asset_id=asset_id).count() == count_after_first

    def test_overdue_catch_up_generates_exactly_one_work_order(self, app):
        """A weekly plan whose schedule is 3 weeks overdue still gets exactly one
        work order, and next_due_date fast-forwards past today, not one cycle at a time."""
        asset_id, _ = _asset(app, 'PM-WEEKLY', asset_type_name='PM Weekly Type')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import MaintenancePlan
            from main import db
            plan = MaintenancePlan(name='Weekly Overdue Test', asset_id=asset_id,
                                   frequency='Weekly', category_id=cat, priority_id=pri,
                                   start_date=date(2026, 3, 1))
            db.session.add(plan)
            db.session.commit()

            from application.pm import generate_due_work_orders
            from application.models import WorkOrder, MaintenanceSchedule
            # Jump straight to a date 3+ weeks after the first due date without
            # ever running generation in between (simulates the job being down).
            check_date = date(2026, 3, 25)  # 24 days after start_date
            created = generate_due_work_orders(today=check_date)
            wo_for_this_asset = [w for w in created if w.asset_id == asset_id]
            assert len(wo_for_this_asset) == 1

            sched = MaintenanceSchedule.query.filter_by(maintenance_plan_id=plan.id, asset_id=asset_id).first()
            assert sched.next_due_date > check_date
            # 2026-03-01 + 7*4 = 2026-03-29, the first Monday-cycle strictly after check_date
            assert sched.next_due_date == date(2026, 3, 29)

    def test_start_date_seeds_first_due_date(self, app):
        asset_id, _ = _asset(app, 'PM-FUTURE', asset_type_name='PM Future Type')
        pri, cat = _ids(app)
        future_start = date(2027, 1, 1)
        with app.app_context():
            from application.models import MaintenancePlan
            from main import db
            plan = MaintenancePlan(name='Future Start Plan', asset_id=asset_id,
                                   frequency='Annual', category_id=cat, priority_id=pri,
                                   start_date=future_start)
            db.session.add(plan)
            db.session.commit()

            from application.pm import generate_due_work_orders
            from application.models import MaintenanceSchedule
            created = generate_due_work_orders(today=date(2026, 6, 1))
            assert not any(w.asset_id == asset_id for w in created)
            sched = MaintenanceSchedule.query.filter_by(maintenance_plan_id=plan.id).first()
            assert sched.next_due_date == future_start

    def test_inactive_plan_is_skipped(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-INACTIVE', asset_type_name='PM Inactive Type')
        pri, cat = _ids(app)
        admin_client.post('/add_maintenance_plan', data={
            'name': 'Disabled Plan', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Daily', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        with app.app_context():
            from application.models import MaintenancePlan
            from main import db
            plan = MaintenancePlan.query.filter_by(name='Disabled Plan').first()
            plan.is_active = False
            db.session.commit()

            from application.pm import generate_due_work_orders
            created = generate_due_work_orders(today=date(2026, 5, 1))
            assert not any(w.asset_id == asset_id for w in created)

    def test_inactive_asset_is_skipped(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-DEACTIVATED', asset_type_name='PM Deactivated Type')
        pri, cat = _ids(app)
        admin_client.post('/add_maintenance_plan', data={
            'name': 'Plan For Retired Asset', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Daily', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        admin_client.post(f'/delete_asset/{asset_id}', follow_redirects=True)  # soft delete
        with app.app_context():
            from application.pm import generate_due_work_orders
            created = generate_due_work_orders(today=date(2026, 5, 2))
            assert not any(w.asset_id == asset_id for w in created)

    def test_assigned_plan_generates_pre_assigned_work_order(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-ASSIGNED', asset_type_name='PM Assigned Type')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import User
            from application.utils import hash_email
            from werkzeug.security import generate_password_hash
            from main import db
            # assigned_to_id choices are role_id in (2, 3) — a Specialist/Technician,
            # not an Admin — so this must be a tech user, not admin@test.com.
            tech = User.query.filter_by(email_hash=hash_email('pm-assign-tech@test.com', app.config['SECRET_KEY'])).first()
            if tech is None:
                tech = User(first_name='Assign', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=1)
                tech.email = 'pm-assign-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            tech_id = tech.id
        admin_client.post('/add_maintenance_plan', data={
            'name': 'Assigned PM Plan', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Monthly', 'category_id': str(cat), 'priority_id': str(pri),
            'assigned_to_id': str(tech_id),
        }, follow_redirects=True)
        with app.app_context():
            from application.pm import generate_due_work_orders
            created = generate_due_work_orders(today=date(2026, 4, 1))
            wo = next(w for w in created if w.asset_id == asset_id)
            assert wo.assigned_to_id == tech_id
            assert wo.status == 'Assigned'
            assert [h.to_status for h in wo.status_history] == ['Assigned', 'New']


class TestPMDashboard:
    def test_regular_user_cannot_view_dashboard(self, user_client):
        assert user_client.get('/pm_dashboard').status_code == 403

    def test_dashboard_loads_for_staff(self, admin_client):
        assert admin_client.get('/pm_dashboard').status_code == 200

    def test_buckets_split_correctly(self):
        from application.pm import dashboard_buckets

        class FakeSchedule:
            def __init__(self, due):
                self.next_due_date = due

        today = date(2026, 6, 15)
        schedules = [
            FakeSchedule(date(2026, 6, 10)),   # overdue
            FakeSchedule(date(2026, 6, 15)),   # due today
            FakeSchedule(date(2026, 6, 20)),   # due this week (5 days out)
            FakeSchedule(date(2026, 6, 22)),   # due this week (boundary, exactly +7)
            FakeSchedule(date(2026, 6, 23)),   # upcoming (+8, past the week window)
        ]
        buckets = dashboard_buckets(schedules, today=today)
        assert len(buckets['overdue']) == 1
        assert len(buckets['due_today']) == 1
        assert len(buckets['due_this_week']) == 2
        assert len(buckets['upcoming']) == 1

    def test_run_pm_generation_forbidden_for_regular_user(self, user_client):
        assert user_client.post('/run_pm_generation').status_code == 403

    def test_run_pm_generation_button_works_for_admin(self, admin_client):
        r = admin_client.post('/run_pm_generation', follow_redirects=True)
        assert r.status_code == 200

    def test_technician_sees_only_own_site_schedules(self, app):
        # Deliberately takes only `app`, not admin_client — issuing an authenticated
        # request with one identity and then another with a different identity in the
        # same test has been observed to make the second client inherit the first
        # one's current_user (reproducible even on a pre-existing Phase-1 route like
        # /sites, so it's a test-client quirk, not a Phase 4 bug). Every site-scoping
        # test in this project therefore creates its setup data directly via the ORM
        # and issues HTTP requests as exactly one identity.
        asset_id, _ = _asset(app, 'PM-SITE-SCOPE', asset_type_name='PM Site Scope Type')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import MaintenancePlan, Site, User
            from application.utils import hash_email
            from werkzeug.security import generate_password_hash
            from main import db

            plan = MaintenancePlan(name='Site Scoped Plan', asset_id=asset_id,
                                   frequency='Daily', category_id=cat, priority_id=pri)
            db.session.add(plan)
            db.session.commit()

            from application.pm import generate_due_work_orders
            generate_due_work_orders(today=date(2026, 7, 1))

            other_site = Site.query.filter_by(site_name='PM Other School').first()
            if other_site is None:
                other_site = Site(site_name='PM Other School', site_acronyms='PMO', site_code='088',
                                  site_cds='00-000-0000088', site_address='8 Other St', site_type='Elementary')
                db.session.add(other_site)
                db.session.commit()
            tech = User.query.filter_by(email_hash=hash_email('pm-tech@test.com', app.config['SECRET_KEY'])).first()
            if tech is None:
                tech = User(first_name='PM', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=other_site.id)
                tech.email = 'pm-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            tech_id = tech.id

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(tech_id)
                sess['_fresh'] = True
            r = c.get('/pm_dashboard')
            assert r.status_code == 200
            assert b'PM-SITE-SCOPE' not in r.data
