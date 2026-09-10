"""
Vendor tests: CRUD, linking to work orders, and performance/cost aggregation
(work order counts, total cost, average completion time, contract/insurance/
license expiration status).
"""
from datetime import date, timedelta
import pytest


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='HVAC').first().id)


def _facility(app):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name='Vendor Test Building').first()
        if f is None:
            f = Facility(site_id=1, name='Vendor Test Building')
            db.session.add(f)
            db.session.commit()
        return f.id


def _make_vendor(app, name='Acme HVAC Services', **kwargs):
    with app.app_context():
        from application.models import Vendor
        from main import db
        v = Vendor.query.filter_by(name=name).first()
        if v is None:
            v = Vendor(name=name, **kwargs)
            db.session.add(v)
            db.session.commit()
        return v.id


def _make_work_order(app, vendor_id, title, status='New', actual_cost=None, created_days_ago=0, completed_days_ago=None):
    """Create a work order directly via the ORM, linked to a vendor, for aggregation tests."""
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app)
        facility_id = _facility(app)
        now = date.today()
        wo = WorkOrder(
            site_id=1, facility_id=facility_id, title=title,
            source='Manual', status=status, priority_id=pri, category_id=cat,
            vendor_id=vendor_id, actual_cost=actual_cost,
        )
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        wo.created_at = date.today()
        if completed_days_ago is not None:
            from datetime import datetime, timezone
            wo.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
            wo.completed_at = datetime.now(timezone.utc) - timedelta(days=completed_days_ago)
        db.session.commit()
        return wo.id


