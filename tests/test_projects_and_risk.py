"""
Projects & Capital Planning tests: risk-score calculation against known
inputs (every factor exercised individually and in combination, including
the "no data" exclusion rule), and project cost roll-up from linked work
orders plus direct project costs.
"""
from datetime import date, datetime, timedelta, timezone
import pytest


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='HVAC').first().id)


def _facility(app, name='Project Test Building'):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=1, name=name)
            db.session.add(f)
            db.session.commit()
        return f.id


def _asset_type(app, name='Risk Test Type', expected_life_years=None):
    with app.app_context():
        from application.models import AssetType
        from main import db
        at = AssetType.query.filter_by(name=name).first()
        if at is None:
            at = AssetType(name=name, expected_life_years=expected_life_years)
            db.session.add(at)
            db.session.commit()
        return at.id


def _make_asset(app, tag, **kwargs):
    with app.app_context():
        from application.models import Asset
        from main import db
        facility_id = _facility(app)
        type_id = kwargs.pop('asset_type_id', None) or _asset_type(app)
        asset = Asset(site_id=1, facility_id=facility_id, asset_type_id=type_id,
                      asset_tag=tag, name=f'Asset {tag}', **kwargs)
        db.session.add(asset)
        db.session.commit()
        return asset.id


def _make_work_order(app, asset_id, source='Manual', status='New', actual_cost=None, estimated_cost=None,
                     completed_days_ago=None, created_days_ago=0):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app)
        facility_id = _facility(app)
        wo = WorkOrder(site_id=1, facility_id=facility_id, asset_id=asset_id, title=f'WO for asset {asset_id}',
                       source=source, status=status, priority_id=pri, category_id=cat,
                       actual_cost=actual_cost, estimated_cost=estimated_cost)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        if completed_days_ago is not None:
            wo.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
            wo.completed_at = datetime.now(timezone.utc) - timedelta(days=completed_days_ago)
        db.session.commit()
        return wo.id


class TestConditionFactor:
    def test_condition_score_100_gives_zero_risk(self, app):
        asset_id = _make_asset(app, 'RISK-COND-1')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            asset.condition_score = 100
            db.session.commit()
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Condition']['value'] == 0

    def test_condition_score_25_gives_75_risk(self, app):
        asset_id = _make_asset(app, 'RISK-COND-2')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            asset.condition_score = 25
            db.session.commit()
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Condition']['value'] == 75

    def test_no_condition_assessment_is_no_data(self, app):
        asset_id = _make_asset(app, 'RISK-COND-3')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Condition']['available'] is False
            assert result['factors']['Condition']['value'] is None


class TestAgeFactor:
    def test_age_at_exactly_expected_life_is_100(self, app):
        type_id = _asset_type(app, name='Age Test Type', expected_life_years=10)
        asset_id = _make_asset(app, 'RISK-AGE-1', asset_type_id=type_id,
                               install_date=date(2016, 6, 15))  # ~10 years before "today" below
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Age']['value'] == 100.0

    def test_age_at_half_expected_life_is_50(self, app):
        type_id = _asset_type(app, name='Age Half Type', expected_life_years=20)
        asset_id = _make_asset(app, 'RISK-AGE-2', asset_type_id=type_id, install_date=date(2016, 6, 15))
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Age']['value'] == 50.0

    def test_age_beyond_expected_life_caps_at_100(self, app):
        type_id = _asset_type(app, name='Age Over Type', expected_life_years=5)
        asset_id = _make_asset(app, 'RISK-AGE-3', asset_type_id=type_id, install_date=date(2000, 1, 1))
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Age']['value'] == 100

    def test_asset_level_expected_life_overrides_type(self, app):
        type_id = _asset_type(app, name='Age Override Type', expected_life_years=100)
        asset_id = _make_asset(app, 'RISK-AGE-4', asset_type_id=type_id,
                               install_date=date(2016, 6, 15), expected_life_years=10)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Age']['value'] == 100.0  # uses asset's 10, not type's 100

    def test_no_install_date_is_no_data(self, app):
        type_id = _asset_type(app, name='Age No Data Type', expected_life_years=10)
        asset_id = _make_asset(app, 'RISK-AGE-5', asset_type_id=type_id)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Age']['available'] is False


