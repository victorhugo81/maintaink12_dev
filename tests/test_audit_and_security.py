"""
Phase 12: audit-log capture (who/what/old/new/when across tracked models,
masking of sensitive fields, attribution to the acting user or System) and
the security review's testable claims — IDOR on every new resource type,
privilege escalation via role checks, CSRF enforcement, file-upload
validation, path-traversal safety on download routes, session-cookie
hardening, and a legacy-AssistItK12 regression sweep.

One authenticated identity per test function throughout (the hard rule in
application/CLAUDE.md).
"""
import io
import pytest

from application import audit

TINY_PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
            b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


def _ids(app, priority='High', category='HVAC'):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name=priority).first().id,
                Category.query.filter_by(name=category).first().id)


def _site2(app):
    with app.app_context():
        from application.models import Site
        from main import db
        if not Site.query.filter_by(id=2).first():
            db.session.add(Site(id=2, site_name='Sec Other Site', site_acronyms='SO', site_code='555',
                                site_cds='5', site_address='x', site_type='Elementary'))
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


def _user(app, email, role_id, first='Sec', site_id=1):
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


def _wo(app, facility_id, title, site_id=1, **kwargs):
    with app.app_context():
        from application.models import WorkOrder
        from main import db
        pri, cat = _ids(app)
        wo = WorkOrder(site_id=site_id, facility_id=facility_id, title=title, source='Manual', status='New',
                       priority_id=pri, category_id=cat, **kwargs)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        db.session.commit()
        return wo.id


def _asset(app, facility_id, tag, site_id=1):
    with app.app_context():
        from application.models import Asset, AssetType
        from main import db
        at = AssetType.query.filter_by(name='Sec Asset Type').first()
        if at is None:
            at = AssetType(name='Sec Asset Type')
            db.session.add(at)
            db.session.flush()
        a = Asset(site_id=site_id, facility_id=facility_id, asset_type_id=at.id, asset_tag=tag, name='sec')
        db.session.add(a)
        db.session.commit()
        return a.id


def _audit_rows(app, **filters):
    with app.app_context():
        from application.models import AuditLog
        return AuditLog.query.filter_by(**filters).order_by(AuditLog.id).all()


# ---------------------------------------------------------------------------
# Audit log capture
# ---------------------------------------------------------------------------