class TestVendorCRUD:
    def test_regular_user_cannot_view_vendors(self, user_client):
        assert user_client.get('/vendors').status_code == 403

    def test_add_vendor(self, app, admin_client):
        r = admin_client.post('/add_vendor', data={
            'name': 'Acme HVAC Services', 'contact_name': 'Jane Doe', 'phone': '555-1234',
            'email': 'jane@acmehvac.example', 'contract_end_date': '2027-01-01',
            'insurance_expiration': '2026-12-01', 'license_expiration': '2026-10-01',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Vendor
            v = Vendor.query.filter_by(name='Acme HVAC Services').first()
            assert v is not None and v.is_active is True
            assert v.contract_end_date.isoformat() == '2027-01-01'

    def test_duplicate_vendor_name_rejected(self, admin_client):
        r = admin_client.post('/add_vendor', data={'name': 'Acme HVAC Services'}, follow_redirects=True)
        assert b'already exists' in r.data

    def test_edit_vendor(self, app, admin_client):
        with app.app_context():
            from application.models import Vendor
            vendor_id = Vendor.query.filter_by(name='Acme HVAC Services').first().id
        r = admin_client.post(f'/edit_vendor/{vendor_id}', data={
            'name': 'Acme HVAC Services', 'contact_name': 'John Smith', 'is_active': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Vendor
            from main import db
            v = db.session.get(Vendor, vendor_id)
            assert v.contact_name == 'John Smith' and v.is_active is True

    def test_deactivate_vendor_keeps_historical_work_order_link(self, app, admin_client):
        vendor_id = _make_vendor(app, name='Deactivate Me LLC')
        wo_id = _make_work_order(app, vendor_id, 'Roof leak repair')

        r = admin_client.post(f'/delete_vendor/{vendor_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Vendor, WorkOrder
            from main import db
            v = db.session.get(Vendor, vendor_id)
            wo = db.session.get(WorkOrder, wo_id)
            assert v is not None and v.is_active is False  # soft delete, row survives
            assert wo.vendor_id == vendor_id  # historical link untouched


class TestWorkOrderVendorLinking:
    def test_link_vendor_via_staff_edit_form(self, app, admin_client):
        vendor_id = _make_vendor(app, name='Linking Test Vendor')
        wo_id = _make_work_order(app, None, 'Parking lot striping')
        pri, cat = _ids(app)
        facility_id = _facility(app)

        r = admin_client.post(f'/edit_work_order/{wo_id}', data={
            'title': 'Parking lot striping', 'site_id': '1', 'facility_id': str(facility_id),
            'room_id': '0', 'asset_id': '0', 'category_id': str(cat), 'subcategory_id': '0',
            'priority_id': str(pri), 'assigned_to_id': '0', 'assigned_team': '',
            'vendor_id': str(vendor_id),
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            assert wo.vendor_id == vendor_id

    def test_regular_user_cannot_set_vendor(self, app, user_client):
        vendor_id = _make_vendor(app, name='Access Control Vendor')
        wo_id = _make_work_order(app, None, 'Regular user cannot touch this')
        r = user_client.post(f'/edit_work_order/{wo_id}', data={'vendor_id': str(vendor_id)})
        assert r.status_code == 403


class TestVendorPerformance:
    def test_regular_user_cannot_view_performance(self, user_client):
        assert user_client.get('/vendor_performance').status_code == 403

    def test_staff_can_view_performance(self, admin_client):
        assert admin_client.get('/vendor_performance').status_code == 200

    def test_cost_and_count_aggregation(self, app, admin_client):
        vendor_id = _make_vendor(app, name='Aggregation Test Vendor')
        _make_work_order(app, vendor_id, 'AggTest WO 1', status='New', actual_cost=100)
        _make_work_order(app, vendor_id, 'AggTest WO 2', status='Completed', actual_cost=250)
        _make_work_order(app, vendor_id, 'AggTest WO 3', status='In Progress', actual_cost=None)

        r = admin_client.get('/vendor_performance')
        assert r.status_code == 200
        assert b'Aggregation Test Vendor' in r.data
        assert b'350.00' in r.data  # 100 + 250 + (None treated as 0)

        with app.app_context():
            from application.models import WorkOrder
            from application.vendors import compute_vendor_stats
            wos = WorkOrder.query.filter_by(vendor_id=vendor_id).all()
            stats = compute_vendor_stats(wos)
            assert stats['total_count'] == 3
            assert stats['open_count'] == 2  # New + In Progress are open; Completed is not
            assert stats['total_cost'] == 350

    def test_average_completion_time(self, app):
        vendor_id = _make_vendor(app, name='Completion Time Vendor')
        _make_work_order(app, vendor_id, 'Completed in 2 days', status='Completed',
                         created_days_ago=2, completed_days_ago=0)
        _make_work_order(app, vendor_id, 'Completed in 4 days', status='Completed',
                         created_days_ago=4, completed_days_ago=0)
        _make_work_order(app, vendor_id, 'Still open, excluded from average', status='New')

        with app.app_context():
            from application.models import WorkOrder
            from application.vendors import compute_vendor_stats
            wos = WorkOrder.query.filter_by(vendor_id=vendor_id).all()
            stats = compute_vendor_stats(wos)
            assert stats['avg_completion_days'] == 3.0  # (2 + 4) / 2, the open one excluded

    def test_no_work_orders_yields_no_average_not_zero(self):
        """A vendor with zero completed work orders should show 'no data', not a misleading 0."""
        from application.vendors import compute_vendor_stats
        stats = compute_vendor_stats([])
        assert stats['total_count'] == 0
        assert stats['avg_completion_days'] is None


class TestExpirationStatus:
    def test_no_date_returns_none(self):
        from application.vendors import expiration_status
        assert expiration_status(None) is None

    def test_past_date_is_expired(self):
        from application.vendors import expiration_status
        today = date(2026, 6, 15)
        assert expiration_status(date(2026, 6, 14), today=today) == 'expired'

    def test_today_is_not_yet_expired(self):
        from application.vendors import expiration_status
        today = date(2026, 6, 15)
        assert expiration_status(today, today=today) == 'expiring_soon'

    def test_within_window_is_expiring_soon(self):
        from application.vendors import expiration_status
        today = date(2026, 6, 15)
        assert expiration_status(date(2026, 7, 1), today=today) == 'expiring_soon'  # 16 days out

    def test_exact_boundary_is_expiring_soon(self):
        from application.vendors import expiration_status
        today = date(2026, 6, 15)
        assert expiration_status(today + timedelta(days=30), today=today) == 'expiring_soon'

    def test_beyond_window_is_ok(self):
        from application.vendors import expiration_status
        today = date(2026, 6, 15)
        assert expiration_status(today + timedelta(days=31), today=today) == 'ok'

    def test_expiring_vendor_flagged_on_performance_page(self, app, admin_client):
        vendor_id = _make_vendor(app, name='Expiring Contract Vendor',
                                 contract_end_date=date.today() + timedelta(days=5))
        r = admin_client.get('/vendor_performance')
        assert b'Expiring Contract Vendor' in r.data
        assert b'bg-warning' in r.data


class TestOtherModulesStillWork:
    """Vendors touched the existing work_order table — confirm nothing else broke."""
    def test_work_order_list_still_loads(self, admin_client):
        assert admin_client.get('/work_orders').status_code == 200
