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

    def test_assigned_plan_generates_work_order_with_assignee_but_open_status(self, app, admin_client):
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
            # Assigning no longer changes status (New/Assigned/Scheduled
            # collapsed into one OPEN status) — a plan with a default
            # assignee still generates an Open work order, just pre-filled.
            assert wo.status == 'Open'
            assert [h.to_status for h in wo.status_history] == ['Open']


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


class TestMaintenanceChecklist:
    """PM checklists (IncidentIQ-inspired addition): a MaintenancePlan can carry
    reusable checklist tasks; each WorkOrder it generates gets its own
    checked/unchecked state against those tasks, tracked separately per WO
    since one plan generates many work orders over time."""

    def test_add_and_delete_checklist_item(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-CHECK-1', asset_type_name='PM Checklist Type')
        pri, cat = _ids(app)
        admin_client.post('/add_maintenance_plan', data={
            'name': 'Boiler Service', 'asset_id': str(asset_id), 'asset_type_id': '0',
            'frequency': 'Annual', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        with app.app_context():
            from application.models import MaintenancePlan
            plan = MaintenancePlan.query.filter_by(name='Boiler Service').first()
            plan_id = plan.id

        r = admin_client.post(f'/add_maintenance_checklist_item/{plan_id}', data={
            'task': 'Check pressure relief valve', 'sort_order': '0',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'Check pressure relief valve' in r.data

        with app.app_context():
            from application.models import MaintenanceChecklistItem
            item = MaintenanceChecklistItem.query.filter_by(
                maintenance_plan_id=plan_id, task='Check pressure relief valve').first()
            assert item is not None
            item_id = item.id

        r = admin_client.post(f'/delete_maintenance_checklist_item/{item_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import MaintenanceChecklistItem
            assert MaintenanceChecklistItem.query.get(item_id) is None

    def test_regular_user_cannot_manage_checklist_items(self, app, user_client):
        asset_id, _ = _asset(app, 'PM-CHECK-2', asset_type_name='PM Checklist Type 2')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import MaintenancePlan
            from main import db
            plan = MaintenancePlan(name='User Scope Plan', asset_id=asset_id,
                                   frequency='Monthly', category_id=cat, priority_id=pri)
            db.session.add(plan)
            db.session.commit()
            plan_id = plan.id
        assert user_client.post(f'/add_maintenance_checklist_item/{plan_id}',
                                 data={'task': 'x', 'sort_order': '0'}).status_code == 403

    def test_generated_work_order_links_to_plan_and_shows_checklist(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-CHECK-3', asset_type_name='PM Checklist Type 3')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import MaintenancePlan, MaintenanceChecklistItem
            from main import db
            plan = MaintenancePlan(name='Filter Swap Plan', asset_id=asset_id,
                                   frequency='Monthly', category_id=cat, priority_id=pri)
            db.session.add(plan)
            db.session.flush()
            db.session.add(MaintenanceChecklistItem(maintenance_plan_id=plan.id, task='Replace air filter', sort_order=0))
            db.session.add(MaintenanceChecklistItem(maintenance_plan_id=plan.id, task='Check belt tension', sort_order=1))
            db.session.commit()
            plan_id = plan.id

            from application.pm import generate_due_work_orders
            created = generate_due_work_orders(today=date(2026, 4, 1))
            wo = next(w for w in created if w.asset_id == asset_id)
            wo_id = wo.id
            assert wo.maintenance_plan_id == plan_id

        r = admin_client.get(f'/edit_work_order/{wo_id}')
        assert r.status_code == 200
        assert b'PM Checklist' in r.data
        assert b'Replace air filter' in r.data
        assert b'Check belt tension' in r.data

    def test_update_checklist_records_checked_state_and_author(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-CHECK-4', asset_type_name='PM Checklist Type 4')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import MaintenancePlan, MaintenanceChecklistItem, User
            from main import db
            plan = MaintenancePlan(name='Pump Check Plan', asset_id=asset_id,
                                   frequency='Monthly', category_id=cat, priority_id=pri)
            db.session.add(plan)
            db.session.flush()
            item1 = MaintenanceChecklistItem(maintenance_plan_id=plan.id, task='Grease bearings', sort_order=0)
            item2 = MaintenanceChecklistItem(maintenance_plan_id=plan.id, task='Inspect seals', sort_order=1)
            db.session.add_all([item1, item2])
            db.session.commit()
            item1_id, item2_id = item1.id, item2.id

            from application.pm import generate_due_work_orders
            created = generate_due_work_orders(today=date(2026, 4, 1))
            wo_id = next(w for w in created if w.asset_id == asset_id).id
            admin_id = User.query.filter_by(role_id=1).first().id

        r = admin_client.post(f'/update_work_order_checklist/{wo_id}', data={
            f'checklist_{item1_id}': 'on',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import WorkOrderChecklistResult
            r1 = WorkOrderChecklistResult.query.filter_by(checklist_item_id=item1_id).first()
            r2 = WorkOrderChecklistResult.query.filter_by(checklist_item_id=item2_id).first()
            assert r1.is_checked is True
            assert r1.checked_by_id == admin_id
            assert r1.checked_at is not None
            assert r2 is None  # never checked -> no row created at all

        # Uncheck item1 in a second submission: row flips back, not deleted.
        admin_client.post(f'/update_work_order_checklist/{wo_id}', data={}, follow_redirects=True)
        with app.app_context():
            from application.models import WorkOrderChecklistResult
            r1 = WorkOrderChecklistResult.query.filter_by(checklist_item_id=item1_id).first()
            assert r1.is_checked is False
            assert r1.checked_at is None

    def test_non_pm_work_order_has_no_checklist_section(self, app, admin_client):
        asset_id, _ = _asset(app, 'PM-CHECK-5', asset_type_name='PM Checklist Type 5')
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import Facility, WorkOrder
            from application import workflow
            from main import db
            fac = Facility.query.filter_by(name='PM Test Building').first()
            wo = WorkOrder(site_id=1, facility_id=fac.id, title='Manual WO, not PM',
                          source='Manual', status=workflow.OPEN, priority_id=pri, category_id=cat)
            db.session.add(wo)
            db.session.commit()
            wo_id = wo.id

        r = admin_client.get(f'/edit_work_order/{wo_id}')
        assert r.status_code == 200
        assert b'PM Checklist' not in r.data