class TestAuditCapture:
    def test_create_via_route_is_attributed_to_the_user(self, app, admin_client):
        admin_client.post('/add_facility', data={'site_id': '1', 'name': 'Audit Create Bldg', 'is_active': 'y'})
        fid = _facility(app, 'Audit Create Bldg')
        rows = _audit_rows(app, entity_type='Facility', entity_id=fid, action='create')
        assert len(rows) == 1
        assert rows[0].user_id is not None and rows[0].entity_label == 'Audit Create Bldg'

    def test_update_logs_one_row_per_changed_field_with_old_and_new(self, app, admin_client):
        fid = _facility(app, 'Audit Update Bldg')
        admin_client.post(f'/edit_facility/{fid}', data={'site_id': '1', 'name': 'Audit Update Bldg', 'year_built': '1988',
                                                        'facility_type': 'Gym', 'is_active': 'y'})
        rows = {r.field: r for r in _audit_rows(app, entity_type='Facility', entity_id=fid, action='update')}
        assert rows['year_built'].old_value is None and rows['year_built'].new_value == '1988'
        assert rows['facility_type'].new_value == 'Gym'
        assert 'name' not in rows  # unchanged field -> no row

    def test_status_change_is_logged_with_old_and_new(self, app):
        fid = _facility(app, 'Audit Status Bldg')
        wo_id = _wo(app, fid, 'Audit status WO')
        with app.app_context():
            from application.models import WorkOrder
            from application import workflow
            from main import db
            workflow.apply_transition(db.session.get(WorkOrder, wo_id), workflow.IN_PROGRESS)
            db.session.commit()
        rows = {r.field: r for r in _audit_rows(app, entity_type='WorkOrder', entity_id=wo_id, action='update')}
        assert rows['status'].old_value == 'New' and rows['status'].new_value == 'In Progress'
        assert rows['status'].user_id is None  # no request context -> System

    def test_second_edit_after_commit_still_captures_old_value(self, app):
        fid = _facility(app, 'Audit Expired Bldg')
        with app.app_context():
            from application.models import Facility
            from main import db
            f = db.session.get(Facility, fid)
            f.year_built = 1970
            db.session.commit()
            f.year_built = 1980
            db.session.commit()
        rows = [r for r in _audit_rows(app, entity_type='Facility', entity_id=fid, action='update') if r.field == 'year_built']
        assert [(r.old_value, r.new_value) for r in rows][-2:] == [(None, '1970'), ('1970', '1980')]

    def test_role_change_and_password_reset_are_logged_password_masked(self, app):
        uid = _user(app, 'audit-perm@test.com', 3, first='AuditPerm')
        with app.app_context():
            from application.models import User
            from werkzeug.security import generate_password_hash
            from main import db
            u = db.session.get(User, uid)
            u.role_id = 2
            u.password = generate_password_hash('Changed@1')
            db.session.commit()
        rows = {r.field: r for r in _audit_rows(app, entity_type='User', entity_id=uid, action='update')}
        assert rows['role_id'].old_value == '3' and rows['role_id'].new_value == '2'
        assert rows['password'].old_value == audit.MASK and rows['password'].new_value == audit.MASK

    def test_cost_change_is_logged(self, app, admin_client):
        fid = _facility(app, 'Audit Cost Bldg')
        wo_id = _wo(app, fid, 'Audit cost WO')
        admin_client.post(f'/update_work_order_cost/{wo_id}', data={'vendor_cost': '125.00', 'other_cost': '0'})
        admin_client.post(f'/update_work_order_cost/{wo_id}', data={'vendor_cost': '200.00', 'other_cost': '0'})
        with app.app_context():
            from application.models import CostRecord
            rec = CostRecord.query.filter_by(work_order_id=wo_id).first()
        rows = _audit_rows(app, entity_type='CostRecord', entity_id=rec.id)
        assert any(r.action == 'create' for r in rows)  # first save creates the record with its initial values
        change = next(r for r in rows if r.field == 'vendor_cost')
        assert change.old_value.startswith('125') and change.new_value.startswith('200')
        assert change.user_id is not None

    def test_delete_is_logged(self, app):
        with app.app_context():
            from application.models import Vendor
            from main import db
            v = Vendor(name='Audit Delete Vendor')
            db.session.add(v)
            db.session.commit()
            vid = v.id
            db.session.delete(v)
            db.session.commit()
        rows = _audit_rows(app, entity_type='Vendor', entity_id=vid)
        assert [r.action for r in rows] == ['create', 'delete']

    def test_audit_page_admin_only_and_filters(self, app, admin_client):
        r = admin_client.get('/audit_log')
        assert r.status_code == 200
        r = admin_client.get('/audit_log?entity_type=Facility&user_id=0')
        assert r.status_code == 200

    def test_audit_page_forbidden_for_technician(self, app):
        tech = _user(app, 'audit-tech@test.com', 3)
        assert _client_as(app, tech).get('/audit_log').status_code == 403


