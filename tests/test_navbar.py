"""
Top-navbar additions (modern-SaaS redesign, phase 1): global search box,
profile dropdown, and the notifications bell fed by
application.notifications.recent_notifications_for() via main.py's
inject_nav_notifications() context processor.
"""
from datetime import datetime, timedelta, timezone
import pytest


def _work_order(app, title='Navbar Test WO'):
    with app.app_context():
        from application.models import WorkOrder, Priority, Category
        from application import workflow
        from main import db
        pri = Priority.query.filter_by(name='High').first().id
        cat = Category.query.filter_by(name='HVAC').first().id
        wo = WorkOrder(site_id=1, title=title, source='Manual', status=workflow.OPEN,
                      priority_id=pri, category_id=cat)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        db.session.commit()
        return wo.id


class TestNavbarChrome:
    def test_search_form_and_profile_dropdown_render(self, admin_client):
        r = admin_client.get('/')
        assert r.status_code == 200
        assert b'name="q"' in r.data and b'action="/search"' in r.data
        assert b'navProfileToggle' in r.data
        assert b'navNotificationsToggle' in r.data

    def test_navbar_renders_on_an_arbitrary_non_dashboard_page(self, admin_client):
        r = admin_client.get('/work_orders')
        assert r.status_code == 200
        assert b'navProfileToggle' in r.data


class TestRecentNotificationsFor:
    def test_only_this_users_rows_returned(self, app):
        from application.models import User, NotificationLog
        from application.utils import hash_email
        from werkzeug.security import generate_password_hash
        from application import notifications as notifications_module
        from main import db

        wo_id = _work_order(app, 'Navbar Notif WO A')
        with app.app_context():
            admin = User.query.filter_by(role_id=1).first()
            other = User.query.filter_by(email_hash=hash_email('navbar-other@test.com', app.config['SECRET_KEY'])).first()
            if other is None:
                other = User(first_name='Navbar', last_name='Other', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=1)
                other.email = 'navbar-other@test.com'
                db.session.add(other)
                db.session.commit()

            db.session.add(NotificationLog(event_type='sla_warning', entity_type='work_order_response',
                                           entity_id=wo_id, user_id=admin.id, bucket='2026-W01'))
            db.session.add(NotificationLog(event_type='sla_warning', entity_type='work_order_response',
                                           entity_id=wo_id, user_id=other.id, bucket='2026-W02'))
            db.session.commit()

            # The shared session-scoped test DB may already have OTHER
            # notification types for the admin user from earlier test files
            # (pm_due, inspection_due, etc.) — don't assert on the shape of
            # every row, just that this specific one made it into the list.
            rows = notifications_module.recent_notifications_for(admin, limit=50)
            assert any(r['params'].get('work_order_id') == wo_id for r in rows)
            # The other user's row must never leak into admin's list — a
            # dedicated fresh user (created just above, in this test) has no
            # prior history, so their list is exactly this one row.
            other_rows = notifications_module.recent_notifications_for(other, limit=10)
            assert len(other_rows) == 1
            assert other_rows[0]['params']['work_order_id'] == wo_id

    def test_newest_first_and_limit_respected(self, app):
        from application.models import User, NotificationLog
        from main import db

        wo_id = _work_order(app, 'Navbar Notif WO B')
        with app.app_context():
            admin = User.query.filter_by(role_id=1).first()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for i in range(3):
                db.session.add(NotificationLog(event_type='sla_warning', entity_type='work_order_response',
                                               entity_id=wo_id, user_id=admin.id,
                                               bucket=f'2026-W{10+i:02d}', sent_at=now - timedelta(days=i)))
            db.session.commit()

            from application import notifications as notifications_module
            rows = notifications_module.recent_notifications_for(admin, limit=2)
            assert len(rows) == 2
            assert rows[0]['sent_at'] >= rows[1]['sent_at']

    def test_deleted_entity_is_skipped_not_broken(self, app):
        from application.models import User, NotificationLog, WorkOrder
        from main import db

        wo_id = _work_order(app, 'Navbar Notif WO To Delete')
        with app.app_context():
            admin = User.query.filter_by(role_id=1).first()
            db.session.add(NotificationLog(event_type='sla_warning', entity_type='work_order_response',
                                           entity_id=wo_id, user_id=admin.id, bucket='2026-W50'))
            db.session.commit()
            db.session.delete(db.session.get(WorkOrder, wo_id))
            db.session.commit()

            from application import notifications as notifications_module
            # Must not raise even though this row's entity is gone.
            rows = notifications_module.recent_notifications_for(admin, limit=10)
            assert all(r['params'].get('work_order_id') != wo_id for r in rows)
