"""
Work order tests: reference data, requester submission flow, staff creation,
status workflow (valid/invalid transitions, data-quality rules), assignment,
comments, and site-/ownership-scoping.
"""
import pytest


def _seed_facility(app):
    """A facility + room at site 1 for work orders; returns (facility_id, room_id)."""
    with app.app_context():
        from application.models import Facility, Room
        from main import db
        f = Facility.query.filter_by(name='WO Test Building').first()
        if f is None:
            f = Facility(site_id=1, name='WO Test Building')
            db.session.add(f)
            db.session.flush()
            db.session.add(Room(site_id=1, facility_id=f.id, room_number='W-12'))
            db.session.commit()
        r = Room.query.filter_by(facility_id=f.id, room_number='W-12').first()
        return f.id, r.id


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='HVAC').first().id)


def _user_id(app, email):
    with app.app_context():
        from application.models import User
        from application.utils import hash_email
        return User.query.filter_by(email_hash=hash_email(email, app.config['SECRET_KEY'])).first().id


def _make_user(app, email, role_id, site_id, first='Test'):
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


def _client_for(app, user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
    return c


WO_TITLE = 'Room W-12 is too hot'


def _wo(app, title=WO_TITLE):
    """
    Look up by title, not by an assumed wo_number/id. WorkOrder ids are a
    single auto-increment sequence shared with every other test module in
    this session-scoped DB (including tests/test_pm.py's PM-generated work
    orders), so "the first work order ever created is always WO-000001" only
    held before test_pm.py existed — it's fragile by construction. Every
    other cross-module lookup in this project's tests already keys off a
    distinguishing name/title for the same reason (see Facility/Room/Asset
    tests' `filter_by(name=...)` helpers).
    """
    with app.app_context():
        from application.models import WorkOrder
        return WorkOrder.query.filter_by(title=title).first()


class TestReferenceData:
    def test_defaults_seeded(self, app):
        with app.app_context():
            from application.models import Priority, Category
            assert Priority.query.count() == 5
            assert Category.query.count() == 24
            assert Priority.query.order_by(Priority.sort_order).first().name == 'Emergency'

    def test_priorities_page_loads_for_admin(self, admin_client):
        assert admin_client.get('/priorities').status_code == 200

    def test_priorities_forbidden_for_regular_user(self, user_client):
        assert user_client.get('/priorities').status_code == 403
        assert user_client.get('/categories').status_code == 403

    def test_add_priority_and_duplicate(self, app, admin_client):
        r = admin_client.post('/add_priority', data={'name': 'Deferred', 'sort_order': '6', 'color': 'secondary'}, follow_redirects=True)
        assert r.status_code == 200
        r = admin_client.post('/add_priority', data={'name': 'Deferred', 'sort_order': '6', 'color': 'secondary'}, follow_redirects=True)
        assert b'already exists' in r.data

    def test_add_subcategory(self, app, admin_client):
        _, cat_id = _ids(app)
        r = admin_client.post(f'/add_subcategory/{cat_id}', data={'name': 'Rooftop Unit'}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Subcategory
            assert Subcategory.query.filter_by(category_id=cat_id, name='Rooftop Unit').first() is not None


class TestRequesterFlow:
    def test_request_page_loads_for_regular_user(self, app, user_client):
        _seed_facility(app)
        r = user_client.get('/request_work_order')
        assert r.status_code == 200
        assert b'WO Test Building' in r.data

    def test_submit_request(self, app, user_client):
        facility_id, room_id = _seed_facility(app)
        pri, cat = _ids(app)
        r = user_client.post('/request_work_order', data={
            'title': 'Room W-12 is too hot',
            'facility_id': str(facility_id), 'room_id': str(room_id),
            'category_id': str(cat), 'priority_id': str(pri),
            'description': 'Thermostat reads 84F all day.',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'submitted' in r.data

        with app.app_context():
            from application.models import WorkOrder
            from application import workflow
            wo = WorkOrder.query.filter_by(title='Room W-12 is too hot').first()
            assert wo is not None
            assert wo.wo_number == f'WO-{wo.id:06d}'
            assert wo.source == workflow.SOURCE_REQUEST
            assert wo.status == workflow.NEW
            assert wo.site_id == 1 and wo.facility_id == facility_id and wo.room_id == room_id
            assert wo.requester_id == _user_id(app, 'user@test.com')
            assert wo.assigned_to_id is None
            assert len(wo.status_history) == 1
            assert wo.status_history[0].from_status is None and wo.status_history[0].to_status == 'New'

    def test_room_from_other_facility_rejected(self, app, user_client):
        _, room_id = _seed_facility(app)
        pri, cat = _ids(app)
        with app.app_context():
            from application.models import Facility
            from main import db
            other = Facility(site_id=1, name='WO Other Building')
            db.session.add(other)
            db.session.commit()
            other_id = other.id
        r = user_client.post('/request_work_order', data={
            'title': 'Mismatch', 'facility_id': str(other_id), 'room_id': str(room_id),
            'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert b'does not belong' in r.data

    def test_requester_sees_own_request_in_list_and_detail(self, app, user_client):
        wo = _wo(app)
        r = user_client.get('/work_orders')
        assert wo.wo_number.encode() in r.data
        r = user_client.get(f'/edit_work_order/{wo.id}')
        assert r.status_code == 200
        assert b'Change Status' not in r.data  # requesters cannot change status

    def test_requester_cannot_edit(self, app, user_client):
        wo = _wo(app)
        r = user_client.post(f'/edit_work_order/{wo.id}', data={'title': 'hacked'})
        assert r.status_code == 403

    def test_requester_can_comment(self, app, user_client):
        wo = _wo(app)
        r = user_client.post(f'/add_work_order_comment/{wo.id}', data={'content': 'Still hot today.'}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrderComment
            assert WorkOrderComment.query.filter_by(work_order_id=wo.id).count() == 1

    def test_other_regular_user_cannot_see_it(self, app):
        wo = _wo(app)
        other_id = _make_user(app, 'other-teacher@test.com', role_id=4, site_id=1, first='Other')
        c = _client_for(app, other_id)
        assert c.get(f'/edit_work_order/{wo.id}').status_code == 403
        assert wo.wo_number.encode() not in c.get('/work_orders').data


class TestStaffFlow:
    def test_regular_user_cannot_open_staff_form(self, user_client):
        assert user_client.get('/add_work_order').status_code == 403

    def test_manual_work_order_without_requester_and_with_assignee(self, app, admin_client):
        facility_id, _ = _seed_facility(app)
        pri, cat = _ids(app)
        tech_id = _make_user(app, 'wo-tech@test.com', role_id=3, site_id=1, first='Tech')
        r = admin_client.post('/add_work_order', data={
            'title': 'Quarterly boiler check', 'site_id': '1', 'facility_id': str(facility_id),
            'room_id': '0', 'asset_id': '0', 'category_id': str(cat), 'subcategory_id': '0',
            'priority_id': str(pri), 'assigned_to_id': str(tech_id), 'assigned_team': '',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import WorkOrder
            from application import workflow
            wo = WorkOrder.query.filter_by(title='Quarterly boiler check').first()
            assert wo.source == workflow.SOURCE_MANUAL
            assert wo.requester_id is None
            assert wo.assigned_to_id == tech_id
            assert wo.status == workflow.ASSIGNED
            assert [h.to_status for h in wo.status_history] == ['Assigned', 'New']

    def test_technician_at_other_site_cannot_see_it(self, app):
        wo = _wo(app)
        with app.app_context():
            from application.models import Site
            from main import db
            site = Site.query.filter_by(site_name='WO Other School').first()
            if site is None:
                site = Site(site_name='WO Other School', site_acronyms='WOS', site_code='077',
                            site_cds='00-000-0000077', site_address='7 Other St', site_type='Elementary')
                db.session.add(site)
                db.session.commit()
            other_site_id = site.id
        tech_id = _make_user(app, 'far-tech@test.com', role_id=3, site_id=other_site_id, first='Far')
        c = _client_for(app, tech_id)
        assert c.get(f'/edit_work_order/{wo.id}').status_code == 403
        assert c.post(f'/change_work_order_status/{wo.id}', data={'new_status': 'In Progress'}).status_code == 403

    def test_assigning_a_new_work_order_moves_it_to_assigned(self, app, admin_client):
        wo = _wo(app)
        tech_id = _user_id(app, 'wo-tech@test.com')
        pri, cat = _ids(app)
        r = admin_client.post(f'/edit_work_order/{wo.id}', data={
            'title': wo.title, 'site_id': '1', 'facility_id': str(wo.facility_id), 'room_id': str(wo.room_id),
            'asset_id': '0', 'category_id': str(cat), 'subcategory_id': '0', 'priority_id': str(pri),
            'assigned_to_id': str(tech_id), 'assigned_team': '',
        }, follow_redirects=True)
        assert r.status_code == 200
        wo = _wo(app)
        assert wo.assigned_to_id == tech_id
        assert wo.status == 'Assigned'


class TestWorkflow:
    def test_transition_matrix(self):
        from application import workflow as w
        assert w.can_transition(w.NEW, w.IN_PROGRESS)
        assert w.can_transition(w.IN_PROGRESS, w.COMPLETED)
        assert w.can_transition(w.COMPLETED, w.CLOSED)
        assert not w.can_transition(w.NEW, w.CLOSED)
        assert not w.can_transition(w.NEW, w.COMPLETED)
        assert not w.can_transition(w.CLOSED, w.COMPLETED)
        assert w.can_transition(w.CANCELLED, w.NEW)  # reopen
        for s in w.ALL_STATUSES:
            assert not w.can_transition(s, s)

    def test_apply_transition_rejects_unknown_status(self, app):
        from application import workflow as w
        wo = _wo(app)
        with app.app_context():
            from main import db
            wo = db.session.merge(wo)
            with pytest.raises(w.WorkflowError):
                w.apply_transition(wo, 'Bogus')
            db.session.rollback()

    def test_invalid_transition_via_route_is_rejected(self, app, admin_client):
        wo = _wo(app)  # currently Assigned
        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={'new_status': 'Closed'}, follow_redirects=True)
        assert b'valid status' in r.data or b'Cannot move' in r.data
        assert _wo(app).status == 'Assigned'

    def test_in_progress_sets_started_at(self, app, admin_client):
        wo = _wo(app)
        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={
            'new_status': 'In Progress', 'note': 'On site',
        }, follow_redirects=True)
        assert b'Status changed to In Progress' in r.data
        wo = _wo(app)
        assert wo.status == 'In Progress' and wo.started_at is not None
        with app.app_context():
            from main import db
            wo = db.session.merge(wo)
            assert wo.status_history[0].note == 'On site'

    def test_completed_requires_valid_completion_date(self, app, admin_client):
        wo = _wo(app)
        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={
            'new_status': 'Completed', 'completed_at': '2999-01-01',
        }, follow_redirects=True)
        assert b'cannot be in the future' in r.data
        assert _wo(app).status == 'In Progress'

        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={
            'new_status': 'Completed', 'completed_at': '2026-09-05',
        }, follow_redirects=True)
        assert b'Status changed to Completed' in r.data
        wo = _wo(app)
        assert wo.status == 'Completed'
        assert wo.completed_at.date().isoformat() == '2026-09-05'

    def test_closed_requires_resolution(self, app, admin_client):
        wo = _wo(app)
        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={'new_status': 'Closed'}, follow_redirects=True)
        assert b'resolution is required' in r.data
        assert _wo(app).status == 'Completed'

        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={
            'new_status': 'Closed', 'resolution': 'Replaced thermostat and recalibrated damper.',
        }, follow_redirects=True)
        assert b'Status changed to Closed' in r.data
        wo = _wo(app)
        assert wo.status == 'Closed' and wo.closed_at is not None
        assert 'thermostat' in wo.resolution

    def test_status_history_is_complete(self, app):
        wo = _wo(app)
        with app.app_context():
            from main import db
            wo = db.session.merge(wo)
            trail = [h.to_status for h in reversed(wo.status_history)]
            assert trail == ['New', 'Assigned', 'In Progress', 'Completed', 'Closed']

    def test_reopen_closed_work_order(self, app, admin_client):
        wo = _wo(app)
        r = admin_client.post(f'/change_work_order_status/{wo.id}', data={
            'new_status': 'In Progress', 'note': 'Reopened — still hot',
        }, follow_redirects=True)
        assert b'Status changed to In Progress' in r.data
        wo = _wo(app)
        assert wo.status == 'In Progress' and wo.closed_at is None and wo.completed_at is None

    def test_closed_list_filter(self, app, admin_client):
        wo_number = _wo(app).wo_number.encode()
        r = admin_client.get('/work_orders?status_filter=Closed')
        assert wo_number not in r.data
        r = admin_client.get('/work_orders?status_filter=open')
        assert wo_number in r.data

    def test_cannot_delete_priority_in_use(self, app, admin_client):
        pri, _ = _ids(app)
        r = admin_client.post(f'/delete_priority/{pri}', follow_redirects=True)
        assert b'still use' in r.data


