"""
Asset / AssetType / AssetConditionHistory tests: CRUD, condition history
append-only guarantees, QR code resolution, site-scoping.
"""
import io
import pytest


def _seed_location(app):
    """Ensure a facility + room exist for asset tests; return (facility_id, room_id)."""
    with app.app_context():
        from application.models import Facility, Room
        from main import db
        facility = Facility.query.filter_by(name='Asset Test Building').first()
        if facility is None:
            facility = Facility(site_id=1, name='Asset Test Building')
            db.session.add(facility)
            db.session.flush()
            room = Room(site_id=1, facility_id=facility.id, room_number='B-101')
            db.session.add(room)
            db.session.commit()
        room = Room.query.filter_by(facility_id=facility.id, room_number='B-101').first()
        return facility.id, room.id


class TestAssetTypes:
    def test_asset_types_page_loads(self, admin_client):
        r = admin_client.get('/asset_types')
        assert r.status_code == 200

    def test_regular_user_cannot_view_asset_types(self, user_client):
        r = user_client.get('/asset_types')
        assert r.status_code == 403

    def test_add_asset_type(self, app, admin_client):
        r = admin_client.post('/add_asset_type', data={
            'name': 'Rooftop HVAC Unit',
            'category': 'HVAC',
            'expected_life_years': '15',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import AssetType
            t = AssetType.query.filter_by(name='Rooftop HVAC Unit').first()
            assert t is not None
            assert t.expected_life_years == 15

    def test_duplicate_asset_type_rejected(self, admin_client):
        r = admin_client.post('/add_asset_type', data={'name': 'Rooftop HVAC Unit'}, follow_redirects=True)
        assert b'already exists' in r.data

    def test_edit_asset_type(self, app, admin_client):
        with app.app_context():
            from application.models import AssetType
            t = AssetType.query.filter_by(name='Rooftop HVAC Unit').first()
            type_id = t.id
        r = admin_client.post(f'/edit_asset_type/{type_id}', data={
            'name': 'Rooftop HVAC Unit',
            'category': 'HVAC',
            'expected_life_years': '20',
            'is_active': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import AssetType
            from main import db
            assert db.session.get(AssetType, type_id).expected_life_years == 20


class TestAssets:
    def test_regular_user_cannot_add_asset(self, user_client):
        assert user_client.get('/add_asset').status_code == 403

    def test_add_asset(self, app, admin_client):
        facility_id, room_id = _seed_location(app)
        with app.app_context():
            from application.models import AssetType
            type_id = AssetType.query.filter_by(name='Rooftop HVAC Unit').first().id

        r = admin_client.post('/add_asset', data={
            'facility_id': str(facility_id),
            'room_id': str(room_id),
            'asset_type_id': str(type_id),
            'asset_tag': 'HVAC-0001',
            'name': 'RTU North Wing',
            'serial_number': 'SN-12345',
            'install_date': '2015-06-01',
            'purchase_cost': '12500.00',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Asset
            a = Asset.query.filter_by(asset_tag='HVAC-0001').first()
            assert a is not None
            assert a.site_id == 1
            assert a.room_id == room_id
            assert a.condition_label is None  # not assessed yet

    def test_duplicate_asset_tag_rejected(self, app, admin_client):
        facility_id, _ = _seed_location(app)
        with app.app_context():
            from application.models import AssetType
            type_id = AssetType.query.filter_by(name='Rooftop HVAC Unit').first().id
        r = admin_client.post('/add_asset', data={
            'facility_id': str(facility_id), 'room_id': '0', 'asset_type_id': str(type_id),
            'asset_tag': 'HVAC-0001', 'name': 'Duplicate',
        }, follow_redirects=True)
        assert b'already exists' in r.data

    def test_room_from_other_facility_rejected(self, app, admin_client):
        facility_id, room_id = _seed_location(app)
        with app.app_context():
            from application.models import AssetType, Facility
            from main import db
            other = Facility(site_id=1, name='Asset Other Building')
            db.session.add(other)
            db.session.commit()
            other_id = other.id
            type_id = AssetType.query.filter_by(name='Rooftop HVAC Unit').first().id
        r = admin_client.post('/add_asset', data={
            'facility_id': str(other_id), 'room_id': str(room_id), 'asset_type_id': str(type_id),
            'asset_tag': 'HVAC-0002', 'name': 'Mismatch',
        }, follow_redirects=True)
        assert b'does not belong' in r.data

    def test_assets_list_and_search(self, admin_client):
        r = admin_client.get('/assets?search=SN-12345')
        assert r.status_code == 200
        assert b'HVAC-0001' in r.data
        r = admin_client.get('/assets?search=nomatchxyz')
        assert b'HVAC-0001' not in r.data

    def test_asset_detail_loads_for_regular_user(self, app, user_client):
        with app.app_context():
            from application.models import Asset
            asset_id = Asset.query.filter_by(asset_tag='HVAC-0001').first().id
        assert user_client.get(f'/edit_asset/{asset_id}').status_code == 200

    def test_regular_user_cannot_edit_asset(self, app, user_client):
        with app.app_context():
            from application.models import Asset
            asset_id = Asset.query.filter_by(asset_tag='HVAC-0001').first().id
        r = user_client.post(f'/edit_asset/{asset_id}', data={'asset_tag': 'HACKED'})
        assert r.status_code == 403

    def test_cannot_delete_asset_type_in_use(self, app, admin_client):
        with app.app_context():
            from application.models import AssetType
            type_id = AssetType.query.filter_by(name='Rooftop HVAC Unit').first().id
        r = admin_client.post(f'/delete_asset_type/{type_id}', follow_redirects=True)
        assert b'still assigned' in r.data
        with app.app_context():
            from application.models import AssetType
            from main import db
            assert db.session.get(AssetType, type_id) is not None


class TestConditionHistory:
    def _asset_id(self, app):
        with app.app_context():
            from application.models import Asset
            return Asset.query.filter_by(asset_tag='HVAC-0001').first().id

    def test_record_condition_appends_and_updates_current(self, app, admin_client):
        asset_id = self._asset_id(app)
        r = admin_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2026-01-15', 'score': '82', 'reason': 'Annual inspection',
        }, follow_redirects=True)
        assert r.status_code == 200
        r = admin_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2026-06-15', 'score': '40', 'reason': 'Compressor failing',
            'recommended_action': 'Replace within 12 months',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Asset
            from main import db
            a = db.session.get(Asset, asset_id)
            assert len(a.condition_history) == 2
            assert a.condition_history[0].score == 40  # newest first
            assert a.condition_history[0].condition == 'Poor'
            assert a.condition_history[1].condition == 'Good'
            assert a.condition_score == 40 and a.condition_label == 'Poor'

    def test_backdated_assessment_does_not_overwrite_current(self, app, admin_client):
        asset_id = self._asset_id(app)
        admin_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2025-01-01', 'score': '95',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Asset
            from main import db
            a = db.session.get(Asset, asset_id)
            assert len(a.condition_history) == 3
            assert a.condition_score == 40  # the 2026-06-15 entry is still current

    def test_score_out_of_range_rejected(self, app, admin_client):
        asset_id = self._asset_id(app)
        r = admin_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2026-07-01', 'score': '150',
        }, follow_redirects=True)
        assert b'between 0 and 100' in r.data
        with app.app_context():
            from application.models import AssetConditionHistory
            assert AssetConditionHistory.query.filter_by(asset_id=asset_id).count() == 3

    def test_condition_photo_links_to_entry(self, app, admin_client):
        asset_id = self._asset_id(app)
        png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00'
               b'\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n\x2d\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
        r = admin_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2026-08-01', 'score': '35',
            'photo': (io.BytesIO(png), 'damage.png'),
        }, content_type='multipart/form-data', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Asset, AssetAttachment
            from main import db
            a = db.session.get(Asset, asset_id)
            newest = a.condition_history[0]
            assert newest.score == 35
            photo = AssetAttachment.query.filter_by(condition_history_id=newest.id).first()
            assert photo is not None and photo.asset_id == asset_id

    def test_history_cannot_be_updated(self, app):
        asset_id = self._asset_id(app)
        with app.app_context():
            from application.models import AssetConditionHistory, ConditionHistoryImmutableError
            from main import db
            entry = AssetConditionHistory.query.filter_by(asset_id=asset_id).first()
            entry.score = 1
            with pytest.raises(ConditionHistoryImmutableError):
                db.session.commit()
            db.session.rollback()
            db.session.expire_all()
            assert AssetConditionHistory.query.get(entry.id).score != 1

    def test_history_cannot_be_deleted(self, app):
        asset_id = self._asset_id(app)
        with app.app_context():
            from application.models import AssetConditionHistory, ConditionHistoryImmutableError
            from main import db
            before = AssetConditionHistory.query.filter_by(asset_id=asset_id).count()
            entry = AssetConditionHistory.query.filter_by(asset_id=asset_id).first()
            db.session.delete(entry)
            with pytest.raises(ConditionHistoryImmutableError):
                db.session.commit()
            db.session.rollback()
            assert AssetConditionHistory.query.filter_by(asset_id=asset_id).count() == before

    def test_regular_user_cannot_record_condition(self, app, user_client):
        asset_id = self._asset_id(app)
        r = user_client.post(f'/add_asset_condition/{asset_id}', data={
            'assessed_at': '2026-08-02', 'score': '50',
        })
        assert r.status_code == 403


class TestAssetQR:
    def test_qr_png_served(self, app, admin_client):
        with app.app_context():
            from application.models import Asset
            asset_id = Asset.query.filter_by(asset_tag='HVAC-0001').first().id
        r = admin_client.get(f'/asset_qr/{asset_id}.png')
        assert r.status_code == 200
        assert r.mimetype == 'image/png'
        assert r.data.startswith(b'\x89PNG')

    def test_qr_url_resolves_to_asset_detail(self, app, admin_client):
        with app.app_context():
            from application.models import Asset
            from application.routes import asset_qr_url
            from flask import url_for
            asset = Asset.query.filter_by(asset_tag='HVAC-0001').first()
            with app.test_request_context():
                encoded = asset_qr_url(asset)
                expected = url_for('routes.edit_asset', asset_id=asset.id, _external=True)
        assert encoded == expected
        # The encoded URL is the detail page — fetch its path and confirm it's the asset.
        from urllib.parse import urlparse
        r = admin_client.get(urlparse(encoded).path)
        assert r.status_code == 200
        assert b'HVAC-0001' in r.data

    def test_qr_respects_site_scoping(self, app):
        with app.app_context():
            from application.models import Site, User, Asset
            from werkzeug.security import generate_password_hash
            from main import db
            site = Site.query.filter_by(site_name='QR Other School').first()
            if site is None:
                site = Site(site_name='QR Other School', site_acronyms='QO', site_code='009',
                            site_cds='00-000-0000009', site_address='9 Other St', site_type='Elementary')
                db.session.add(site)
                db.session.flush()
                u = User(first_name='QR', last_name='Tech', status='Active',
                         password=generate_password_hash('Other@Password1'),
                         must_change_password=False, failed_login_attempts=0,
                         role_id=3, site_id=site.id)
                u.email = 'qrtech@test.com'
                db.session.add(u)
                db.session.commit()
            from application.utils import hash_email
            uid = User.query.filter_by(email_hash=hash_email('qrtech@test.com', app.config['SECRET_KEY'])).first().id
            asset_id = Asset.query.filter_by(asset_tag='HVAC-0001').first().id
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(uid)
                sess['_fresh'] = True
            assert c.get(f'/asset_qr/{asset_id}.png').status_code == 403
            assert c.get(f'/edit_asset/{asset_id}').status_code == 403


class TestAssetSoftDelete:
    def test_delete_asset_is_soft(self, app, admin_client):
        with app.app_context():
            from application.models import Asset
            asset_id = Asset.query.filter_by(asset_tag='HVAC-0001').first().id
        r = admin_client.post(f'/delete_asset/{asset_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Asset, AssetConditionHistory
            from main import db
            a = db.session.get(Asset, asset_id)
            assert a is not None and a.is_active is False
            assert AssetConditionHistory.query.filter_by(asset_id=asset_id).count() == 4
