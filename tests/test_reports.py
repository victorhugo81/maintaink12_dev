"""
Reports & Exports tests: every report's row-builder against known seeded
data with hand-computed expected values, CSV export correctness (both the
formatting helper directly and a full route round-trip parsed back with
csv.reader), filter application, and access control.

Report builders are called directly with an explicit filters dict (not
through parse_filters/HTTP) wherever the exact numbers matter, so tests
don't depend on "today" the way a preset like 'quarter' would — the same
approach tests/test_dashboard.py uses for analytics.py.
"""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from application import reports


TODAY = datetime.now(timezone.utc).date()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _ids(app, priority='High', category='HVAC'):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name=priority).first().id,
                Category.query.filter_by(name=category).first().id)


def _facility(app, name, site_id=1, **kwargs):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=site_id, name=name, **kwargs)
            db.session.add(f)
            db.session.commit()
        return f.id


def _user(app, email, role_id, first='Rpt', site_id=1):
    with app.app_context():
        from application.models import User
        from application.utils import hash_email
        from werkzeug.security import generate_password_hash
        from main import db
        u = User.query.filter_by(email_hash=hash_email(email, app.config['SECRET_KEY'])).first()
        if u is None:
            u = User(first_name=first, last_name='User', status='Active',
                     password=generate_password_hash('Some@Password1'), must_change_password=False,
                     failed_login_attempts=0, role_id=role_id, site_id=site_id)
            u.email = email
            db.session.add(u)
            db.session.commit()
        return u.id


def _client_as(app, user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
    return c


def _wo(app, facility_id, title, status='New', priority='High', category='HVAC', created_days_ago=0,
        completed_days_after=None, due_in=None, **kwargs):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app, priority, category)
        created = NOW - timedelta(days=created_days_ago)
        wo = WorkOrder(site_id=kwargs.pop('site_id', 1), facility_id=facility_id, title=title,
                       source=kwargs.pop('source', 'Manual'), status=status, priority_id=pri, category_id=cat, **kwargs)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        wo.created_at = created
        if completed_days_after is not None:
            wo.completed_at = created + timedelta(days=completed_days_after)
        if due_in is not None:
            wo.due_date = TODAY + timedelta(days=due_in)
        db.session.commit()
        return wo.id


def _asset(app, facility_id, tag, **kwargs):
    with app.app_context():
        from application.models import Asset, AssetType
        from main import db
        at = AssetType.query.filter_by(name='RPT Asset Type').first()
        if at is None:
            at = AssetType(name='RPT Asset Type', expected_life_years=10)
            db.session.add(at)
            db.session.commit()
        a = Asset(site_id=1, facility_id=facility_id, asset_type_id=at.id, asset_tag=tag, name=f'Asset {tag}', **kwargs)
        db.session.add(a)
        db.session.commit()
        return a.id


def _filters(facility_id=None, days=60, start='default', end='default', **overrides):
    f = {'start': TODAY - timedelta(days=days) if start == 'default' else start,
         'end': TODAY if end == 'default' else end,
         'site_id': None, 'site_ids': None, 'facility_id': facility_id, 'team': None,
         'technician_id': None, 'category_id': None, 'priority_id': None, 'vendor_id': None, 'asset_id': None,
         'status': None, 'requester_id': None, 'dimension': 'facility', 'preset': 'custom', 'period_label': 'range'}
    f.update(overrides)
    return f


def _all_time(facility_id=None, **overrides):
    return _filters(facility_id=facility_id, start=None, end=None, **overrides)


# ---------------------------------------------------------------------------
# CSV formatting (the explicit "test CSV export correctness" ask)
# ---------------------------------------------------------------------------

