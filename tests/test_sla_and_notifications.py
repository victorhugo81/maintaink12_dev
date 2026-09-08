"""
SLA rules and Notifications tests: breach/warning state-machine accuracy
against known inputs, dashboard SLA summary and compliance fallback,
per-user preference honoring, and dedup (never re-notifying the same
condition within its bucket window).
"""
from datetime import date, datetime, timedelta, timezone
import pytest

from application import sla, notifications
from application.models import NOTIFICATION_EVENTS


NOW = datetime.now(timezone.utc).replace(tzinfo=None)
TODAY = NOW.date()


def _ids(app, priority='High', category='HVAC'):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name=priority).first().id,
                Category.query.filter_by(name=category).first().id)


def _facility(app, name, site_id=1):
    with app.app_context():
        from application.models import Facility
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=site_id, name=name)
            db.session.add(f)
            db.session.commit()
        return f.id


def _user(app, email, role_id, first='Sla'):
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


def _wo(app, facility_id, title, status='New', priority='High', category='HVAC',
        created_hours_ago=0, started_hours_after=None, completed_hours_after=None, **kwargs):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app, priority, category)
        created = NOW - timedelta(hours=created_hours_ago)
        wo = WorkOrder(site_id=1, facility_id=facility_id, title=title, source=kwargs.pop('source', 'Manual'),
                       status=status, priority_id=pri, category_id=cat, **kwargs)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        wo.created_at = created
        if started_hours_after is not None:
            wo.started_at = created + timedelta(hours=started_hours_after)
        if completed_hours_after is not None:
            wo.completed_at = created + timedelta(hours=completed_hours_after)
        db.session.commit()
        return wo.id


def _rule(response_hours=4, resolution_hours=48):
    from application.models import SLARule
    return SLARule(id=1, priority_id=1, response_hours=response_hours, resolution_hours=resolution_hours, is_active=True)


class _Wo:
    """A bare namespace standing in for a WorkOrder in pure sla.py tests."""
    def __init__(self, created_at, started_at=None, completed_at=None, priority_id=1, status='New'):
        self.created_at, self.started_at, self.completed_at = created_at, started_at, completed_at
        self.priority_id, self.status = priority_id, status


# ---------------------------------------------------------------------------
# Pure: sla.py state machine
# ---------------------------------------------------------------------------

class TestSlaStateMachine:
    def test_response_met_when_started_before_deadline(self):
        rule = _rule(response_hours=4)
        wo = _Wo(created_at=NOW - timedelta(hours=10), started_at=NOW - timedelta(hours=8))
        assert sla.evaluate(wo, rule, now=NOW)['response'] == sla.MET

    def test_response_breached_when_started_after_deadline(self):
        rule = _rule(response_hours=4)
        wo = _Wo(created_at=NOW - timedelta(hours=10), started_at=NOW - timedelta(hours=2))
        assert sla.evaluate(wo, rule, now=NOW)['response'] == sla.BREACHED

    def test_response_breached_when_not_started_and_past_deadline(self):
        rule = _rule(response_hours=4)
        wo = _Wo(created_at=NOW - timedelta(hours=5))
        assert sla.evaluate(wo, rule, now=NOW)['response'] == sla.BREACHED

    def test_response_warning_in_final_20_percent_of_window(self):
        rule = _rule(response_hours=10)  # warning starts at hour 8 (last 20% = 2h)
        wo = _Wo(created_at=NOW - timedelta(hours=8, minutes=30))
        assert sla.evaluate(wo, rule, now=NOW)['response'] == sla.WARNING

    def test_response_pending_well_before_deadline(self):
        rule = _rule(response_hours=10)
        wo = _Wo(created_at=NOW - timedelta(hours=1))
        assert sla.evaluate(wo, rule, now=NOW)['response'] == sla.PENDING

    def test_resolution_met_and_breached_from_completed_at(self):
        rule = _rule(resolution_hours=48)
        met = _Wo(created_at=NOW - timedelta(hours=60), completed_at=NOW - timedelta(hours=20))
        breached = _Wo(created_at=NOW - timedelta(hours=60), completed_at=NOW - timedelta(hours=5))
        assert sla.evaluate(met, rule, now=NOW)['resolution'] == sla.MET
        assert sla.evaluate(breached, rule, now=NOW)['resolution'] == sla.BREACHED

    def test_due_at_helpers(self):
        rule = _rule(response_hours=4, resolution_hours=48)
        wo = _Wo(created_at=datetime(2026, 1, 1, 0, 0))
        assert sla.response_due_at(wo, rule) == datetime(2026, 1, 1, 4, 0)
        assert sla.resolution_due_at(wo, rule) == datetime(2026, 1, 3, 0, 0)


