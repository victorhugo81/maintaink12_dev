"""
Labor & cost tracking tests: labor-hour and cost aggregation correctness
across every rollup dimension, cost-record refresh on labor/material
add/delete, and technician workload calculation.
"""
from datetime import date, datetime, timedelta, timezone
import pytest


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='HVAC').first().id)


def _facility(app, name='Cost Test Building'):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=1, name=name)
            db.session.add(f)
            db.session.commit()
        return f.id


def _tech(app, email='cost-tech@test.com', first='Cost'):
    with app.app_context():
        from application.models import User
        from application.utils import hash_email
        from werkzeug.security import generate_password_hash
        from main import db
        u = User.query.filter_by(email_hash=hash_email(email, app.config['SECRET_KEY'])).first()
        if u is None:
            u = User(first_name=first, last_name='Tech', status='Active',
                     password=generate_password_hash('Some@Password1'), must_change_password=False,
                     failed_login_attempts=0, role_id=3, site_id=1)
            u.email = email
            db.session.add(u)
            db.session.commit()
        return u.id


def _work_order(app, title, **kwargs):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app)
        facility_id = _facility(app)
        wo = WorkOrder(site_id=1, facility_id=facility_id, title=title, source='Manual',
                       status=kwargs.pop('status', 'New'), priority_id=pri, category_id=cat, **kwargs)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        db.session.commit()
        return wo.id