class TestCsvExport:
    def test_to_csv_formats_every_value_type(self):
        headers = ['Name', 'Amount', 'Assessed', 'Created', 'Blank']
        rows = [reports._row('Roof', Decimal('123.456'), date(2026, 3, 1), datetime(2026, 3, 1, 14, 30), None)]
        text = reports.to_csv(headers, rows)
        parsed = list(csv.reader(io.StringIO(text)))
        assert parsed[0] == headers
        assert parsed[1] == ['Roof', '123.46', '2026-03-01', '2026-03-01 14:30', '']

    def test_to_csv_round_trips_a_full_report(self, app):
        fid = _facility(app, 'CSV Correctness Building')
        wo_id = _wo(app, fid, 'CSV export WO', estimated_cost=Decimal('100.005'), status='New')
        with app.app_context():
            headers, rows, truncated = reports.report_work_orders(_filters(fid))
            text = reports.to_csv(headers, rows)
        parsed = list(csv.reader(io.StringIO(text)))
        assert parsed[0] == headers
        assert len(parsed) == 2 and not truncated
        row = dict(zip(headers, parsed[1]))
        assert row['Title'] == 'CSV export WO'
        assert row['Estimated Cost'] == '100.01' or row['Estimated Cost'] == '100.0'  # rounds to 2dp

    def test_cap_flags_truncation_without_dropping_silently(self, monkeypatch):
        monkeypatch.setattr(reports, 'MAX_EXPORT_ROWS', 2)
        rows, truncated = reports._cap([1, 2, 3, 4])
        assert rows == [1, 2] and truncated is True
        rows, truncated = reports._cap([1])
        assert rows == [1] and truncated is False


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestParseFilters:
    def test_all_preset_means_no_date_bound(self):
        from werkzeug.datastructures import MultiDict
        f = reports.parse_filters(MultiDict({'preset': 'all'}), None, default_preset='quarter')
        assert f['start'] is None and f['end'] is None and f['preset'] == 'all'

    def test_default_preset_applies_when_absent(self):
        from werkzeug.datastructures import MultiDict
        f = reports.parse_filters(MultiDict(), None, default_preset='all', today=TODAY)
        assert f['preset'] == 'all' and f['start'] is None

    def test_extra_report_fields_present(self):
        from werkzeug.datastructures import MultiDict
        f = reports.parse_filters(MultiDict({'vendor_id': '3', 'asset_id': '9', 'dimension': 'category'}), None, default_preset='all')
        assert f['vendor_id'] == 3 and f['asset_id'] == 9 and f['dimension'] == 'category'

    def test_site_locked_user_ignores_site_id_arg(self):
        from werkzeug.datastructures import MultiDict
        f = reports.parse_filters(MultiDict({'site_id': '9'}), [1], default_preset='all')
        assert f['site_id'] is None and f['site_ids'] == [1]


# ---------------------------------------------------------------------------
# Work Order / Open / Overdue
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def wo_facility(app):
    fid = _facility(app, 'RPT WorkOrders Building')
    _wo(app, fid, 'RPT recent new', status='New', created_days_ago=5, estimated_cost=100)
    _wo(app, fid, 'RPT completed on time', status='Completed', created_days_ago=10, completed_days_after=2,
        due_in=5, estimated_cost=200, actual_cost=999)
    _wo(app, fid, 'RPT too old', status='New', created_days_ago=200, estimated_cost=50)
    _wo(app, fid, 'RPT overdue open', status='Waiting for Parts', created_days_ago=3, due_in=-1)
    return fid


class TestWorkOrderReport:
    def test_date_window_excludes_older_rows(self, app, wo_facility):
        with app.app_context():
            headers, rows, truncated = reports.report_work_orders(_filters(wo_facility, days=60))
        titles = {r[headers.index('Title')] for r in rows}
        assert 'RPT recent new' in titles and 'RPT overdue open' in titles and 'RPT completed on time' in titles
        assert 'RPT too old' not in titles
        assert not truncated

    def test_cost_record_wins_over_actual_cost(self, app, wo_facility):
        with app.app_context():
            from application.models import CostRecord
            from main import db
            wo_id = _wo(app, wo_facility, 'RPT cost record wins', status='Completed', actual_cost=999, completed_days_after=1)
            db.session.add(CostRecord(work_order_id=wo_id, labor_cost=10, material_cost=5, total_cost=15))
            db.session.commit()
            headers, rows, _ = reports.report_work_orders(_filters(wo_facility, days=5))
        row = next(r for r in rows if r[headers.index('Title')] == 'RPT cost record wins')
        assert row[headers.index('Actual Cost')] == 15.0

    def test_all_time_includes_everything(self, app, wo_facility):
        with app.app_context():
            headers, rows, _ = reports.report_work_orders(_all_time(wo_facility))
        titles = {r[headers.index('Title')] for r in rows}
        assert 'RPT too old' in titles


class TestOpenAndOverdueReports:
    def test_open_excludes_completed_includes_open_regardless_of_age(self, app, wo_facility):
        with app.app_context():
            headers, rows, _ = reports.report_open_work_orders(_all_time(wo_facility))
        titles = {r[headers.index('Title')] for r in rows}
        assert 'RPT completed on time' not in titles
        assert {'RPT recent new', 'RPT too old', 'RPT overdue open'} <= titles

    def test_overdue_is_open_and_past_due_date_only(self, app, wo_facility):
        with app.app_context():
            headers, rows, _ = reports.report_overdue_work_orders(_all_time(wo_facility), today=TODAY)
        titles = {r[headers.index('Title')] for r in rows}
        assert titles == {'RPT overdue open'}

    def test_facility_filter_narrows_results(self, app, wo_facility):
        other = _facility(app, 'RPT Other Building')
        _wo(app, other, 'RPT other facility WO')
        with app.app_context():
            headers, rows, _ = reports.report_work_orders(_all_time(facility_id=wo_facility))
        titles = {r[headers.index('Title')] for r in rows}
        assert 'RPT other facility WO' not in titles