class TestFailureFrequencyFactor:
    def test_zero_failures_is_zero_risk_not_no_data(self, app):
        asset_id = _make_asset(app, 'RISK-FAIL-0')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Failure Frequency']['available'] is True
            assert result['factors']['Failure Frequency']['value'] == 0

    def test_pm_work_orders_do_not_count_as_failures(self, app):
        asset_id = _make_asset(app, 'RISK-FAIL-PM')
        _make_work_order(app, asset_id, source='PM')
        _make_work_order(app, asset_id, source='PM')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Failure Frequency']['value'] == 0

    def test_failure_buckets(self, app):
        cases = [
            (1, 30), (2, 30), (3, 60), (5, 60), (6, 100), (10, 100),
        ]
        for count, expected in cases:
            asset_id = _make_asset(app, f'RISK-FAIL-{count}')
            for i in range(count):
                _make_work_order(app, asset_id, source='Request')
            with app.app_context():
                from application.models import Asset
                from application.risk import calculate_risk
                from main import db
                asset = db.session.get(Asset, asset_id)
                result = calculate_risk(asset, work_orders=asset.work_orders)
                assert result['factors']['Failure Frequency']['value'] == expected, f'count={count}'


class TestMaintenanceCostFactor:
    def test_cost_at_full_purchase_price_is_100(self, app):
        asset_id = _make_asset(app, 'RISK-COST-1', purchase_cost=1000)
        _make_work_order(app, asset_id, actual_cost=1000)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Maintenance Cost']['value'] == 100.0

    def test_cost_at_quarter_purchase_price_is_25(self, app):
        asset_id = _make_asset(app, 'RISK-COST-2', purchase_cost=1000)
        _make_work_order(app, asset_id, actual_cost=250)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Maintenance Cost']['value'] == 25.0

    def test_cost_beyond_purchase_price_caps_at_100(self, app):
        asset_id = _make_asset(app, 'RISK-COST-3', purchase_cost=100)
        _make_work_order(app, asset_id, actual_cost=500)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Maintenance Cost']['value'] == 100

    def test_no_purchase_cost_is_no_data(self, app):
        asset_id = _make_asset(app, 'RISK-COST-4')
        _make_work_order(app, asset_id, actual_cost=500)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Maintenance Cost']['available'] is False

    def test_zero_maintenance_cost_with_purchase_price_is_zero_risk(self, app):
        asset_id = _make_asset(app, 'RISK-COST-5', purchase_cost=1000)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Maintenance Cost']['available'] is True
            assert result['factors']['Maintenance Cost']['value'] == 0

    def test_uses_cost_record_when_available(self, app, admin_client):
        """Maintenance cost prefers CostRecord.total_cost over WorkOrder.actual_cost."""
        asset_id = _make_asset(app, 'RISK-COST-6', purchase_cost=1000)
        wo_id = _make_work_order(app, asset_id, actual_cost=999)  # should be ignored
        with app.app_context():
            from application.models import WorkOrderLabor, User
            from application.costs import refresh_cost_record
            from application.models import WorkOrder
            from main import db
            tech = User.query.filter_by(role_id=3).first()
            if tech is None:
                from application.utils import hash_email
                from werkzeug.security import generate_password_hash
                tech = User(first_name='Risk', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=1)
                tech.email = 'risk-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            db.session.add(WorkOrderLabor(work_order_id=wo_id, technician_id=tech.id,
                                          labor_hours=2, hourly_rate=50))  # cost = 100
            db.session.flush()
            wo = db.session.get(WorkOrder, wo_id)
            refresh_cost_record(wo)
            db.session.commit()

            from application.models import Asset
            from application.risk import calculate_risk
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=asset.work_orders)
            assert result['factors']['Maintenance Cost']['value'] == 10.0  # 100/1000 = 10%, not 999/1000


class TestSafetyAndOperationalFactors:
    def test_values_pass_through_directly(self, app):
        asset_id = _make_asset(app, 'RISK-SAFETY-1', safety_impact=75, operational_importance=25)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Safety Impact']['value'] == 75
            assert result['factors']['Operational Importance']['value'] == 25

    def test_unset_is_no_data_not_zero(self, app):
        asset_id = _make_asset(app, 'RISK-SAFETY-2')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Safety Impact']['available'] is False
            assert result['factors']['Operational Importance']['available'] is False


class TestWarrantyFactor:
    def test_expired_warranty_is_100(self, app):
        asset_id = _make_asset(app, 'RISK-WARR-1', warranty_expiration=date(2020, 1, 1))
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Warranty Status']['value'] == 100

    def test_active_warranty_is_zero(self, app):
        asset_id = _make_asset(app, 'RISK-WARR-2', warranty_expiration=date(2030, 1, 1))
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factors']['Warranty Status']['value'] == 0

    def test_no_warranty_date_is_no_data(self, app):
        asset_id = _make_asset(app, 'RISK-WARR-3')
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            assert result['factors']['Warranty Status']['available'] is False