class TestSlaScan:
    def test_scan_finds_breach_and_warning_separately(self):
        rule = _rule(response_hours=4, resolution_hours=100)
        breached_response = _Wo(created_at=NOW - timedelta(hours=10))  # no rule for resolution yet (100h away)
        result = sla.scan([breached_response], rules=[rule], now=NOW)
        assert len(result['breaches']) == 1 and result['breaches'][0]['metric'] == 'response'
        assert result['warnings'] == []

    def test_scan_skips_cancelled_and_unruled_priorities(self):
        rule = _rule(response_hours=1)
        cancelled = _Wo(created_at=NOW - timedelta(hours=10), status='Cancelled')
        no_rule = _Wo(created_at=NOW - timedelta(hours=10), priority_id=99)
        result = sla.scan([cancelled, no_rule], rules=[rule], now=NOW)
        assert result['breaches'] == [] and result['warnings'] == []

    def test_one_work_order_can_breach_both_metrics(self):
        rule = _rule(response_hours=1, resolution_hours=2)
        wo = _Wo(created_at=NOW - timedelta(hours=10))
        result = sla.scan([wo], rules=[rule], now=NOW)
        metrics = {b['metric'] for b in result['breaches']}
        assert metrics == {'response', 'resolution'}


class TestAggregateCompliance:
    def test_met_and_total_counts(self):
        rule = _rule(resolution_hours=48)
        met = _Wo(created_at=NOW - timedelta(hours=60), completed_at=NOW - timedelta(hours=20), status='Completed')
        missed = _Wo(created_at=NOW - timedelta(hours=60), completed_at=NOW - timedelta(hours=5), status='Closed')
        still_open = _Wo(created_at=NOW - timedelta(hours=60), status='New')
        met_n, total = sla.aggregate_compliance([met, missed, still_open], rules=[rule])
        assert (met_n, total) == (1, 2)

    def test_no_rules_returns_zero_total(self):
        wo = _Wo(created_at=NOW, completed_at=NOW, status='Completed')
        assert sla.aggregate_compliance([wo], rules=[]) == (0, 0)


# ---------------------------------------------------------------------------
# analytics.py integration
# ---------------------------------------------------------------------------