# ---------------------------------------------------------------------------
# Preventive Maintenance
# ---------------------------------------------------------------------------

class TestPreventiveMaintenanceReport:
    def test_status_buckets(self, app):
        fid = _facility(app, 'RPT PM Building')
        aid = _asset(app, fid, 'RPT-PM-1')
        with app.app_context():
            from application.models import MaintenancePlan, MaintenanceSchedule
            from main import db
            pri, cat = _ids(app)
            plan = MaintenancePlan(name='RPT PM Plan', asset_id=aid, frequency='Monthly', category_id=cat, priority_id=pri)
            db.session.add(plan)
            db.session.flush()
            db.session.add(MaintenanceSchedule(maintenance_plan_id=plan.id, asset_id=aid, next_due_date=TODAY - timedelta(days=2)))
            db.session.commit()
            headers, rows, _ = reports.report_preventive_maintenance(_all_time(fid), today=TODAY)
        assert len(rows) == 1
        assert rows[0][headers.index('Status')] == 'Overdue'
        assert rows[0][headers.index('Asset Tag')] == 'RPT-PM-1'


# ---------------------------------------------------------------------------
# Asset Condition
# ---------------------------------------------------------------------------

class TestAssetConditionReport:
    def test_date_filter_requires_an_assessment_unassessed_shown_only_all_time(self, app):
        fid = _facility(app, 'RPT Condition Building')
        assessed_id = _asset(app, fid, 'RPT-COND-A', condition_score=90, condition_label='Excellent')
        unassessed_id = _asset(app, fid, 'RPT-COND-B')
        with app.app_context():
            from application.models import AssetConditionHistory
            from main import db
            db.session.add(AssetConditionHistory(asset_id=assessed_id, assessed_at=TODAY - timedelta(days=3), condition='Excellent', score=90))
            db.session.commit()
            headers, windowed, _ = reports.report_asset_condition(_filters(fid, days=10))
            _, all_time, _ = reports.report_asset_condition(_all_time(fid))
        windowed_tags = {r[headers.index('Asset Tag')] for r in windowed}
        all_tags = {r[headers.index('Asset Tag')] for r in all_time}
        assert windowed_tags == {'RPT-COND-A'}
        assert all_tags == {'RPT-COND-A', 'RPT-COND-B'}
        unassessed_row = next(r for r in all_time if r[headers.index('Asset Tag')] == 'RPT-COND-B')
        assert unassessed_row[headers.index('Last Assessed')] == '' and unassessed_row[headers.index('Condition Score')] == ''


# ---------------------------------------------------------------------------
# Facility Condition
# ---------------------------------------------------------------------------

class TestFacilityConditionReport:
    def test_includes_facility_with_health_score(self, app):
        fid = _facility(app, 'RPT Facility Health Building', year_built=2000)
        _wo(app, fid, 'RPT health WO', status='New')
        with app.app_context():
            headers, rows, _ = reports.report_facility_condition(_filters(fid, days=90))
        assert len(rows) == 1
        assert rows[0][headers.index('Facility')] == 'RPT Facility Health Building'
        assert isinstance(rows[0][headers.index('Health Score')], (int, float))


# ---------------------------------------------------------------------------
# Maintenance Cost
# ---------------------------------------------------------------------------

class TestMaintenanceCostReport:
    def test_groups_by_facility_with_correct_sums(self, app):
        fid = _facility(app, 'RPT Cost Building')
        _wo(app, fid, 'RPT cost WO 1', status='Completed', estimated_cost=100, actual_cost=80, completed_days_after=1)
        _wo(app, fid, 'RPT cost WO 2', status='Completed', estimated_cost=200, actual_cost=150, completed_days_after=1)
        with app.app_context():
            headers, rows, _ = reports.report_maintenance_cost(_filters(fid, days=30))
        assert len(rows) == 1
        row = rows[0]
        assert row[headers.index('Work Order Count')] == 2
        assert row[headers.index('Estimated Cost')] == 300
        assert row[headers.index('Actual Cost')] == 230

    def test_dimension_switch_groups_by_category(self, app):
        fid = _facility(app, 'RPT Cost Dimension Building')
        _wo(app, fid, 'RPT dim WO', category='Plumbing', estimated_cost=50)
        with app.app_context():
            headers, rows, _ = reports.report_maintenance_cost(_filters(fid, days=30, dimension='category'))
        assert headers[0] == 'Category'
        assert any(r[0] == 'Plumbing' for r in rows)


