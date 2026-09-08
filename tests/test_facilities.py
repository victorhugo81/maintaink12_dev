"""
Facility / Floor / Room tests: CRUD, site-scoping, data-quality rules.
"""
import pytest


class TestFacilities:
    def test_facilities_page_loads(self, admin_client):
        r = admin_client.get('/facilities')
        assert r.status_code == 200

    def test_regular_user_can_view_facilities_list(self, user_client):
        r = user_client.get('/facilities')
        assert r.status_code == 200

    def test_regular_user_cannot_add_facility(self, user_client):
        r = user_client.get('/add_facility')
        assert r.status_code == 403

    def test_add_facility(self, app, admin_client):
        r = admin_client.post('/add_facility', data={
            'site_id': '1',
            'name': 'Main Building',
            'facility_type': 'Main Building',
            'building_code': 'MB',
            'year_built': '1998',
            'square_footage': '50000',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            assert facility is not None
            assert facility.site_id == 1
            assert facility.is_active is True

    def test_add_duplicate_facility_name_at_same_site_rejected(self, admin_client):
        r = admin_client.post('/add_facility', data={
            'site_id': '1',
            'name': 'Main Building',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'already exists' in r.data

    def test_edit_facility_page_loads(self, app, admin_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            if facility is None:
                pytest.skip('Main Building not found')
            facility_id = facility.id

        r = admin_client.get(f'/edit_facility/{facility_id}')
        assert r.status_code == 200

    def test_regular_user_can_view_own_site_facility_detail(self, app, user_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            if facility is None:
                pytest.skip('Main Building not found')
            facility_id = facility.id

        r = user_client.get(f'/edit_facility/{facility_id}')
        assert r.status_code == 200

    def test_regular_user_cannot_edit_facility(self, app, user_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            if facility is None:
                pytest.skip('Main Building not found')
            facility_id = facility.id

        r = user_client.post(f'/edit_facility/{facility_id}', data={
            'site_id': '1',
            'name': 'Hacked Building',
        })
        assert r.status_code == 403

    def test_edit_facility_updates_fields(self, app, admin_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            if facility is None:
                pytest.skip('Main Building not found')
            facility_id = facility.id

        r = admin_client.post(f'/edit_facility/{facility_id}', data={
            'site_id': '1',
            'name': 'Main Building',
            'facility_type': 'Elementary',
            'year_built': '1999',
            'is_active': 'y',  # checkbox must be resent, matching real browser behavior
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Facility
            from main import db
            facility = db.session.get(Facility, facility_id)
            assert facility.facility_type == 'Elementary'
            assert facility.year_built == 1999
            assert facility.is_active is True

    def test_nonexistent_facility_returns_404(self, admin_client):
        r = admin_client.get('/edit_facility/999999')
        assert r.status_code == 404

    def test_add_floor(self, app, admin_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            facility_id = facility.id

        r = admin_client.post(f'/add_floor/{facility_id}', data={
            'name': '1st Floor',
            'sort_order': '1',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Floor
            floor = Floor.query.filter_by(facility_id=facility_id, name='1st Floor').first()
            assert floor is not None

    def test_duplicate_floor_name_rejected(self, app, admin_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            facility_id = facility.id

        r = admin_client.post(f'/add_floor/{facility_id}', data={
            'name': '1st Floor',
            'sort_order': '1',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'already exists' in r.data

    def test_delete_facility_is_soft_delete(self, app, admin_client):
        r = admin_client.post('/add_facility', data={
            'site_id': '1',
            'name': 'Portable A',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Portable A').first()
            facility_id = facility.id

        r = admin_client.post(f'/delete_facility/{facility_id}', follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Facility
            from main import db
            facility = db.session.get(Facility, facility_id)
            # Soft delete: the row survives, just flipped inactive.
            assert facility is not None
            assert facility.is_active is False


class TestRooms:
    def test_regular_user_cannot_add_room(self, user_client):
        r = user_client.get('/add_room')
        assert r.status_code == 403

    def test_add_room(self, app, admin_client):
        with app.app_context():
            from application.models import Facility, Floor
            facility = Facility.query.filter_by(name='Main Building').first()
            floor = Floor.query.filter_by(facility_id=facility.id, name='1st Floor').first()
            facility_id, floor_id = facility.id, floor.id

        r = admin_client.post('/add_room', data={
            'facility_id': str(facility_id),
            'floor_id': str(floor_id),
            'room_number': '101',
            'room_name': 'Boiler Room',
            'room_type': 'Mechanical',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Room
            room = Room.query.filter_by(facility_id=facility_id, room_number='101').first()
            assert room is not None
            assert room.site_id == 1  # inherited from the facility, not user-supplied
            assert room.floor_id == floor_id

    def test_add_duplicate_room_number_in_same_facility_rejected(self, app, admin_client):
        with app.app_context():
            from application.models import Facility
            facility = Facility.query.filter_by(name='Main Building').first()
            facility_id = facility.id

        r = admin_client.post('/add_room', data={
            'facility_id': str(facility_id),
            'floor_id': '0',
            'room_number': '101',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'already exists' in r.data

    def test_floor_from_another_facility_rejected(self, app, admin_client):
        with app.app_context():
            from application.models import Facility, Floor
            from main import db
            other = Facility(site_id=1, name='Other Building')
            db.session.add(other)
            db.session.commit()
            other_floor = Floor(facility_id=other.id, name='Ground Floor')
            db.session.add(other_floor)
            db.session.commit()

            main = Facility.query.filter_by(name='Main Building').first()
            main_id, other_floor_id = main.id, other_floor.id

        r = admin_client.post('/add_room', data={
            'facility_id': str(main_id),
            'floor_id': str(other_floor_id),
            'room_number': '202',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'does not belong' in r.data

    def test_rooms_list_loads(self, admin_client):
        r = admin_client.get('/rooms')
        assert r.status_code == 200

    def test_room_site_scoping_for_other_site_user(self, app):
        """A user at a different site cannot open a room belonging to Main School."""
        with app.app_context():
            from application.models import Site, Role, User, Room
            from application.utils import hash_email
            from werkzeug.security import generate_password_hash
            from main import db

            other_site = Site(
                site_name='Other School', site_acronyms='OS', site_code='002',
                site_cds='00-000-0000002', site_address='1 Other St', site_type='Elementary',
            )
            db.session.add(other_site)
            db.session.flush()

            other_user = User(
                first_name='Other', last_name='Tech', status='Active',
                password=generate_password_hash('Other@Password1'),
                must_change_password=False, failed_login_attempts=0,
                role_id=3, site_id=other_site.id,
            )
            other_user.email = 'othertech@test.com'
            db.session.add(other_user)
            db.session.commit()

            room = Room.query.filter_by(room_number='101').first()
            room_id = room.id
            other_user_id = other_user.id

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(other_user_id)
                sess['_fresh'] = True
            r = c.get(f'/edit_room/{room_id}')
            assert r.status_code == 403

    def test_delete_room_is_soft_delete(self, app, admin_client):
        with app.app_context():
            from application.models import Room
            room = Room.query.filter_by(room_number='101').first()
            room_id = room.id

        r = admin_client.post(f'/delete_room/{room_id}', follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Room
            from main import db
            room = db.session.get(Room, room_id)
            assert room is not None
            assert room.is_active is False