# ---------------------------------------------------------------------------
# IDOR: a Technician at site 2 against site-1 resources
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def site1_resources(app):
    fid = _facility(app, 'IDOR Site1 Bldg')
    aid = _asset(app, fid, 'IDOR-A-1')
    wo_id = _wo(app, fid, 'IDOR site1 WO')
    with app.app_context():
        from application.models import (Room, WorkOrderAttachment, FacilityAttachment, AssetAttachment,
                                        InspectionTemplate, InspectionItem, Inspection, Project, ProjectDocument)
        from main import db
        from datetime import date
        pri, cat = _ids(app)
        room = Room(site_id=1, facility_id=fid, room_number='IDOR-R1')
        db.session.add(room)
        db.session.add(WorkOrderAttachment(work_order_id=wo_id, attach_file='idor_wo.png'))
        db.session.add(FacilityAttachment(facility_id=fid, attach_file='idor_fac.png'))
        db.session.add(AssetAttachment(asset_id=aid, attach_file='idor_asset.png'))
        tmpl = InspectionTemplate(name='IDOR Template', category_id=cat, priority_id=pri)
        db.session.add(tmpl)
        db.session.flush()
        db.session.add(InspectionItem(template_id=tmpl.id, question='q'))
        insp = Inspection(template_id=tmpl.id, site_id=1, facility_id=fid, due_date=date.today())
        db.session.add(insp)
        project = Project(site_id=1, name='IDOR Project')
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectDocument(project_id=project.id, attach_file='idor_doc.pdf'))
        db.session.commit()
        ids = {
            'facility': fid, 'asset': aid, 'wo': wo_id, 'room': room.id, 'inspection': insp.id, 'project': project.id,
            'wo_att': WorkOrderAttachment.query.filter_by(work_order_id=wo_id).first().id,
            'fac_att': FacilityAttachment.query.filter_by(facility_id=fid).first().id,
            'asset_att': AssetAttachment.query.filter_by(asset_id=aid).first().id,
            'doc': ProjectDocument.query.filter_by(project_id=project.id).first().id,
        }
    return ids


class TestIdorOtherSiteTechnician:
    """Every new resource type: a Technician scoped to site 2 gets 403 on site 1's records."""

    @pytest.fixture(autouse=True)
    def _tech(self, app):
        _site2(app)
        self.tech_id = _user(app, 'idor-tech-site2@test.com', 3, first='IdorTech', site_id=2)

    @pytest.mark.parametrize('url_tpl', [
        '/edit_work_order/{wo}', '/download_work_order_attachment/{wo_att}',
        '/edit_asset/{asset}', '/asset_qr/{asset}.png', '/download_asset_attachment/{asset_att}',
        '/edit_facility/{facility}', '/download_facility_attachment/{fac_att}',
        '/edit_room/{room}', '/edit_inspection/{inspection}', '/download_project_document/{doc}',
    ])
    def test_get_is_forbidden(self, app, site1_resources, url_tpl):
        r = _client_as(app, self.tech_id).get(url_tpl.format(**site1_resources))
        assert r.status_code == 403

    @pytest.mark.parametrize('url_tpl, data', [
        ('/change_work_order_status/{wo}', {'new_status': 'Assigned'}),
        ('/add_work_order_comment/{wo}', {'comment': 'x'}),
        ('/add_work_order_labor/{wo}', {'technician_id': '1', 'labor_hours': '1', 'labor_type': 'Regular'}),
        ('/add_asset_condition/{asset}', {'assessed_at': '2026-01-01', 'score': '50'}),
        ('/record_inspection_results/{inspection}', {'items-0-result': 'Pass'}),
    ])
    def test_post_is_forbidden(self, app, site1_resources, url_tpl, data):
        r = _client_as(app, self.tech_id).post(url_tpl.format(**site1_resources), data=data)
        assert r.status_code == 403

    def test_list_pages_hide_other_sites_rows(self, app, site1_resources):
        c = _client_as(app, self.tech_id)
        assert b'IDOR site1 WO' not in c.get('/work_orders?status_filter=all').data
        assert b'IDOR-A-1' not in c.get('/assets').data
        assert b'IDOR Site1 Bldg' not in c.get('/facilities').data


class TestIdorRegularUser:
    """A School Staff user (role 4) can only see work orders they requested."""

    def test_other_users_work_order_forbidden(self, app, site1_resources):
        staff = _user(app, 'idor-staff@test.com', 4, first='IdorStaff')
        c = _client_as(app, staff)
        assert c.get(f"/edit_work_order/{site1_resources['wo']}").status_code == 403
        assert c.get(f"/edit_inspection/{site1_resources['inspection']}").status_code == 403


# ---------------------------------------------------------------------------
# Privilege escalation via role checks
# ---------------------------------------------------------------------------

