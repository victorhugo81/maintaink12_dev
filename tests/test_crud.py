"""
CRUD tests for Roles, Sites, and Notifications.
All actions require admin access.
"""
import pytest


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class TestRoles:
    def test_roles_page_loads(self, admin_client):
        r = admin_client.get('/roles')
        assert r.status_code == 200

    def test_add_role(self, app, admin_client):
        r = admin_client.post('/add_role', data={
            'role_name': 'TestRole',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Role
            role = Role.query.filter_by(role_name='TestRole').first()
            assert role is not None

    def test_add_role_form_loads(self, admin_client):
        r = admin_client.get('/add_role')
        assert r.status_code == 200

    def test_edit_role(self, app, admin_client):
        """Roles with ID > 5 can be edited; the seeded roles (1-5) are protected."""
        with app.app_context():
            from application.models import Role
            from main import db
            # Create a role with an id well above 5 for editing
            r = Role(role_name='EditableRole')
            db.session.add(r)
            db.session.commit()
            role_id = r.id

        resp = admin_client.post(f'/edit_role/{role_id}', data={
            'role_name': 'EditableRoleUpdated',
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from application.models import Role
            from main import db
            role = db.session.get(Role, role_id)
            assert role.role_name == 'EditableRoleUpdated'

    def test_delete_role(self, app, admin_client):
        """Roles with ID > 5 can be deleted."""
        with app.app_context():
            from application.models import Role
            from main import db
            r = Role(role_name='DeletableRole')
            db.session.add(r)
            db.session.commit()
            role_id = r.id

        resp = admin_client.post(f'/delete_role/{role_id}', follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from application.models import Role
            from main import db
            assert db.session.get(Role, role_id) is None

    def test_regular_user_cannot_add_role(self, user_client):
        r = user_client.post('/add_role', data={'role_name': 'HackerRole'})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

class TestSites:
    def test_sites_page_loads(self, admin_client):
        r = admin_client.get('/sites')
        assert r.status_code == 200

    def test_add_site(self, app, admin_client):
        r = admin_client.post('/add_site', data={
            'site_name': 'Test School',
            'site_acronyms': 'TS',
            'site_code': 'TSC001',
            'site_cds': '00-001-0000001',
            'site_address': '456 Test Ave',
            'site_type': 'Middle',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Site
            site = Site.query.filter_by(site_name='Test School').first()
            assert site is not None

    def test_edit_site(self, app, admin_client):
        with app.app_context():
            from application.models import Site
            site = Site.query.filter_by(site_name='Test School').first()
            if site is None:
                pytest.skip('Test School not found')
            site_id = site.id

        r = admin_client.post(f'/edit_site/{site_id}', data={
            'site_name': 'Test School Edited',
            'site_acronyms': 'TSE',
            'site_code': 'TSC001',
            'site_cds': '00-001-0000001',
            'site_address': '456 Test Ave',
            'site_type': 'Middle',
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_delete_site(self, app, admin_client):
        with app.app_context():
            from application.models import Site
            site = Site.query.filter_by(site_name='Test School Edited').first()
            if site is None:
                pytest.skip('Test School Edited not found')
            site_id = site.id

        r = admin_client.post(f'/delete_site/{site_id}', follow_redirects=True)
        assert r.status_code == 200

    def test_regular_user_cannot_add_site(self, user_client):
        r = user_client.get('/add_site')
        assert r.status_code == 403

    def test_site_details_loads_for_admin(self, admin_client):
        r = admin_client.get('/site_details/1')
        assert r.status_code == 200
        assert b'Facility Health Score' in r.data
        assert b'Work Orders by Status' in r.data

    def test_site_details_work_order_status_counts(self, app, admin_client):
        with app.app_context():
            from application.models import WorkOrder, Priority, Category
            from application import workflow
            from main import db
            pri = Priority.query.filter_by(name='High').first().id
            cat = Category.query.filter_by(name='HVAC').first().id

            def make(title, status):
                wo = WorkOrder(site_id=1, title=title, source=workflow.SOURCE_MANUAL,
                               status=workflow.OPEN, priority_id=pri, category_id=cat)
                db.session.add(wo)
                db.session.flush()
                wo.assign_number()
                workflow.record_initial_status(wo)
                if status != workflow.OPEN:
                    workflow.apply_transition(wo, status)
                db.session.commit()

            make('Status Card Open WO', workflow.IN_PROGRESS)
            make('Status Card Pending WO', workflow.WAITING_APPROVAL)
            make('Status Card Closed WO', workflow.CANCELLED)

        r = admin_client.get('/site_details/1')
        with app.app_context():
            from application.models import WorkOrder
            from application import workflow
            counts = {s: WorkOrder.query.filter_by(site_id=1, status=s).count() for s in workflow.ALL_STATUSES}
        open_count = sum(counts[s] for s in workflow.OPEN_STATUSES if s not in workflow.WAITING_STATUSES)
        pending_count = sum(counts[s] for s in workflow.WAITING_STATUSES)
        closed_count = sum(counts[s] for s in workflow.TERMINAL_STATUSES)
        assert open_count >= 1 and pending_count >= 1 and closed_count >= 1
        body = r.get_data(as_text=True)
        # Each count renders somewhere on the page (loosely — just confirm no crash and real numbers show up).
        assert str(open_count) in body
        assert str(pending_count) in body
        assert str(closed_count) in body
        assert '/work_orders?status_filter=open&amp;site_filter=1' in body
        assert '/work_orders?status_filter=pending&amp;site_filter=1' in body
        assert '/work_orders?status_filter=closed&amp;site_filter=1' in body

    def test_work_orders_pending_and_closed_status_filters(self, app, admin_client):
        with app.app_context():
            from application.models import WorkOrder, Priority, Category
            from application import workflow
            from main import db
            pri = Priority.query.filter_by(name='High').first().id
            cat = Category.query.filter_by(name='HVAC').first().id

            def make(title, status):
                wo = WorkOrder(site_id=1, title=title, source=workflow.SOURCE_MANUAL,
                               status=workflow.OPEN, priority_id=pri, category_id=cat)
                db.session.add(wo)
                db.session.flush()
                wo.assign_number()
                workflow.record_initial_status(wo)
                if status != workflow.OPEN:
                    workflow.apply_transition(wo, status)
                db.session.commit()

            make('Filter Test Pending WO', workflow.ON_HOLD)
            make('Filter Test Closed WO', workflow.CANCELLED)

        r_pending = admin_client.get('/work_orders?status_filter=pending')
        assert b'Filter Test Pending WO' in r_pending.data
        assert b'Filter Test Closed WO' not in r_pending.data

        r_closed = admin_client.get('/work_orders?status_filter=closed')
        assert b'Filter Test Closed WO' in r_closed.data
        assert b'Filter Test Pending WO' not in r_closed.data

    def test_site_details_loads_for_own_site_user(self, user_client):
        r = user_client.get('/site_details/1')
        assert r.status_code == 200

    def test_site_details_forbidden_for_other_site_user(self, app):
        with app.app_context():
            from application.models import Site, User
            from application.utils import hash_email
            from werkzeug.security import generate_password_hash
            from main import db

            other_site = Site.query.filter_by(site_name='Other Details School').first()
            if other_site is None:
                other_site = Site(site_name='Other Details School', site_acronyms='ODS', site_code='097',
                                  site_cds='00-000-0000097', site_address='7 Other St', site_type='Elementary')
                db.session.add(other_site)
                db.session.commit()
            tech = User.query.filter_by(email_hash=hash_email('details-tech@test.com', app.config['SECRET_KEY'])).first()
            if tech is None:
                tech = User(first_name='Details', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=other_site.id)
                tech.email = 'details-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            tech_id = tech.id

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(tech_id)
                sess['_fresh'] = True
            assert c.get('/site_details/1').status_code == 403

    def test_site_details_nonexistent_site_404s(self, admin_client):
        assert admin_client.get('/site_details/999999').status_code == 404


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifications:
    def test_notifications_page_loads(self, admin_client):
        r = admin_client.get('/notifications')
        assert r.status_code == 200

    def test_add_notification(self, app, admin_client):
        r = admin_client.post('/add_notification', data={
            'msg_name': 'Test Notice',
            'msg_content': 'This is a test notification message.',
            'msg_status': 'active',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Notification
            n = Notification.query.filter_by(msg_name='Test Notice').first()
            assert n is not None

    def test_edit_notification(self, app, admin_client):
        with app.app_context():
            from application.models import Notification
            n = Notification.query.filter_by(msg_name='Test Notice').first()
            if n is None:
                pytest.skip('Test Notice not found')
            n_id = n.id

        r = admin_client.post(f'/edit_notification/{n_id}', data={
            'msg_name': 'Test Notice',
            'msg_content': 'Updated content.',
            'msg_status': 'inactive',
        }, follow_redirects=True)
        assert r.status_code == 200

    def test_delete_notification(self, app, admin_client):
        with app.app_context():
            from application.models import Notification
            n = Notification.query.filter_by(msg_name='Test Notice').first()
            if n is None:
                pytest.skip('Test Notice not found')
            n_id = n.id

        r = admin_client.post(f'/delete_notification/{n_id}', follow_redirects=True)
        assert r.status_code == 200

    def test_regular_user_cannot_access_notifications(self, user_client):
        r = user_client.get('/notifications')
        assert r.status_code == 403