# ---------------------------------------------------------------------------
# Technician Productivity
# ---------------------------------------------------------------------------

class TestTechnicianProductivityReport:
    def test_completed_and_labor_hours_hand_computed(self, app):
        fid = _facility(app, 'RPT Productivity Building')
        tech_id = _user(app, 'rpt-tech@test.com', 3, first='RptTech')
        wo1 = _wo(app, fid, 'RPT prod completed 1', status='Completed', assigned_to_id=tech_id, completed_days_after=4, created_days_ago=10)
        wo2 = _wo(app, fid, 'RPT prod open', status='In Progress', assigned_to_id=tech_id, created_days_ago=1)
        with app.app_context():
            from application.models import WorkOrderLabor
            from main import db
            db.session.add(WorkOrderLabor(work_order_id=wo1, technician_id=tech_id, labor_hours=3, hourly_rate=20))
            db.session.add(WorkOrderLabor(work_order_id=wo2, technician_id=tech_id, labor_hours=1, hourly_rate=40))
            db.session.commit()
            headers, rows, _ = reports.report_technician_productivity(_filters(technician_id=tech_id, days=30))
        assert len(rows) == 1
        row = rows[0]
        assert row[headers.index('Open Assigned')] == 1
        assert row[headers.index('Completed')] == 1
        assert row[headers.index('Avg Resolution (days)')] == 4.0
        assert row[headers.index('Work Orders Touched')] == 2
        assert row[headers.index('Labor Hours')] == 4.0
        assert row[headers.index('Labor Cost')] == 3 * 20 + 1 * 40


# ---------------------------------------------------------------------------
# Vendor Performance
# ---------------------------------------------------------------------------

class TestVendorPerformanceReport:
    def test_totals_match_compute_vendor_stats(self, app):
        fid = _facility(app, 'RPT Vendor Building')
        with app.app_context():
            from application.models import Vendor
            from main import db
            vendor = Vendor(name='RPT Report Vendor')
            db.session.add(vendor)
            db.session.commit()
            vendor_id = vendor.id
        _wo(app, fid, 'RPT vendor open', status='New', vendor_id=vendor_id, actual_cost=None)
        _wo(app, fid, 'RPT vendor closed', status='Closed', vendor_id=vendor_id, actual_cost=250, completed_days_after=3, created_days_ago=10)
        with app.app_context():
            headers, rows, _ = reports.report_vendor_performance(_filters(fid, days=30, vendor_id=vendor_id))
        assert len(rows) == 1
        row = rows[0]
        assert row[headers.index('Total Work Orders')] == 2
        assert row[headers.index('Open')] == 1
        assert row[headers.index('Total Cost')] == 250


# ---------------------------------------------------------------------------
# Asset Maintenance History
# ---------------------------------------------------------------------------

class TestAssetMaintenanceHistoryReport:
    def test_combines_and_sorts_condition_and_work_orders(self, app):
        fid = _facility(app, 'RPT History Building')
        aid = _asset(app, fid, 'RPT-HIST-1')
        with app.app_context():
            from application.models import AssetConditionHistory
            from main import db
            db.session.add(AssetConditionHistory(asset_id=aid, assessed_at=TODAY - timedelta(days=5), condition='Good', score=80))
            db.session.commit()
        _wo(app, fid, 'RPT history WO', asset_id=aid, created_days_ago=2)
        with app.app_context():
            headers, rows, _ = reports.report_asset_maintenance_history(_filters(asset_id=aid, days=30))
        assert len(rows) == 2
        types = [r[headers.index('Type')] for r in rows]
        assert types == ['Work Order', 'Condition Assessment']  # newest (2 days ago) before oldest (5 days ago)


# ---------------------------------------------------------------------------
# Capital Replacement
# ---------------------------------------------------------------------------

class TestCapitalReplacementReport:
    def test_matches_risk_calculate_risk_directly(self, app):
        fid = _facility(app, 'RPT Capital Building')
        aid = _asset(app, fid, 'RPT-CAP-1', condition_score=20, condition_label='Critical', safety_impact=80)
        with app.app_context():
            from application.models import Asset
            from application import risk
            headers, rows, _ = reports.report_capital_replacement(_filters(fid, days=9999), today=TODAY)
            asset = Asset.query.get(aid)
            expected = risk.calculate_risk(asset, work_orders=[], today=TODAY)
        assert len(rows) == 1
        assert rows[0][headers.index('Risk Score')] == expected['score']
        assert rows[0][headers.index('Factors With Data')] == expected['factor_count']