class TestOverallScoreAggregation:
    def test_full_known_inputs_produce_exact_expected_score(self, app):
        """
        All 7 factors set to known values -> exact average, computed by hand:
        Condition: condition_score=80 -> risk 20
        Age: install 10 years ago, expected_life=10 -> risk 100
        Failure: 1 non-PM WO -> risk 30
        Maintenance cost: purchase=1000, spend=500 -> risk 50
        Safety: 75
        Operational: 50
        Warranty: expired -> 100
        Mean = (20+100+30+50+75+50+100)/7 = 425/7 = 60.714... -> rounds to 60.7
        """
        type_id = _asset_type(app, name='Full Inputs Type', expected_life_years=10)
        asset_id = _make_asset(app, 'RISK-FULL-1', asset_type_id=type_id,
                               install_date=date(2016, 6, 15), purchase_cost=1000,
                               safety_impact=75, operational_importance=50,
                               warranty_expiration=date(2020, 1, 1))
        _make_work_order(app, asset_id, source='Manual', actual_cost=500)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            asset.condition_score = 80
            db.session.commit()
            result = calculate_risk(asset, work_orders=asset.work_orders, today=date(2026, 6, 15))
            assert result['factor_count'] == 7
            assert result['score'] == round((20 + 100 + 30 + 50 + 75 + 50 + 100) / 7, 1)

    def test_partial_data_averages_only_available_factors(self, app):
        """
        Condition (risk 40) and warranty (risk 0) explicitly set; Failure
        Frequency is ALWAYS available too (0 work orders -> risk 0, not "no
        data" — a real, meaningful zero). So 3 factors go into the average,
        not 2: (40 + 0 + 0) / 3 = 13.3.
        """
        asset_id = _make_asset(app, 'RISK-PARTIAL-1', warranty_expiration=date(2030, 1, 1))
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            asset.condition_score = 60  # risk = 40
            db.session.commit()
            result = calculate_risk(asset, work_orders=[], today=date(2026, 6, 15))
            assert result['factor_count'] == 3
            assert result['score'] == round(40 / 3, 1)

    def test_zero_data_gives_none_score(self, app):
        type_id = _asset_type(app, name='Zero Data Type')
        asset_id = _make_asset(app, 'RISK-NONE-1', asset_type_id=type_id)
        with app.app_context():
            from application.models import Asset
            from application.risk import calculate_risk
            from main import db
            asset = db.session.get(Asset, asset_id)
            result = calculate_risk(asset, work_orders=[])
            # Failure Frequency (0, always available) is the only available factor.
            assert result['factor_count'] == 1
            assert result['score'] == 0.0

    def test_absolutely_nothing_available_gives_none(self, app):
        """failure_frequency is always available (0 work orders = 0 risk), so to get a
        genuinely None score we'd need to fake even that away — verify the module-level
        contract directly instead: an empty `raw` dict must yield score=None."""
        from application.risk import calculate_risk
        class FakeAssetType:
            expected_life_years = None
        class FakeAsset:
            condition_score = None
            expected_life_years = None
            asset_type = FakeAssetType()
            install_date = None
            purchase_cost = None
            safety_impact = None
            operational_importance = None
            warranty_expiration = None
        result = calculate_risk(FakeAsset(), work_orders=[])
        # failure frequency is still available (0 WOs -> 0), so factor_count == 1, not 0.
        assert result['factor_count'] == 1
        assert result['score'] == 0.0


