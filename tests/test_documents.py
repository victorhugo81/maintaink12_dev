"""
Document Library (IncidentIQ-inspired addition): aggregates FacilityAttachment,
RoomAttachment, AssetAttachment, WorkOrderAttachment, InspectionAttachment,
and ProjectDocument into one searchable /documents view. Rows are created
directly in the DB (not real uploads) since the route only lists existing
attachment rows — it doesn't touch the filesystem.
"""
import pytest


def _site2(app):
    with app.app_context():
        from application.models import Site
        from main import db
        if not Site.query.filter_by(id=2).first():
            db.session.add(Site(id=2, site_name='Doc Other Site', site_acronyms='DO', site_code='777',
                                site_cds='7', site_address='x', site_type='Elementary'))
            db.session.commit()
        return 2


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


class TestDocumentLibrary:
    def test_regular_user_cannot_view_library(self, user_client):
        assert user_client.get('/documents').status_code == 403

    def test_aggregates_multiple_attachment_types(self, app, admin_client):
        fid = _facility(app, 'Doc Lib Building')
        with app.app_context():
            from application.models import FacilityAttachment, WorkOrder, WorkOrderAttachment, Priority, Category
            from application import workflow
            from main import db
            pri = Priority.query.filter_by(name='High').first().id
            cat = Category.query.filter_by(name='HVAC').first().id
            wo = WorkOrder(site_id=1, facility_id=fid, title='Doc Lib WO', source='Manual',
                          status=workflow.OPEN, priority_id=pri, category_id=cat)
            db.session.add(wo)
            db.session.flush()
            wo.assign_number()
            db.session.add(FacilityAttachment(facility_id=fid, attach_file='doclib_facility_manual.pdf'))
            db.session.add(WorkOrderAttachment(work_order_id=wo.id, attach_file='doclib_wo_photo.png'))
            db.session.commit()

        r = admin_client.get('/documents')
        assert r.status_code == 200
        assert b'doclib_facility_manual.pdf' in r.data
        assert b'doclib_wo_photo.png' in r.data
        assert b'Doc Lib Building' in r.data
        assert b'Doc Lib WO' in r.data or b'DOC-' in r.data.upper() or b'WO-' in r.data

    def test_filename_filter_narrows_results(self, app, admin_client):
        fid = _facility(app, 'Doc Lib Filter Building')
        with app.app_context():
            from application.models import FacilityAttachment
            from main import db
            db.session.add(FacilityAttachment(facility_id=fid, attach_file='uniquematchtoken.pdf'))
            db.session.add(FacilityAttachment(facility_id=fid, attach_file='unrelated_other_file.pdf'))
            db.session.commit()

        r = admin_client.get('/documents?q=uniquematchtoken')
        assert r.status_code == 200
        assert b'uniquematchtoken.pdf' in r.data
        assert b'unrelated_other_file.pdf' not in r.data

    def test_type_filter_narrows_results(self, app, admin_client):
        fid = _facility(app, 'Doc Lib Type Building')
        with app.app_context():
            from application.models import FacilityAttachment, RoomAttachment, Room
            from main import db
            room = Room.query.filter_by(facility_id=fid, room_number='DLT-1').first()
            if room is None:
                room = Room(site_id=1, facility_id=fid, room_number='DLT-1')
                db.session.add(room)
                db.session.commit()
            db.session.add(FacilityAttachment(facility_id=fid, attach_file='typefilter_facility.pdf'))
            db.session.add(RoomAttachment(room_id=room.id, attach_file='typefilter_room.pdf'))
            db.session.commit()

        r = admin_client.get('/documents?type=facility')
        assert r.status_code == 200
        assert b'typefilter_facility.pdf' in r.data
        assert b'typefilter_room.pdf' not in r.data

    def test_site_scoped_user_does_not_see_other_site_attachments(self, app):
        from application.models import User
        from application.utils import hash_email
        from werkzeug.security import generate_password_hash

        other_site_id = _site2(app)
        fid_mine = _facility(app, 'Doc Lib Mine Building', site_id=1)
        fid_other = _facility(app, 'Doc Lib Other Building', site_id=other_site_id)

        with app.app_context():
            from application.models import FacilityAttachment
            from main import db
            db.session.add(FacilityAttachment(facility_id=fid_mine, attach_file='doclib_mine_file.pdf'))
            db.session.add(FacilityAttachment(facility_id=fid_other, attach_file='doclib_other_file.pdf'))
            db.session.commit()

            tech = User.query.filter_by(email_hash=hash_email('doclib-tech@test.com', app.config['SECRET_KEY'])).first()
            if tech is None:
                tech = User(first_name='DocLib', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=1)
                tech.email = 'doclib-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            tech_id = tech.id

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(tech_id)
                sess['_fresh'] = True
            r = c.get('/documents')
            assert r.status_code == 200
            assert b'doclib_mine_file.pdf' in r.data
            assert b'doclib_other_file.pdf' not in r.data