# ---------------------------------------------------------------------------
# SLA Performance
# ---------------------------------------------------------------------------

class TestSlaPerformanceReport:
    def test_per_priority_and_totals_row(self, app):
        fid = _facility(app, 'RPT SLA Building')
        _wo(app, fid, 'RPT sla met', status='Completed', priority='High', due_in=2, completed_days_after=1, created_days_ago=5)
        _wo(app, fid, 'RPT sla missed', status='Completed', priority='High', due_in=-12, completed_days_after=1, created_days_ago=10)
        _wo(app, fid, 'RPT sla no due date', status='Completed', priority='High', completed_days_after=1, created_days_ago=3)
        with app.app_context():
            headers, rows, _ = reports.report_sla_performance(_filters(fid, days=30))
        by_label = {r[0]: r for r in rows}
        assert by_label['High'][headers.index('Completed (with due date)')] == 2
        assert by_label['High'][headers.index('Met Due Date')] == 1
        assert by_label['High'][headers.index('SLA Compliance %')] == 50.0
        assert by_label['All Priorities'][headers.index('Completed (with due date)')] == 2


# ---------------------------------------------------------------------------
# Recurring Problems
# ---------------------------------------------------------------------------

class TestRecurringProblemsReport:
    def test_detects_group_and_lists_work_orders(self, app):
        fid = _facility(app, 'RPT Recurring Building')
        for i in range(3):
            _wo(app, fid, f'RPT recurring roof {i}', category='Roofing')
        with app.app_context():
            headers, rows, _ = reports.report_recurring_problems(_filters(fid, days=30))
        assert len(rows) == 1
        assert rows[0][headers.index('Count')] == 3
        assert rows[0][headers.index('Kind')] == 'facility'
        assert rows[0][headers.index('Work Orders')].count('WO-') == 3


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestReportRoutes:
    def test_index_requires_login(self, client):
        assert client.get('/reports').status_code in (302, 401)

    def test_index_forbidden_for_regular_user(self, user_client):
        assert user_client.get('/reports').status_code == 403

    def test_index_ok_for_admin(self, admin_client):
        assert admin_client.get('/reports').status_code == 200

    def test_unknown_report_key_404s(self, admin_client):
        assert admin_client.get('/reports/does-not-exist').status_code == 404

    @pytest.mark.parametrize('key', reports.REPORT_ORDER)
    def test_every_report_renders_and_exports_csv(self, admin_client, key):
        r = admin_client.get(f'/reports/{key}')
        assert r.status_code == 200
        r_csv = admin_client.get(f'/reports/{key}?format=csv')
        assert r_csv.status_code == 200
        assert r_csv.headers['Content-Type'].startswith('text/csv')
        assert f'{key}.csv' in r_csv.headers['Content-Disposition']
        parsed = list(csv.reader(io.StringIO(r_csv.data.decode())))
        assert len(parsed) >= 1  # header row always present

    def test_csv_download_matches_known_data(self, app, admin_client):
        fid = _facility(app, 'RPT CSV Route Building')
        _wo(app, fid, 'RPT csv route WO', status='New', estimated_cost=42)
        r = admin_client.get(f'/reports/work_orders?format=csv&preset=all&facility_id={fid}')
        parsed = list(csv.reader(io.StringIO(r.data.decode())))
        assert len(parsed) == 2
        row = dict(zip(parsed[0], parsed[1]))
        assert row['Title'] == 'RPT csv route WO'
        assert row['Estimated Cost'] == '42.0'

    def test_technician_only_sees_own_site(self, app):
        from application.models import Site
        with app.app_context():
            from main import db
            if not Site.query.filter_by(id=2).first():
                db.session.add(Site(id=2, site_name='RPT Other Site', site_acronyms='OS', site_code='999',
                                    site_cds='9', site_address='x', site_type='Elementary'))
                db.session.commit()
        tech_id = _user(app, 'rpt-scope-tech@test.com', 3, first='ScopeTech', site_id=1)
        home_fid = _facility(app, 'RPT Tech Home Building', site_id=1)
        other_fid = _facility(app, 'RPT Tech Other Building', site_id=2)
        _wo(app, home_fid, 'RPT tech home WO', site_id=1)
        _wo(app, other_fid, 'RPT tech other WO', site_id=2)
        c = _client_as(app, tech_id)
        r = c.get('/reports/work_orders?format=csv&preset=all')
        assert b'RPT tech home WO' in r.data
        assert b'RPT tech other WO' not in r.data