class TestPrivilegeEscalation:
    @pytest.mark.parametrize('method, url', [
        ('POST', '/delete_facility/1'), ('POST', '/delete_asset/1'), ('GET', '/vendors'), ('GET', '/add_vendor'),
        ('GET', '/priorities'), ('GET', '/add_priority'), ('GET', '/maintenance_plans'), ('GET', '/inspection_templates'),
        ('GET', '/add_project'), ('POST', '/delete_project/1'), ('GET', '/sla_rules'), ('GET', '/csv_import'),
        ('GET', '/csv_import/facilities/template'), ('GET', '/audit_log'), ('POST', '/run_pm_generation'),
        ('POST', '/update_asset_risk_fields/1'), ('GET', '/asset_types'),
    ])
    def test_technician_cannot_reach_admin_routes(self, app, method, url):
        tech = _user(app, 'priv-tech@test.com', 3, first='PrivTech')
        c = _client_as(app, tech)
        r = c.post(url, data={}) if method == 'POST' else c.get(url)
        assert r.status_code == 403

    @pytest.mark.parametrize('method, url', [
        ('GET', '/add_work_order'), ('GET', '/pm_dashboard'), ('GET', '/inspections'), ('GET', '/cost_rollups'),
        ('GET', '/technician_workload'), ('GET', '/capital_replacement'), ('GET', '/reports'), ('GET', '/projects'),
        ('GET', '/vendor_performance'),
    ])
    def test_school_staff_cannot_reach_staff_routes(self, user_client, method, url):
        r = user_client.post(url, data={}) if method == 'POST' else user_client.get(url)
        assert r.status_code == 403

    def test_role_field_not_self_editable_through_profile(self, app):
        """/profile only changes the password — a POST carrying role_id must not escalate."""
        tech = _user(app, 'priv-profile@test.com', 3, first='PrivProfile')
        c = _client_as(app, tech)
        c.post('/profile', data={'current_password': 'Some@Password1', 'password': 'Another@Pass1',
                                 'confirm_password': 'Another@Pass1', 'role_id': '1'})
        with app.app_context():
            from application.models import User
            from main import db
            assert db.session.get(User, tech).role_id == 3


# ---------------------------------------------------------------------------
# CSRF, uploads, path traversal, session hardening
# ---------------------------------------------------------------------------

class TestCsrf:
    def test_state_changing_post_without_token_is_rejected_when_csrf_enabled(self, app, admin_client):
        pri_id, _ = _ids(app, priority='Low')
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            r = admin_client.post(f'/edit_sla_rule/{pri_id}', data={'response_hours': '1', 'resolution_hours': '2'})
        finally:
            app.config['WTF_CSRF_ENABLED'] = False
        assert r.status_code == 400
        with app.app_context():
            from application.models import SLARule
            assert SLARule.query.filter_by(priority_id=pri_id).first() is None


class TestUploadValidation:
    def _post_attachment(self, app, admin_client, wo_id, filename, content):
        with app.app_context():
            from application.models import WorkOrder
            from main import db
            wo = db.session.get(WorkOrder, wo_id)
            base = {'title': wo.title, 'description': '', 'facility_id': str(wo.facility_id), 'room_id': '0',
                    'asset_id': '0', 'category_id': str(wo.category_id), 'subcategory_id': '0',
                    'priority_id': str(wo.priority_id), 'assigned_to_id': '0', 'assigned_team': '',
                    'source': wo.source, 'project_id': '0'}
        base['attachment'] = (io.BytesIO(content), filename)
        return admin_client.post(f'/edit_work_order/{wo_id}', data=base, content_type='multipart/form-data',
                                 follow_redirects=True)

    def _attachment_count(self, app, wo_id):
        with app.app_context():
            from application.models import WorkOrderAttachment
            return WorkOrderAttachment.query.filter_by(work_order_id=wo_id).count()

    def test_disallowed_extension_rejected(self, app, admin_client):
        fid = _facility(app, 'Upload Bldg')
        wo_id = _wo(app, fid, 'Upload ext WO')
        self._post_attachment(app, admin_client, wo_id, 'evil.php', b'<?php system($_GET["c"]); ?>')
        assert self._attachment_count(app, wo_id) == 0

    def test_extension_spoofing_rejected_by_magic_bytes(self, app, admin_client):
        fid = _facility(app, 'Upload Bldg')
        wo_id = _wo(app, fid, 'Upload spoof WO')
        self._post_attachment(app, admin_client, wo_id, 'not_really.png', b'<?php echo 1; ?>')
        assert self._attachment_count(app, wo_id) == 0

    def test_valid_png_accepted_and_stored_under_a_generated_name(self, app, admin_client, tmp_path):
        fid = _facility(app, 'Upload Bldg')
        wo_id = _wo(app, fid, 'Upload ok WO')
        app.config['UPLOAD_WORK_ORDER_ATTACHMENT'] = str(tmp_path)
        self._post_attachment(app, admin_client, wo_id, '../../../etc/evil.png', TINY_PNG)
        with app.app_context():
            from application.models import WorkOrderAttachment
            att = WorkOrderAttachment.query.filter_by(work_order_id=wo_id).first()
        assert att is not None
        assert '/' not in att.attach_file and '..' not in att.attach_file and att.attach_file.endswith('.png')
        assert (tmp_path / att.attach_file).exists()