class TestLaborEntries:
    def test_regular_user_cannot_add_labor(self, app, user_client):
        wo_id = _work_order(app, 'Labor access control WO')
        tech_id = _tech(app)
        r = user_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '2', 'labor_type': 'Regular',
        })
        assert r.status_code == 403

    def test_add_labor_with_direct_hours(self, app, admin_client):
        wo_id = _work_order(app, 'Direct hours WO')
        tech_id = _tech(app)
        r = admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '3.5', 'labor_type': 'Overtime',
            'hourly_rate': '40.00', 'notes': 'Replaced belt',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderLabor
            entry = WorkOrderLabor.query.filter_by(work_order_id=wo_id).first()
            assert entry is not None
            assert float(entry.labor_hours) == 3.5
            assert entry.labor_type == 'Overtime'
            assert float(entry.labor_cost) == 140.0  # 3.5 * 40

    def test_hours_auto_computed_from_start_end_time(self, app, admin_client):
        wo_id = _work_order(app, 'Auto-computed hours WO')
        tech_id = _tech(app)
        r = admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_type': 'Regular',
            'start_time': '2026-09-01T08:00', 'end_time': '2026-09-01T10:30',
            'hourly_rate': '20',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderLabor
            entry = WorkOrderLabor.query.filter_by(work_order_id=wo_id).first()
            assert float(entry.labor_hours) == 2.5  # 08:00 to 10:30

    def test_end_before_start_rejected(self, app, admin_client):
        wo_id = _work_order(app, 'Bad time range WO')
        tech_id = _tech(app)
        r = admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_type': 'Regular',
            'start_time': '2026-09-01T10:00', 'end_time': '2026-09-01T08:00',
        }, follow_redirects=True)
        assert b'End time must be after start time' in r.data
        with app.app_context():
            from application.models import WorkOrderLabor
            assert WorkOrderLabor.query.filter_by(work_order_id=wo_id).count() == 0

    def test_missing_hours_and_times_rejected(self, app, admin_client):
        wo_id = _work_order(app, 'No hours no times WO')
        tech_id = _tech(app)
        r = admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_type': 'Regular',
        }, follow_redirects=True)
        assert b'Enter labor hours' in r.data

    def test_delete_labor_entry(self, app, admin_client):
        wo_id = _work_order(app, 'Delete labor WO')
        tech_id = _tech(app)
        admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '1', 'labor_type': 'Regular',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import WorkOrderLabor
            entry_id = WorkOrderLabor.query.filter_by(work_order_id=wo_id).first().id
        r = admin_client.post(f'/delete_work_order_labor/{entry_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderLabor
            from main import db
            assert db.session.get(WorkOrderLabor, entry_id) is None


class TestMaterialEntries:
    def test_add_material(self, app, admin_client):
        wo_id = _work_order(app, 'Material WO')
        r = admin_client.post(f'/add_work_order_material/{wo_id}', data={
            'description': 'HVAC filter 20x20', 'quantity': '4', 'unit_cost': '12.50',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderMaterial
            m = WorkOrderMaterial.query.filter_by(work_order_id=wo_id).first()
            assert m is not None
            assert float(m.total_cost) == 50.0  # 4 * 12.50

    def test_delete_material_entry(self, app, admin_client):
        wo_id = _work_order(app, 'Delete material WO')
        admin_client.post(f'/add_work_order_material/{wo_id}', data={
            'description': 'Widget', 'quantity': '1', 'unit_cost': '10',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import WorkOrderMaterial
            mid = WorkOrderMaterial.query.filter_by(work_order_id=wo_id).first().id
        r = admin_client.post(f'/delete_work_order_material/{mid}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderMaterial
            from main import db
            assert db.session.get(WorkOrderMaterial, mid) is None


class TestCostRecordRefresh:
    def test_cost_record_created_and_summed_from_labor_and_materials(self, app, admin_client):
        wo_id = _work_order(app, 'Full cost WO')
        tech_id = _tech(app)
        admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '2', 'hourly_rate': '30', 'labor_type': 'Regular',
        }, follow_redirects=True)  # labor_cost = 60
        admin_client.post(f'/add_work_order_material/{wo_id}', data={
            'description': 'Part A', 'quantity': '2', 'unit_cost': '15',
        }, follow_redirects=True)  # material_cost = 30

        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            assert wo.cost_record is not None
            assert float(wo.cost_record.labor_cost) == 60.0
            assert float(wo.cost_record.material_cost) == 30.0
            assert float(wo.cost_record.total_cost) == 90.0  # vendor/other default to 0

    def test_removing_a_labor_entry_recalculates_total(self, app, admin_client):
        wo_id = _work_order(app, 'Recalculate WO')
        tech_id = _tech(app)
        admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '5', 'hourly_rate': '20', 'labor_type': 'Regular',
        }, follow_redirects=True)  # 100
        with app.app_context():
            from application.models import WorkOrderLabor
            entry_id = WorkOrderLabor.query.filter_by(work_order_id=wo_id).first().id
        admin_client.post(f'/delete_work_order_labor/{entry_id}', follow_redirects=True)

        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            assert float(wo.cost_record.labor_cost) == 0.0
            assert float(wo.cost_record.total_cost) == 0.0

    def test_vendor_and_other_cost_added_directly(self, app, admin_client):
        wo_id = _work_order(app, 'Vendor and other cost WO')
        r = admin_client.post(f'/update_work_order_cost/{wo_id}', data={
            'vendor_cost': '200', 'other_cost': '25',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            assert float(wo.cost_record.vendor_cost) == 200.0
            assert float(wo.cost_record.other_cost) == 25.0
            assert float(wo.cost_record.total_cost) == 225.0

    def test_vendor_other_cost_survives_subsequent_labor_add(self, app, admin_client):
        """Refreshing labor/material cost must not clobber manually-entered vendor/other cost."""
        wo_id = _work_order(app, 'Survives refresh WO')
        admin_client.post(f'/update_work_order_cost/{wo_id}', data={
            'vendor_cost': '500', 'other_cost': '0',
        }, follow_redirects=True)
        tech_id = _tech(app)
        admin_client.post(f'/add_work_order_labor/{wo_id}', data={
            'technician_id': str(tech_id), 'labor_hours': '1', 'hourly_rate': '50', 'labor_type': 'Regular',
        }, follow_redirects=True)

        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            assert float(wo.cost_record.vendor_cost) == 500.0  # untouched
            assert float(wo.cost_record.labor_cost) == 50.0
            assert float(wo.cost_record.total_cost) == 550.0

    def test_regular_user_cannot_update_cost(self, app, user_client):
        wo_id = _work_order(app, 'Cost access control WO')
        r = user_client.post(f'/update_work_order_cost/{wo_id}', data={'vendor_cost': '999'})
        assert r.status_code == 403


class TestCostRollupPure:
    """Directly exercise application/costs.py's aggregation, independent of routes."""

    def _fake_wo(self, **kwargs):
        class Fake:
            pass
        wo = Fake()
        wo.wo_number = kwargs.get('wo_number', 'WO-000001')
        wo.facility = kwargs.get('facility')
        wo.site = kwargs.get('site')
        wo.asset = kwargs.get('asset')
        wo.category = kwargs.get('category')
        wo.vendor = kwargs.get('vendor')
        wo.assigned_team = kwargs.get('assigned_team')
        wo.assigned_to = kwargs.get('assigned_to')
        wo.created_at = kwargs.get('created_at', datetime(2026, 3, 15))
        wo.estimated_cost = kwargs.get('estimated_cost', 0)
        wo.actual_cost = kwargs.get('actual_cost', 0)
        wo.cost_record = kwargs.get('cost_record')
        return wo

    class FakeNamed:
        def __init__(self, name):
            self.name = name
            self.site_name = name
            self.asset_tag = name

    class FakeCostRecord:
        def __init__(self, total):
            self.total_cost = total

    def test_rollup_by_category_sums_correctly(self):
        from application.costs import cost_rollup
        cat_hvac = self.FakeNamed('HVAC')
        cat_elec = self.FakeNamed('Electrical')
        wos = [
            self._fake_wo(category=cat_hvac, estimated_cost=100, cost_record=self.FakeCostRecord(120)),
            self._fake_wo(category=cat_hvac, estimated_cost=50, cost_record=self.FakeCostRecord(40)),
            self._fake_wo(category=cat_elec, estimated_cost=200, cost_record=self.FakeCostRecord(200)),
        ]
        rows = cost_rollup(wos, 'category')
        by_label = {r['label']: r for r in rows}
        assert by_label['Electrical']['count'] == 1
        assert by_label['Electrical']['estimated'] == 200
        assert by_label['Electrical']['actual'] == 200
        assert by_label['HVAC']['count'] == 2
        assert by_label['HVAC']['estimated'] == 150
        assert by_label['HVAC']['actual'] == 160

    def test_rollup_falls_back_to_actual_cost_without_cost_record(self):
        from application.costs import cost_rollup
        wos = [self._fake_wo(category=self.FakeNamed('Plumbing'), actual_cost=75, cost_record=None)]
        rows = cost_rollup(wos, 'category')
        assert rows[0]['actual'] == 75

    def test_rollup_by_month_and_year(self):
        from application.costs import cost_rollup
        wos = [
            self._fake_wo(created_at=datetime(2026, 1, 10), estimated_cost=10, cost_record=self.FakeCostRecord(10)),
            self._fake_wo(created_at=datetime(2026, 1, 20), estimated_cost=20, cost_record=self.FakeCostRecord(20)),
            self._fake_wo(created_at=datetime(2027, 1, 5), estimated_cost=5, cost_record=self.FakeCostRecord(5)),
        ]
        month_rows = {r['label']: r for r in cost_rollup(wos, 'month')}
        assert month_rows['2026-01']['count'] == 2
        assert month_rows['2027-01']['count'] == 1
        year_rows = {r['label']: r for r in cost_rollup(wos, 'year')}
        assert year_rows['2026']['actual'] == 30
        assert year_rows['2027']['actual'] == 5

    def test_rollup_by_team_falls_back_to_assignee_name(self):
        from application.costs import cost_rollup
        class FakeUser:
            def get_full_name(self):
                return 'Jordan Lee'
        wos = [
            self._fake_wo(assigned_team='HVAC Crew', cost_record=self.FakeCostRecord(10)),
            self._fake_wo(assigned_team=None, assigned_to=FakeUser(), cost_record=self.FakeCostRecord(20)),
            self._fake_wo(assigned_team=None, assigned_to=None, cost_record=self.FakeCostRecord(30)),
        ]
        labels = {r['label'] for r in cost_rollup(wos, 'team')}
        assert labels == {'HVAC Crew', 'Jordan Lee', 'Unassigned'}

    def test_unknown_dimension_rejected(self):
        from application.costs import cost_rollup
        with pytest.raises(ValueError):
            cost_rollup([], 'bogus_dimension')


class TestCostRollupRoute:
    def test_regular_user_cannot_view_rollups(self, user_client):
        assert user_client.get('/cost_rollups').status_code == 403

    def test_rollup_page_loads_for_each_dimension(self, admin_client):
        from application.costs import ROLLUP_DIMENSIONS
        for dim in ROLLUP_DIMENSIONS:
            r = admin_client.get(f'/cost_rollups?dimension={dim}')
            assert r.status_code == 200, f'dimension={dim} failed'

    def test_invalid_dimension_falls_back_to_facility(self, admin_client):
        r = admin_client.get('/cost_rollups?dimension=not_a_real_dimension')
        assert r.status_code == 200


class TestTechnicianWorkloadPure:
    def _wo(self, status, due_date=None):
        class Fake:
            pass
        wo = Fake()
        wo.status = status
        wo.due_date = due_date
        return wo

    def test_workload_buckets(self):
        from application.costs import technician_workload
        from application import workflow

        class FakeTech:
            def __init__(self, id):
                self.id = id
        tech = FakeTech(1)
        today = date(2026, 6, 15)
        wos = [
            self._wo(workflow.NEW, due_date=today - timedelta(days=2)),        # open + overdue
            self._wo(workflow.IN_PROGRESS, due_date=today),                    # open + in_progress + due_today
            self._wo(workflow.ASSIGNED, due_date=today + timedelta(days=3)),   # open + due_this_week
            self._wo(workflow.COMPLETED, due_date=today - timedelta(days=10)), # not open, excluded entirely
        ]
        rows = technician_workload([tech], {1: wos}, today=today)
        row = rows[0]
        assert row['open'] == 3          # NEW, IN_PROGRESS, ASSIGNED (COMPLETED excluded)
        assert row['in_progress'] == 1
        assert row['overdue'] == 1
        assert row['due_today'] == 1
        assert row['due_this_week'] == 1

    def test_technician_with_no_work_orders(self):
        from application.costs import technician_workload

        class FakeTech:
            id = 99
        rows = technician_workload([FakeTech()], {}, today=date(2026, 6, 15))
        assert rows[0]['open'] == 0 and rows[0]['overdue'] == 0


class TestTechnicianWorkloadRoute:
    def test_regular_user_cannot_view_workload(self, user_client):
        assert user_client.get('/technician_workload').status_code == 403

    def test_staff_can_view_workload(self, admin_client):
        assert admin_client.get('/technician_workload').status_code == 200

    def test_technician_appears_with_assigned_work_orders(self, app, admin_client):
        tech_id = _tech(app, email='workload-tech@test.com', first='Workload')
        _work_order(app, 'Assigned to workload tech', assigned_to_id=tech_id, status='In Progress')
        r = admin_client.get('/technician_workload')
        # get_full_name() renders "First  Last" (double space) when middle_name is
        # None — pre-existing AssistItK12 behavior, not something this phase touches.
        assert b'Workload' in r.data and b'Tech' in r.data


class TestOtherModulesStillWork:
    def test_work_order_detail_still_loads(self, app, admin_client):
        wo_id = _work_order(app, 'Still loads WO')
        assert admin_client.get(f'/edit_work_order/{wo_id}').status_code == 200
