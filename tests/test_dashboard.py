"""
Dashboards & Analytics tests: every Canonical KPI computed against seeded
data with hand-computed expected values, facility health factors, recurring
issue detection, insight rules, filter/date handling, role views, and an
index-usage check for the dashboard's main query.

KPI fixtures are isolated by a dedicated Facility and queried with a
facility_id filter, so other test modules' work orders in the shared
session DB can't skew the expected numbers.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from application import analytics
from application.analytics import RecurringRow


TODAY = datetime.now(timezone.utc).date()
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _ids(app, priority='High', category='HVAC'):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name=priority).first().id,
                Category.query.filter_by(name=category).first().id)


def _facility(app, name, **kwargs):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=1, name=name, **kwargs)
            db.session.add(f)
            db.session.commit()
        return f.id


def _user(app, email, role_id, first='Dash'):
    with app.app_context():
        from application.models import User
        from application.utils import hash_email
        from werkzeug.security import generate_password_hash
        from main import db
        u = User.query.filter_by(email_hash=hash_email(email, app.config['SECRET_KEY'])).first()
        if u is None:
            u = User(first_name=first, last_name='User', status='Active',
                     password=generate_password_hash('Some@Password1'), must_change_password=False,
                     failed_login_attempts=0, role_id=role_id, site_id=1)
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
        started_days_after=None, completed_days_after=None, due_in=None, **kwargs):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app, priority, category)
        created = NOW - timedelta(days=created_days_ago)
        wo = WorkOrder(site_id=1, facility_id=facility_id, title=title, source=kwargs.pop('source', 'Manual'),
                       status=status, priority_id=pri, category_id=cat, **kwargs)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        wo.created_at = created
        if started_days_after is not None:
            wo.started_at = created + timedelta(days=started_days_after)
        if completed_days_after is not None:
            wo.completed_at = created + timedelta(days=completed_days_after)
        if due_in is not None:
            wo.due_date = TODAY + timedelta(days=due_in)
        db.session.commit()
        return wo.id


def _filters(facility_id=None, days=60, **overrides):
    f = {'preset': 'custom', 'start': TODAY - timedelta(days=days), 'end': TODAY, 'period_label': 'in the selected period',
         'site_id': None, 'site_ids': None, 'facility_id': facility_id, 'team': None, 'technician_id': None,
         'category_id': None, 'priority_id': None, 'status': None, 'requester_id': None}
    f.update(overrides)
    return f


# ---------------------------------------------------------------------------
# Pure: date ranges
# ---------------------------------------------------------------------------

class TestDateRange:
    def test_presets(self):
        today = date(2026, 8, 12)  # a Wednesday
        assert analytics.date_range('today', today)[:2] == (today, today)
        assert analytics.date_range('week', today)[:2] == (date(2026, 8, 10), today)
        assert analytics.date_range('month', today)[:2] == (date(2026, 8, 1), today)
        assert analytics.date_range('quarter', today)[:2] == (date(2026, 7, 1), today)
        assert analytics.date_range('year', today)[:2] == (date(2026, 1, 1), today)

    def test_custom_and_fallback(self):
        today = date(2026, 8, 12)
        assert analytics.date_range('custom', today, date(2026, 2, 1), date(2026, 3, 1)) == (date(2026, 2, 1), date(2026, 3, 1), 'custom')
        assert analytics.date_range('custom', today, date(2026, 3, 1), date(2026, 2, 1)) == (date(2026, 8, 1), today, 'month')
        assert analytics.date_range('custom', today)[2] == 'month'

    def test_parse_filters_validates(self):
        from werkzeug.datastructures import MultiDict
        args = MultiDict({'preset': 'bogus', 'site_id': '3', 'status': 'Nope', 'facility_id': 'abc', 'team': ' Crew A '})
        f = analytics.parse_filters(args, None, today=date(2026, 8, 12))
        assert f['preset'] == 'month' and f['status'] is None and f['facility_id'] is None
        assert f['site_id'] == 3 and f['site_ids'] == [3] and f['team'] == 'Crew A'
        locked = analytics.parse_filters(args, [1], today=date(2026, 8, 12))
        assert locked['site_id'] is None and locked['site_ids'] == [1]


# ---------------------------------------------------------------------------
# Pure: recurring issue detection
# ---------------------------------------------------------------------------

def _row(i, title='Leak', asset=None, room=None, facility=1, category=1):
    return RecurringRow(i, f'WO-{i:06d}', title, asset, room, facility, category, NOW - timedelta(days=i))


class TestRecurringDetection:
    def test_normalize_title(self):
        assert analytics.normalize_title('The AC in Room 12 is NOT cooling, again!') == 'ac room 12 not cooling'
        assert analytics.normalize_title('') == ''

    def test_below_threshold_is_not_recurring(self):
        rows = [_row(1, asset=5), _row(2, asset=5)]
        assert analytics.detect_recurring_issues(rows) == []

    def test_same_asset_three_times(self):
        rows = [_row(1, 'a', asset=5), _row(2, 'b', asset=5), _row(3, 'c', asset=5, category=2)]
        groups = analytics.detect_recurring_issues(rows)
        assert [(g['kind'], g['count']) for g in groups] == [('asset', 3)]
        assert groups[0]['work_order_ids'] == {1, 2, 3}

    def test_room_facility_and_description_kinds(self):
        rows = [_row(1, 'x', room=9), _row(2, 'y', room=9), _row(3, 'z', room=9),
                _row(4, 'door stuck', facility=2), _row(5, 'door stuck', facility=3), _row(6, 'Door stuck!', facility=4)]
        kinds = {g['kind'] for g in analytics.detect_recurring_issues(rows)}
        assert kinds == {'room', 'description'}

    def test_redundant_broader_groups_are_dropped_but_supersets_kept(self):
        # 3 on one asset (all facility 1 / HVAC) + 1 more facility 1 / HVAC on no asset:
        # the asset group is kept, the facility group is a strict superset so it's kept too,
        # and the identical-membership description group is dropped.
        rows = [_row(1, 'same', asset=5), _row(2, 'same', asset=5), _row(3, 'same', asset=5), _row(4, 'other')]
        groups = analytics.detect_recurring_issues(rows)
        assert [(g['kind'], g['count']) for g in groups] == [('facility', 4), ('asset', 3)]

    def test_facility_id_is_single_or_none(self):
        rows = [_row(1, 'same', facility=1), _row(2, 'same', facility=2), _row(3, 'same', facility=1)]
        g = analytics.detect_recurring_issues(rows)[0]
        assert g['kind'] == 'description' and g['facility_id'] is None


# ---------------------------------------------------------------------------
# Pure: facility health
# ---------------------------------------------------------------------------

class _Fac:
    def __init__(self, year_built=None):
        self.year_built = year_built


class TestFacilityHealth:
    def test_full_data_hand_computed(self):
        stats = {'avg_condition': 82.4, 'open_wos': 4, 'overdue_wos': 1, 'recurring_groups': 1,
                 'pm_total': 10, 'pm_overdue': 2, 'inspection_results': 20, 'inspection_failures': 5,
                 'maintenance_cost': Decimal('1500'), 'district_avg_cost': Decimal('1000')}
        h = analytics.facility_health(_Fac(year_built=2000), stats, today=date(2026, 8, 12))
        v = {k: f['value'] for k, f in h['factors'].items()}
        assert v == {'Asset Condition': 82, 'Open/Overdue Work Orders': 60, 'Recurring Problems': 75,
                     'PM Compliance': 80, 'Inspection Failures': 75, 'Facility Age': 65, 'Maintenance Cost': 60}
        assert h['factor_count'] == 7
        assert h['score'] == round((82 + 60 + 75 + 80 + 75 + 65 + 60) / 7)

    def test_missing_factors_are_excluded_not_zero(self):
        h = analytics.facility_health(_Fac(), {'open_wos': 0, 'overdue_wos': 0, 'recurring_groups': 0}, today=date(2026, 8, 12))
        # Only Open/Overdue and Recurring Problems are always available; the other five need data.
        assert h['factor_count'] == 2
        assert h['score'] == 100
        assert not h['factors']['Asset Condition']['available']
        assert not h['factors']['Maintenance Cost']['available']

    def test_overdue_penalty_floors_at_zero(self):
        h = analytics.facility_health(_Fac(), {'open_wos': 25, 'overdue_wos': 5}, today=date(2026, 8, 12))
        assert h['factors']['Open/Overdue Work Orders']['value'] == 0

    def test_cost_below_average_scores_high(self):
        stats = {'maintenance_cost': Decimal('100'), 'district_avg_cost': Decimal('1000')}
        assert analytics.facility_health(_Fac(), stats)['factors']['Maintenance Cost']['value'] == 100


# ---------------------------------------------------------------------------
# Pure: insights
# ---------------------------------------------------------------------------

class TestInsights:
    def test_no_data_no_claims(self):
        assert analytics.build_insights({}, 'this month') == []

    def test_each_rule_fires_with_its_numbers(self):
        data = {
            'wo': {'overdue': 3, 'unassigned': 2, 'total_open': 8, 'created_in_period': 40, 'sla_compliance': 60.0,
                   'sla_sample': 10, 'avg_resolution_days': 6.0},
            'prior_created': 20, 'prior_avg_resolution_days': 4.0,
            'top_category': ('HVAC', 20), 'total_in_period': 40,
            'facility_vs_avg': {'facility': 'North', 'category': 'HVAC', 'count': 12, 'avg': 8.0, 'facility_count': 3},
            'overdue_by_facility': [('North', 2)],
            'maintenance': {'preventive': 4, 'corrective': 36, 'preventive_pct': 10.0},
            'facility': {'assets_poor_critical': 2, 'assets_past_expected_life': 1},
            'recurring': [{'label': 'Asset X', 'count': 5}],
            'cost': {'both_count': 5, 'both_estimated': Decimal('1000'), 'both_actual': Decimal('1300')},
        }
        texts = [i['text'] for i in analytics.build_insights(data, 'this month')]
        assert 'Work order volume is up 100% this month versus the previous period (40 vs 20).' in texts
        assert 'North has 50% more HVAC work orders than the average facility this month (12 vs 8.0).' in texts
        assert '3 open work orders are past due; North has the most (2).' in texts
        assert '2 of 8 open work orders are unassigned (25.0%).' in texts
        assert '10.0% of work orders opened this month were preventive (PM-generated); 36 were corrective.' in texts
        assert 'HVAC accounts for 50.0% of work orders opened this month (20 of 40).' in texts
        assert '2 assets are in Poor or Critical condition and 1 is past expected life.' in texts
        assert '1 recurring issue detected this month; most frequent: Asset X (5 work orders).' in texts
        assert 'Actual cost exceeded estimates by 30% across 5 work orders with both recorded this month.' in texts
        assert '60.0% of work orders completed this month met their due date (10 had one).' in texts
        assert 'Average resolution time is 6.0 days this month, up 50% from 4.0 days the previous period.' in texts
        levels = [i['level'] for i in analytics.build_insights(data, 'this month')]
        assert levels == sorted(levels, key={'danger': 0, 'warning': 1, 'info': 2}.get)

    def test_rules_stay_quiet_below_thresholds(self):
        data = {'wo': {'created_in_period': 4, 'sla_sample': 2, 'sla_compliance': 100.0}, 'prior_created': 4,
                'top_category': ('HVAC', 4), 'total_in_period': 4,
                'facility_vs_avg': {'facility': 'N', 'category': 'HVAC', 'count': 2, 'avg': 1.0, 'facility_count': 2},
                'maintenance': {'preventive': 1, 'corrective': 3, 'preventive_pct': 25.0},
                'cost': {'both_count': 2, 'both_estimated': Decimal('10'), 'both_actual': Decimal('100')}}
        assert analytics.build_insights(data, 'today') == []


# ---------------------------------------------------------------------------
# KPIs against seeded data
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def kpi_facility(app):
    """Seven work orders with known states, isolated by facility."""
    fid = _facility(app, 'KPI Building', year_built=1985)
    tech_id = _user(app, 'kpi-tech@test.com', 3, first='Kpi')
    _wo(app, fid, 'KPI new unassigned overdue backlog', status='New', created_days_ago=40, due_in=-1)
    _wo(app, fid, 'KPI new assigned emergency', status='New', priority='Emergency', assigned_to_id=tech_id)
    _wo(app, fid, 'KPI in progress', status='In Progress', created_days_ago=5, started_days_after=2, assigned_to_id=tech_id)
    _wo(app, fid, 'KPI waiting parts', status='Waiting for Parts', assigned_to_id=tech_id)
    _wo(app, fid, 'KPI completed on time', status='Completed', created_days_ago=10, completed_days_after=4, due_in=5,
        assigned_to_id=tech_id, estimated_cost=100, actual_cost=150)
    _wo(app, fid, 'KPI closed late', status='Closed', created_days_ago=10, completed_days_after=6, due_in=-8,
        assigned_to_id=tech_id, estimated_cost=200, actual_cost=180, source='PM')
    _wo(app, fid, 'KPI cancelled', status='Cancelled', created_days_ago=3)
    return fid


class TestWorkOrderKpis:
    def test_point_in_time_counts(self, app, kpi_facility):
        with app.app_context():
            k = analytics.work_order_kpis(_filters(kpi_facility), today=TODAY)
        assert k['total_open'] == 4
        assert k['new_requests'] == 2
        assert k['unassigned'] == 1
        assert k['in_progress'] == 1
        assert k['waiting'] == 1
        assert k['overdue'] == 1
        assert k['emergency'] == 1 and k['critical'] == 0
        assert k['backlog'] == 1

    def test_period_metrics(self, app, kpi_facility):
        with app.app_context():
            k = analytics.work_order_kpis(_filters(kpi_facility), today=TODAY)
        assert k['created_in_period'] == 7
        assert k['completed'] == 2
        assert k['completion_rate'] == round(100 * 2 / 7, 1)
        assert k['avg_response_days'] == 2.0
        assert k['avg_resolution_days'] == 5.0   # (4 + 6) / 2
        assert k['sla_compliance'] == 50.0 and k['sla_sample'] == 2

    def test_date_filter_excludes_older_work_orders(self, app, kpi_facility):
        with app.app_context():
            k = analytics.work_order_kpis(_filters(kpi_facility, days=20), today=TODAY)
        assert k['created_in_period'] == 6      # the 40-day-old one drops out
        assert k['total_open'] == 4             # point-in-time counts don't

    def test_filter_by_applies_consistently(self, app, kpi_facility):
        with app.app_context():
            from application.models import Priority
            emergency = Priority.query.filter_by(name='Emergency').first().id
            k = analytics.work_order_kpis(_filters(kpi_facility, priority_id=emergency), today=TODAY)
            k_status = analytics.work_order_kpis(_filters(kpi_facility, status='Waiting for Parts'), today=TODAY)
        assert k['total_open'] == 1 and k['created_in_period'] == 1
        assert k_status['total_open'] == 1 and k_status['waiting'] == 1


class TestMaintenanceAndCostKpis:
    def test_preventive_vs_corrective_and_cost(self, app, kpi_facility):
        with app.app_context():
            m = analytics.maintenance_kpis(_filters(kpi_facility), today=TODAY)
            c = analytics.cost_kpis(_filters(kpi_facility))
        assert m['preventive'] == 1 and m['corrective'] == 6
        assert m['preventive_pct'] == round(100 / 7, 1)
        assert c['work_order_count'] == 7
        assert c['actual_total'] == Decimal('330')
        assert c['estimated_total'] == Decimal('300')
        assert c['both_count'] == 2 and c['both_estimated'] == Decimal('300') and c['both_actual'] == Decimal('330')

    def test_cost_prefers_cost_record_over_actual_cost(self, app):
        fid = _facility(app, 'Cost Pref Building')
        wo_id = _wo(app, fid, 'Cost pref WO', actual_cost=999)
        with app.app_context():
            from application.models import CostRecord
            from main import db
            db.session.add(CostRecord(work_order_id=wo_id, labor_cost=40, material_cost=10, vendor_cost=25, total_cost=75))
            db.session.commit()
            c = analytics.cost_kpis(_filters(fid))
        assert c['actual_total'] == Decimal('75')
        assert (c['labor_cost'], c['material_cost'], c['vendor_cost']) == (Decimal('40'), Decimal('10'), Decimal('25'))

    def test_pm_and_inspection_due_overdue(self, app):
        fid = _facility(app, 'PM Dash Building')
        with app.app_context():
            from application.models import AssetType, Asset, MaintenancePlan, MaintenanceSchedule, InspectionTemplate, Inspection
            from main import db
            pri, cat = _ids(app)
            at = AssetType(name='Dash PM Type'); db.session.add(at); db.session.flush()
            a = Asset(site_id=1, facility_id=fid, asset_type_id=at.id, asset_tag='DASH-PM-1', name='Dash PM Asset')
            db.session.add(a); db.session.flush()
            plan = MaintenancePlan(name='Dash PM Plan', asset_id=a.id, frequency='Monthly', category_id=cat, priority_id=pri)
            db.session.add(plan); db.session.flush()
            db.session.add(MaintenanceSchedule(maintenance_plan_id=plan.id, asset_id=a.id, next_due_date=TODAY - timedelta(days=2)))
            tmpl = InspectionTemplate(name='Dash Insp Template', category_id=cat, priority_id=pri)
            db.session.add(tmpl); db.session.flush()
            db.session.add(Inspection(template_id=tmpl.id, site_id=1, facility_id=fid, due_date=TODAY + timedelta(days=3)))
            db.session.add(Inspection(template_id=tmpl.id, site_id=1, asset_id=a.id, due_date=TODAY - timedelta(days=1)))
            db.session.commit()
            m = analytics.maintenance_kpis(_filters(fid), today=TODAY)
        assert (m['pm_overdue'], m['pm_due']) == (1, 0)
        assert (m['inspections_overdue'], m['inspections_due']) == (1, 1)


class TestFacilityKpis:
    def test_asset_condition_kpis(self, app):
        fid = _facility(app, 'Condition KPI Building')
        with app.app_context():
            from application.models import AssetType, Asset
            from main import db
            at = AssetType(name='Dash Cond Type', expected_life_years=5); db.session.add(at); db.session.flush()
            specs = [('Excellent', 95, None), ('Fair', 60, None), ('Poor', 30, date(2010, 1, 1)), ('Critical', 10, None), (None, None, None)]
            for i, (label, score, install) in enumerate(specs):
                db.session.add(Asset(site_id=1, facility_id=fid, asset_type_id=at.id, asset_tag=f'DASH-COND-{i}',
                                     name='c', condition_label=label, condition_score=score, install_date=install))
            db.session.commit()
            k = analytics.facility_kpis(_filters(fid), today=TODAY)
        assert k['total_facilities'] == 1 and k['total_assets'] == 5
        assert k['assets_requiring_attention'] == 3
        assert k['assets_poor_critical'] == 2
        assert k['assets_past_expected_life'] == 1
        assert k['condition_distribution'] == {'Excellent': 1, 'Good': 0, 'Fair': 1, 'Poor': 1, 'Critical': 1, 'Unassessed': 1}

    def test_open_issues_by_facility(self, app, kpi_facility):
        with app.app_context():
            k = analytics.facility_kpis(_filters(kpi_facility), today=TODAY)
        assert k['open_issues_by_facility'] == [('KPI Building', 4)]


class TestCharts:
    def test_status_and_month_series(self, app, kpi_facility):
        with app.app_context():
            charts = analytics.chart_data(_filters(kpi_facility))
        by_status = dict(zip(charts['by_status']['labels'], charts['by_status']['datasets'][0]['data']))
        assert by_status['New'] == 2 and by_status['Cancelled'] == 1 and by_status['Closed'] == 1
        assert sum(charts['by_month']['datasets'][0]['data']) == 7
        assert charts['by_facility']['labels'] == ['KPI Building']
        assert charts['by_technician']['datasets'][0]['data'] == [5]
        assert len(charts['trends']['datasets']) == 2


class TestFacilityHealthScores:
    def test_end_to_end_factors(self, app, kpi_facility):
        with app.app_context():
            from application.models import AssetType, Asset
            from main import db
            at = AssetType(name='Dash Health Type'); db.session.add(at); db.session.flush()
            db.session.add(Asset(site_id=1, facility_id=kpi_facility, asset_type_id=at.id, asset_tag='DASH-HEALTH-1',
                                 name='h', condition_score=90, condition_label='Excellent'))
            db.session.commit()
            rows = analytics.facility_health_scores(_filters(kpi_facility), today=TODAY)
        assert len(rows) == 1
        factors = rows[0]['health']['factors']
        assert factors['Asset Condition']['value'] == 90
        assert factors['Open/Overdue Work Orders']['value'] == 60     # 4 open -> 70, minus 10 for 1 overdue
        assert factors['Facility Age']['value'] == 45                 # built 1985 -> 41 years -> the 41-60 bucket
        assert not factors['PM Compliance']['available']
        assert not factors['Inspection Failures']['available']
        assert factors['Maintenance Cost']['value'] == 80             # sole facility in scope == the average -> ratio 1.0
        assert rows[0]['health']['score'] is not None


class TestRecurringQueries:
    def test_detects_and_labels_from_db(self, app):
        fid = _facility(app, 'Recurring Building')
        for i in range(3):
            _wo(app, fid, f'Recurring roof leak {i}', category='Roofing')
        with app.app_context():
            groups = analytics.label_recurring_groups(analytics.detect_recurring_issues(analytics.recurring_rows(_filters(fid))))
        assert len(groups) == 1
        assert groups[0]['kind'] == 'facility' and groups[0]['label'] == 'Recurring Building — Roofing'
        assert len(groups[0]['work_orders']) == 3

    def test_related_groups_for_work_order_detail(self, app):
        fid = _facility(app, 'Related Building')
        ids = [_wo(app, fid, 'Related plumbing', category='Plumbing') for _ in range(3)]
        lone = _wo(app, fid, 'Lonely electrical', category='Electrical')
        with app.app_context():
            from application.models import WorkOrder
            related = analytics.related_recurring_groups(WorkOrder.query.get(ids[0]))
            none = analytics.related_recurring_groups(WorkOrder.query.get(lone))
        assert len(related) == 1 and related[0]['work_order_ids'] == set(ids)
        assert none == []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class TestDashboardRoutes:
    def test_requires_login(self, client):
        r = client.get('/dashboard')
        assert r.status_code in (302, 401)

    def test_admin_default_is_executive_and_can_switch(self, admin_client):
        r = admin_client.get('/dashboard')
        assert r.status_code == 200 and b'Executive' in r.data and b'Facility Health Score' in r.data
        r = admin_client.get('/dashboard?view=manager&preset=year')
        assert r.status_code == 200 and b'M&amp;O Manager' in r.data and b'Response &amp; Completion' in r.data
        r = admin_client.get('/dashboard?view=technician')
        assert r.status_code == 200 and b'M&amp;O Dashboard &mdash; Executive' in r.data   # not allowed -> default

    def test_filters_change_the_numbers(self, app, admin_client, kpi_facility):
        r = admin_client.get(f'/dashboard?view=manager&preset=custom&start={TODAY - timedelta(days=60)}&end={TODAY}&facility_id={kpi_facility}')
        assert r.status_code == 200
        assert b'KPI Building' in r.data
        assert b'Backlog (open &gt; 30 days)' in r.data
        r = admin_client.get('/dashboard?preset=custom&start=bad&end=2026-01-01')
        assert r.status_code == 200   # invalid custom range falls back to this month

    def test_technician_gets_own_view_only(self, app):
        tech_id = _user(app, 'dash-tech@test.com', 3, first='Dashtech')
        fid = _facility(app, 'Tech Dash Building')
        _wo(app, fid, 'Tech dash mine', assigned_to_id=tech_id, due_in=0)
        c = _client_as(app, tech_id)
        r = c.get('/dashboard?view=executive')
        assert r.status_code == 200
        assert b'Technician' in r.data and b'My Open Work Orders' in r.data and b'Tech dash mine' in r.data
        assert b'Facility Health Score' not in r.data

    def test_school_staff_sees_own_requests(self, app):
        staff_id = _user(app, 'dash-staff@test.com', 4, first='Dashstaff')
        fid = _facility(app, 'Staff Dash Building')
        _wo(app, fid, 'Staff dash request', requester_id=staff_id, source='Request')
        c = _client_as(app, staff_id)
        r = c.get('/dashboard?view=manager')
        assert r.status_code == 200
        assert b'School Staff' in r.data and b'Staff dash request' in r.data and b'Open Issues by Facility' in r.data
        assert b'Maintenance Cost' not in r.data

    def test_facility_page_shows_health_factors(self, admin_client, kpi_facility):
        r = admin_client.get(f'/edit_facility/{kpi_facility}')
        assert r.status_code == 200
        assert b'Health Score' in r.data and b'Open/Overdue Work Orders' in r.data and b'built 1985' in r.data

    def test_work_order_page_links_recurring_siblings(self, app, admin_client):
        fid = _facility(app, 'WO Recurring Building')
        ids = [_wo(app, fid, 'Sibling grounds issue', category='Grounds') for _ in range(3)]
        r = admin_client.get(f'/edit_work_order/{ids[0]}')
        assert r.status_code == 200 and b'Recurring Issue' in r.data
        with app.app_context():
            from application.models import WorkOrder
            sibling = WorkOrder.query.get(ids[1]).wo_number.encode()
        assert sibling in r.data

    def test_legacy_ticket_dashboard_untouched(self, admin_client):
        assert admin_client.get('/').status_code == 200


# ---------------------------------------------------------------------------
# Performance baseline: the main dashboard query uses indexes
# ---------------------------------------------------------------------------

class TestQueryPlans:
    def test_open_kpi_query_uses_work_order_indexes(self, app, kpi_facility):
        with app.app_context():
            from main import db
            from sqlalchemy import text
            plan = db.session.execute(text(
                "EXPLAIN QUERY PLAN SELECT count(id) FROM work_order WHERE facility_id = :f AND status IN ('New','Assigned')"
            ), {'f': kpi_facility}).fetchall()
            plan_text = ' '.join(str(row) for row in plan)
        assert 'USING INDEX' in plan_text and 'SCAN work_order' not in plan_text