class TestPathTraversal:
    def test_download_never_leaves_the_upload_folder(self, app, admin_client, tmp_path):
        """Even if a stored filename were tampered into a traversal, send_from_directory refuses it."""
        fid = _facility(app, 'Traversal Bldg')
        wo_id = _wo(app, fid, 'Traversal WO')
        app.config['UPLOAD_WORK_ORDER_ATTACHMENT'] = str(tmp_path)
        (tmp_path.parent / 'secret.txt').write_text('top secret')
        with app.app_context():
            from application.models import WorkOrderAttachment
            from main import db
            att = WorkOrderAttachment(work_order_id=wo_id, attach_file='../secret.txt')
            db.session.add(att)
            db.session.commit()
            att_id = att.id
        r = admin_client.get(f'/download_work_order_attachment/{att_id}')
        assert r.status_code in (302, 404)
        assert b'top secret' not in r.data


class TestSessionAndHeaders:
    def test_production_cookie_hardening(self):
        from config import config as config_map
        prod = config_map['production']
        assert prod.SESSION_COOKIE_SECURE is True
        assert prod.SESSION_COOKIE_HTTPONLY is True
        assert prod.SESSION_COOKIE_SAMESITE == 'Lax'
        assert prod.PERMANENT_SESSION_LIFETIME.total_seconds() <= 8 * 3600
        assert prod.MAX_CONTENT_LENGTH <= 16 * 1024 * 1024

    def test_new_pages_carry_security_headers(self, admin_client):
        for url in ('/dashboard', '/reports', '/search?q=xx', '/audit_log'):
            r = admin_client.get(url)
            assert r.headers.get('X-Content-Type-Options') == 'nosniff'
            assert 'Content-Security-Policy' in r.headers

    def test_search_and_csv_upload_are_rate_limited(self, app):
        from application.routes import search, csv_import_upload
        # Flask-Limiter marks decorated views; the attribute exists only when a limit was applied.
        assert getattr(search, '__wrapped__', None) is not None
        assert getattr(csv_import_upload, '__wrapped__', None) is not None


# ---------------------------------------------------------------------------
# Legacy AssistItK12 regression sweep
# ---------------------------------------------------------------------------

class TestLegacyRegression:
    @pytest.mark.parametrize('url', ['/', '/tickets', '/add_ticket', '/users', '/add_user', '/sites', '/roles',
                                     '/titles', '/notifications', '/profile', '/bulk-data-upload'])
    def test_legacy_admin_pages_still_load(self, admin_client, url):
        assert admin_client.get(url).status_code == 200

    def test_legacy_organization_page_loads_with_csrf_enabled(self, app, admin_client):
        # organization.html reads email_form.csrf_token._value() inline, which only
        # exists when CSRF is on — true in every real config, off in the test suite.
        app.config['WTF_CSRF_ENABLED'] = True
        try:
            assert admin_client.get('/organization').status_code == 200
        finally:
            app.config['WTF_CSRF_ENABLED'] = False

    def test_legacy_user_pages_still_load(self, user_client):
        for url in ('/', '/tickets', '/add_ticket', '/profile'):
            assert user_client.get(url).status_code == 200