class TestAnalyticsIntegration:
    def test_sla_compliance_falls_back_to_due_date_when_no_rule(self, app):
        from application import analytics
        fid = _facility(app, 'SLA Fallback Building')
        _wo(app, fid, 'Fallback met', status='Completed', priority='Low', completed_hours_after=1, created_hours_ago=48)
        with app.app_context():
            from application.models import WorkOrder
            wo = WorkOrder.query.filter_by(title='Fallback met').first()
            wo.due_date = TODAY + timedelta(days=5)
            from main import db
            db.session.commit()
            f = {'start': TODAY - timedelta(days=10), 'end': TODAY, 'facility_id': fid, 'site_ids': None}
            k = analytics.work_order_kpis(f, today=TODAY)
        assert k['sla_sample'] == 1 and k['sla_compliance'] == 100.0

    def test_sla_compliance_uses_real_rule_when_configured(self, app):
        from application import analytics
        from application.models import SLARule
        from main import db
        fid = _facility(app, 'SLA Rule Building')
        pri_id, _ = _ids(app, priority='Critical')
        with app.app_context():
            rule = SLARule.query.filter_by(priority_id=pri_id).first()
            if rule is None:
                db.session.add(SLARule(priority_id=pri_id, response_hours=4, resolution_hours=24))
            else:
                rule.response_hours, rule.resolution_hours, rule.is_active = 4, 24, True
            db.session.commit()
        _wo(app, fid, 'Rule breach WO', status='Completed', priority='Critical', created_hours_ago=48, completed_hours_after=30)
        with app.app_context():
            f = {'start': TODAY - timedelta(days=10), 'end': TODAY, 'facility_id': fid, 'site_ids': None}
            k = analytics.work_order_kpis(f, today=TODAY)
        assert k['sla_sample'] == 1 and k['sla_compliance'] == 0.0  # 30h > 24h resolution target

    def test_sla_summary_unavailable_without_active_rules(self, app):
        """Deactivates every SLARule (regardless of what earlier tests in this
        shared-DB session created) right before asserting, so this test's
        outcome doesn't depend on execution order — the hard rule documented
        in application/CLAUDE.md for cross-test DB state."""
        from application import analytics
        from application.models import SLARule
        from main import db
        fid = _facility(app, 'No SLA Rules Building')
        with app.app_context():
            SLARule.query.update({'is_active': False})
            db.session.commit()
            f = {'start': TODAY - timedelta(days=10), 'end': TODAY, 'facility_id': fid, 'site_ids': None}
            summary = analytics.sla_summary(f, today=TODAY)
        assert summary['available'] is False and summary['breach_count'] == 0

    def test_sla_summary_reports_breach(self, app):
        from application import analytics
        from application.models import SLARule
        from main import db
        fid = _facility(app, 'SLA Summary Building')
        pri_id, _ = _ids(app, priority='Critical')
        with app.app_context():
            rule = SLARule.query.filter_by(priority_id=pri_id).first()
            if rule is None:
                db.session.add(SLARule(priority_id=pri_id, response_hours=2, resolution_hours=1000))
            else:
                rule.response_hours, rule.resolution_hours, rule.is_active = 2, 1000, True
            db.session.commit()
        _wo(app, fid, 'Summary breach WO', status='New', priority='Critical', created_hours_ago=10)
        with app.app_context():
            f = {'start': TODAY - timedelta(days=10), 'end': TODAY, 'facility_id': fid, 'site_ids': None}
            summary = analytics.sla_summary(f, today=TODAY)
        assert summary['available'] is True and summary['breach_count'] >= 1


# ---------------------------------------------------------------------------
# SLA Rule routes
# ---------------------------------------------------------------------------