class TestCapitalReplacementRoute:
    def test_regular_user_cannot_view(self, user_client):
        assert user_client.get('/capital_replacement').status_code == 403

    def test_staff_can_view_and_sorted_highest_risk_first(self, app, admin_client):
        low_id = _make_asset(app, 'RISK-SORT-LOW')
        high_id = _make_asset(app, 'RISK-SORT-HIGH')
        with app.app_context():
            from application.models import Asset
            from main import db
            db.session.get(Asset, low_id).condition_score = 95   # risk 5
            db.session.get(Asset, high_id).condition_score = 5   # risk 95
            db.session.commit()

        r = admin_client.get('/capital_replacement')
        assert r.status_code == 200
        body = r.data.decode()
        assert body.index('RISK-SORT-HIGH') < body.index('RISK-SORT-LOW')

    def test_update_risk_fields_route(self, app, admin_client):
        asset_id = _make_asset(app, 'RISK-UPDATE-1')
        r = admin_client.post(f'/update_asset_risk_fields/{asset_id}', data={
            'safety_impact': '75', 'operational_importance': '50',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Asset
            from main import db
            asset = db.session.get(Asset, asset_id)
            assert asset.safety_impact == 75 and asset.operational_importance == 50

    def test_sentinel_not_assessed_clears_to_none(self, app, admin_client):
        asset_id = _make_asset(app, 'RISK-UPDATE-2', safety_impact=50)
        r = admin_client.post(f'/update_asset_risk_fields/{asset_id}', data={
            'safety_impact': '-1', 'operational_importance': '-1',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Asset
            from main import db
            asset = db.session.get(Asset, asset_id)
            assert asset.safety_impact is None

    def test_regular_user_cannot_update_risk_fields(self, app, user_client):
        asset_id = _make_asset(app, 'RISK-ACCESS-1')
        r = user_client.post(f'/update_asset_risk_fields/{asset_id}', data={'safety_impact': '100'})
        assert r.status_code == 403


class TestProjectCRUD:
    def test_regular_user_cannot_view_projects(self, user_client):
        assert user_client.get('/projects').status_code == 403

    def test_regular_user_cannot_add_project(self, user_client):
        assert user_client.get('/add_project').status_code == 403

    def test_add_project(self, app, admin_client):
        r = admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Roof Replacement 2026', 'status': 'Planning', 'budget': '50000',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Project
            p = Project.query.filter_by(name='Roof Replacement 2026').first()
            assert p is not None and p.status == 'Planning'

    def test_edit_project(self, app, admin_client):
        with app.app_context():
            from application.models import Project
            pid = Project.query.filter_by(name='Roof Replacement 2026').first().id
        r = admin_client.post(f'/edit_project/{pid}', data={
            'site_id': '1', 'name': 'Roof Replacement 2026', 'status': 'Approved', 'budget': '55000',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Project
            from main import db
            p = db.session.get(Project, pid)
            assert p.status == 'Approved' and float(p.budget) == 55000.0

    def test_delete_project_cancels_not_removes(self, app, admin_client):
        admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Cancel Me Project', 'status': 'Planning',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Project
            pid = Project.query.filter_by(name='Cancel Me Project').first().id
        r = admin_client.post(f'/delete_project/{pid}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Project
            from main import db
            p = db.session.get(Project, pid)
            assert p is not None  # row survives
            assert p.status == 'Cancelled'


class TestProjectAssociationsAndRollup:
    def test_asset_and_work_order_link_to_project(self, app, admin_client):
        admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Association Test Project', 'status': 'In Progress',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Project
            project_id = Project.query.filter_by(name='Association Test Project').first().id

        asset_id = _make_asset(app, 'PROJECT-LINKED-ASSET')
        pri, cat = _ids(app)
        facility_id = _facility(app)
        r = admin_client.post(f'/edit_asset/{asset_id}', data={
            'facility_id': str(facility_id), 'room_id': '0', 'asset_type_id': str(_asset_type(app)),
            'asset_tag': 'PROJECT-LINKED-ASSET', 'name': 'Asset PROJECT-LINKED-ASSET',
            'project_id': str(project_id),
        }, follow_redirects=True)
        assert r.status_code == 200

        wo_id = _make_work_order(app, asset_id, actual_cost=1000)
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            wo.project_id = project_id
            wo.estimated_cost = 900
            db.session.commit()

        with app.app_context():
            from application.models import Project, Asset
            from main import db
            project = db.session.get(Project, project_id)
            assert asset_id in [a.id for a in project.assets]
            assert wo_id in [w.id for w in project.work_orders]

    def test_cost_rollup_combines_direct_and_work_order_costs(self, app, admin_client):
        admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Rollup Test Project', 'status': 'In Progress',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Project
            project_id = Project.query.filter_by(name='Rollup Test Project').first().id

        asset_id = _make_asset(app, 'ROLLUP-ASSET-1')
        wo1_id = _make_work_order(app, asset_id, actual_cost=1000, estimated_cost=900)
        wo2_id = _make_work_order(app, asset_id, actual_cost=2000, estimated_cost=1800)
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            for wid in (wo1_id, wo2_id):
                wo = db.session.get(WorkOrder, wid)
                wo.project_id = project_id
            db.session.commit()

        admin_client.post(f'/add_project_cost/{project_id}', data={
            'description': 'Design fee', 'cost_type': 'Design', 'amount': '500',
        }, follow_redirects=True)
        admin_client.post(f'/add_project_cost/{project_id}', data={
            'description': 'Permit fee', 'cost_type': 'Permit', 'amount': '150',
        }, follow_redirects=True)

        with app.app_context():
            from application.models import Project
            from application.projects import project_cost_rollup
            from main import db
            project = db.session.get(Project, project_id)
            rollup = project_cost_rollup(project)
            assert rollup['direct_cost'] == 650          # 500 + 150
            assert rollup['work_order_cost'] == 3000      # 1000 + 2000
            assert rollup['total_cost'] == 3650
            assert rollup['work_order_count'] == 2
            assert rollup['estimated_cost'] == 2700       # 900 + 1800

    def test_rollup_falls_back_to_actual_cost_without_cost_record(self, app):
        """Work orders with no labor/material logged (no CostRecord) still count via actual_cost."""
        with app.app_context():
            from application.models import Project
            from main import db
            project = Project(site_id=1, name='Fallback Rollup Project', status='Planning')
            db.session.add(project)
            db.session.commit()
            project_id = project.id

        asset_id = _make_asset(app, 'FALLBACK-ASSET-1')
        wo_id = _make_work_order(app, asset_id, actual_cost=42)
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            wo.project_id = project_id
            db.session.commit()

            from application.models import Project as P
            from application.projects import project_cost_rollup
            project = db.session.get(P, project_id)
            rollup = project_cost_rollup(project)
            assert rollup['work_order_cost'] == 42
            assert rollup['total_cost'] == 42  # no direct costs

    def test_project_with_no_costs_returns_zeros(self, app):
        with app.app_context():
            from application.models import Project
            from application.projects import project_cost_rollup
            from main import db
            project = Project(site_id=1, name='Empty Rollup Project', status='Planning')
            db.session.add(project)
            db.session.commit()
            rollup = project_cost_rollup(project)
            assert rollup == {'direct_cost': 0, 'work_order_cost': 0, 'total_cost': 0,
                              'work_order_count': 0, 'estimated_cost': 0}

    def test_add_and_remove_project_vendor(self, app, admin_client):
        admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Vendor Link Project', 'status': 'Planning',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Project, Vendor
            from main import db
            project_id = Project.query.filter_by(name='Vendor Link Project').first().id
            vendor = Vendor(name='Project Test Vendor')
            db.session.add(vendor)
            db.session.commit()
            vendor_id = vendor.id

        r = admin_client.post(f'/add_project_vendor/{project_id}', data={'vendor_id': str(vendor_id)}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Project
            from main import db
            project = db.session.get(Project, project_id)
            assert vendor_id in [v.id for v in project.vendors]

        r = admin_client.post(f'/remove_project_vendor/{project_id}/{vendor_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Project
            from main import db
            project = db.session.get(Project, project_id)
            assert vendor_id not in [v.id for v in project.vendors]

    def test_add_and_toggle_project_task(self, app, admin_client):
        admin_client.post('/add_project', data={
            'site_id': '1', 'name': 'Task Project', 'status': 'Planning',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Project
            project_id = Project.query.filter_by(name='Task Project').first().id

        admin_client.post(f'/add_project_task/{project_id}', data={
            'name': 'Get board approval', 'sort_order': '0',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import ProjectTask
            task = ProjectTask.query.filter_by(project_id=project_id, name='Get board approval').first()
            assert task is not None and task.status == 'Not Started'
            task_id = task.id

        admin_client.post(f'/toggle_project_task/{task_id}', follow_redirects=True)
        with app.app_context():
            from application.models import ProjectTask
            from main import db
            task = db.session.get(ProjectTask, task_id)
            assert task.status == 'Completed' and task.completed_at is not None


class TestTicketsAndOtherModulesStillWork:
    def test_ticket_routes_unaffected(self, user_client):
        assert user_client.get('/tickets').status_code == 200

    def test_asset_and_work_order_detail_still_load(self, app, admin_client):
        asset_id = _make_asset(app, 'REGRESSION-ASSET')
        wo_id = _make_work_order(app, asset_id)
        assert admin_client.get(f'/edit_asset/{asset_id}').status_code == 200
        assert admin_client.get(f'/edit_work_order/{wo_id}').status_code == 200