class TestSlaRuleRoutes:
    def test_regular_user_forbidden(self, user_client):
        assert user_client.get('/sla_rules').status_code == 403

    def test_admin_can_create_and_edit(self, app, admin_client):
        pri_id, _ = _ids(app, priority='Critical')
        r = admin_client.post(f'/edit_sla_rule/{pri_id}', data={'response_hours': '4', 'resolution_hours': '48', 'is_active': 'y'},
                              follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import SLARule
            rule = SLARule.query.filter_by(priority_id=pri_id).first()
            assert rule is not None and rule.response_hours == 4 and rule.resolution_hours == 48

    def test_delete_rule(self, app, admin_client):
        pri_id, _ = _ids(app, priority='Medium')
        admin_client.post(f'/edit_sla_rule/{pri_id}', data={'response_hours': '8', 'resolution_hours': '72', 'is_active': 'y'})
        with app.app_context():
            from application.models import SLARule
            rule_id = SLARule.query.filter_by(priority_id=pri_id).first().id
        r = admin_client.post(f'/delete_sla_rule/{rule_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import SLARule
            assert SLARule.query.get(rule_id) is None


# ---------------------------------------------------------------------------
# Notification detection & dedup
# ---------------------------------------------------------------------------

class TestNotificationPreferences:
    def test_no_row_means_everything_allowed(self, app):
        with app.app_context():
            from application.models import User
            from application.utils import hash_email
            user = User.query.filter_by(email_hash=hash_email('admin@test.com', app.config['SECRET_KEY'])).first()
            assert user.notification_preference is None
            assert notifications.preference_allows(user, 'pm_due') is True

    def test_disabled_preference_is_honored(self, app):
        uid = _user(app, 'pref-test@test.com', 1, first='Pref')
        with app.app_context():
            from application.models import NotificationPreference, User
            from main import db
            db.session.add(NotificationPreference(user_id=uid, pm_due=False, pm_overdue=True, inspection_due=True,
                                                   inspection_failed=True, vendor_contract_expiring=True,
                                                   asset_warranty_expiring=True, sla_warning=True, sla_breach=True))
            db.session.commit()
            user = User.query.get(uid)
            assert notifications.preference_allows(user, 'pm_due') is False
            assert notifications.preference_allows(user, 'pm_overdue') is True


class TestNotificationSweepDedup:
    def test_pm_overdue_notifies_once_then_dedups_same_week(self, app):
        fid = _facility(app, 'Notif PM Building')
        pri_id, cat_id = _ids(app)
        aid = None
        with app.app_context():
            from application.models import AssetType, Asset, MaintenancePlan, MaintenanceSchedule, NotificationLog
            from main import db
            at = AssetType(name='Notif PM Type')
            db.session.add(at)
            db.session.flush()
            asset = Asset(site_id=1, facility_id=fid, asset_type_id=at.id, asset_tag='NOTIF-PM-1', name='n')
            db.session.add(asset)
            db.session.flush()
            aid = asset.id
            plan = MaintenancePlan(name='Notif PM Plan', asset_id=aid, frequency='Monthly', category_id=cat_id, priority_id=pri_id)
            db.session.add(plan)
            db.session.flush()
            sched = MaintenanceSchedule(maintenance_plan_id=plan.id, asset_id=aid, next_due_date=TODAY - timedelta(days=3))
            db.session.add(sched)
            db.session.commit()
            first = notifications.run_notification_sweep(today=TODAY, now=NOW)
            log_count_1 = NotificationLog.query.filter_by(event_type='pm_overdue').count()
            second = notifications.run_notification_sweep(today=TODAY, now=NOW)
            log_count_2 = NotificationLog.query.filter_by(event_type='pm_overdue').count()
        assert first['pm_overdue'] >= 1
        assert second['pm_overdue'] == 0
        assert log_count_1 == log_count_2

    def test_dedup_is_per_bucket_not_forever(self, app):
        """A week-bucketed event notifies again once the bucket changes."""
        fid = _facility(app, 'Notif Bucket Building')
        with app.app_context():
            from application.models import Vendor, NotificationLog
            from main import db
            vendor = Vendor(name='Notif Bucket Vendor', contract_end_date=TODAY + timedelta(days=5))
            db.session.add(vendor)
            db.session.commit()
            week1 = notifications._notify_vendor_contracts(TODAY)
            week2 = notifications._notify_vendor_contracts(TODAY + timedelta(days=10))
            db.session.commit()
        assert week1 >= 1
        assert week2 >= 1  # different ISO week -> notifies again

    def test_disabled_preference_blocks_notification(self, app):
        fid = _facility(app, 'Notif Pref Building')
        with app.app_context():
            from application.models import Vendor, NotificationPreference, User
            from main import db
            vendor = Vendor(name='Notif Pref Vendor', contract_end_date=TODAY + timedelta(days=1))
            db.session.add(vendor)
            # Disable the event for every current staff recipient
            for user in notifications.staff_recipients():
                pref = user.notification_preference or NotificationPreference(user_id=user.id)
                pref.vendor_contract_expiring = False
                for e in NOTIFICATION_EVENTS:
                    if not hasattr(pref, e) or getattr(pref, e) is None:
                        setattr(pref, e, True)
                db.session.add(pref)
            db.session.commit()
            sent = notifications._notify_vendor_contracts(TODAY)
        assert sent == 0

    def test_inspection_failed_is_event_driven_and_exactly_once(self, app):
        fid = _facility(app, 'Notif Insp Building')
        pri_id, cat_id = _ids(app)
        with app.app_context():
            from application.models import InspectionTemplate, InspectionItem, Inspection, InspectionResult, NotificationLog
            from main import db
            tmpl = InspectionTemplate(name='Notif Insp Template', category_id=cat_id, priority_id=pri_id)
            db.session.add(tmpl)
            db.session.flush()
            item = InspectionItem(template_id=tmpl.id, question='Q')
            db.session.add(item)
            db.session.flush()
            insp = Inspection(template_id=tmpl.id, site_id=1, facility_id=fid, due_date=TODAY, status='Completed')
            db.session.add(insp)
            db.session.flush()
            db.session.add(InspectionResult(inspection_id=insp.id, inspection_item_id=item.id, result='Fail'))
            db.session.commit()
            first = notifications.notify_inspection_failed(insp)
            second = notifications.notify_inspection_failed(insp)
        assert first >= 1
        assert second == 0

    def test_sweep_never_sends_inspection_failed_itself(self, app):
        """inspection_failed is event-driven only (notify_inspection_failed(),
        called from the results-recording route) — the daily sweep always
        reports 0 for it regardless of what's in the database."""
        summary = notifications.run_notification_sweep(today=TODAY, now=NOW)
        assert summary['inspection_failed'] == 0

    def test_sla_breach_notifies_staff_and_assigned_technician_then_dedups(self, app):
        fid = _facility(app, 'Notif SLA Building')
        tech_id = _user(app, 'notif-sla-tech@test.com', 3, first='NotifSlaTech')
        pri_id, _ = _ids(app, priority='Critical')
        with app.app_context():
            from application.models import SLARule, NotificationLog
            from main import db
            rule = SLARule.query.filter_by(priority_id=pri_id).first()
            if rule is None:
                db.session.add(SLARule(priority_id=pri_id, response_hours=2, resolution_hours=1000))
            else:
                rule.response_hours, rule.resolution_hours, rule.is_active = 2, 1000, True
            db.session.commit()
        wo_id = _wo(app, fid, 'Notif SLA breach WO', status='New', priority='Critical',
                   created_hours_ago=10, assigned_to_id=tech_id)
        with app.app_context():
            from application.models import NotificationLog
            first = notifications.run_notification_sweep(today=TODAY, now=NOW)
            recipients_notified = {log.user_id for log in NotificationLog.query.filter_by(
                event_type='sla_breach', entity_type='work_order_response', entity_id=wo_id).all()}
            second = notifications.run_notification_sweep(today=TODAY, now=NOW)
            recipients_after_second = NotificationLog.query.filter_by(
                event_type='sla_breach', entity_type='work_order_response', entity_id=wo_id).count()
        assert first['sla_breach'] >= 1
        assert tech_id in recipients_notified
        assert second['sla_breach'] == 0  # same day bucket for pm/insp, week bucket for sla_breach -> no new sends
        assert recipients_after_second == len(recipients_notified)  # unchanged, not duplicated


class TestNotificationPreferencesRoute:
    def test_save_preferences(self, admin_client):
        r = admin_client.post('/notification_preferences', data={'pm_overdue': 'y', 'sla_breach': 'y'}, follow_redirects=True)
        assert r.status_code == 200

    def test_get_renders_with_defaults(self, user_client):
        r = user_client.get('/notification_preferences')
        assert r.status_code == 200


class TestInspectionFailedRouteHook:
    def test_recording_a_failed_result_triggers_notification(self, app, admin_client):
        fid = _facility(app, 'Route Hook Insp Building')
        pri_id, cat_id = _ids(app)
        with app.app_context():
            from application.models import InspectionTemplate, InspectionItem, Inspection
            from main import db
            tmpl = InspectionTemplate(name='Route Hook Template', category_id=cat_id, priority_id=pri_id)
            db.session.add(tmpl)
            db.session.flush()
            item = InspectionItem(template_id=tmpl.id, question='Q1')
            db.session.add(item)
            db.session.flush()
            insp = Inspection(template_id=tmpl.id, site_id=1, facility_id=fid, due_date=TODAY)
            db.session.add(insp)
            db.session.commit()
            insp_id, item_id = insp.id, item.id
        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Fail', 'items-0-notes': '', 'generate_work_order': '',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import NotificationLog
            assert NotificationLog.query.filter_by(event_type='inspection_failed', entity_id=insp_id).count() >= 1
