"""
Global Search and CSV Import tests: search relevance/scoping against known
fixtures, and CSV import validation/dedup/rollback-on-error behavior.
"""
import io
import pytest

from application import search, csv_import


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


def _asset_type(app, name='Search Asset Type'):
    with app.app_context():
        from application.models import AssetType
        from main import db
        at = AssetType.query.filter_by(name=name).first()
        if at is None:
            at = AssetType(name=name)
            db.session.add(at)
            db.session.commit()
        return at.id


def _site2(app):
    with app.app_context():
        from application.models import Site
        from main import db
        if not Site.query.filter_by(id=2).first():
            db.session.add(Site(id=2, site_name='Search Other Site', site_acronyms='OS', site_code='777',
                                site_cds='7', site_address='x', site_type='Elementary'))
            db.session.commit()
        return 2


def _user(app, email, role_id, first='Search', site_id=1):
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


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------

class TestGlobalSearch:
    def test_below_minimum_length_returns_nothing(self, app):
        with app.app_context():
            assert search.global_search('a') == []
            assert search.global_search('') == []
            assert search.global_search(None) == []

    def test_finds_work_order_by_number_and_title(self, app):
        fid = _facility(app, 'Search WO Building')
        with app.app_context():
            from application.models import WorkOrder, Priority, Category
            from main import db
            pri = Priority.query.filter_by(name='High').first().id
            cat = Category.query.filter_by(name='HVAC').first().id
            wo = WorkOrder(site_id=1, facility_id=fid, title='Unique Leaky Roof Report', source='Manual',
                           status='New', priority_id=pri, category_id=cat)
            db.session.add(wo)
            db.session.flush()
            wo.assign_number()
            db.session.commit()
            wo_number = wo.wo_number
            by_number = search.global_search(wo_number)
            by_title = search.global_search('Leaky Roof')
        assert any(label == 'Work Orders' for label, _ in by_number)
        assert any(row['title'].startswith(wo_number) for _, rows in by_number for row in rows)
        assert any(label == 'Work Orders' for label, _ in by_title)

    def test_finds_asset_by_tag_and_serial(self, app):
        fid = _facility(app, 'Search Asset Building')
        type_id = _asset_type(app)
        with app.app_context():
            from application.models import Asset
            from main import db
            db.session.add(Asset(site_id=1, facility_id=fid, asset_type_id=type_id,
                                 asset_tag='SEARCH-TAG-1', name='Findable Asset', serial_number='SN-UNIQUE-123'))
            db.session.commit()
            by_tag = search.global_search('SEARCH-TAG-1')
            by_serial = search.global_search('SN-UNIQUE-123')
        assert any(row['title'].startswith('SEARCH-TAG-1') for _, rows in by_tag for row in rows)
        assert any(row['title'].startswith('SEARCH-TAG-1') for _, rows in by_serial for row in rows)

    def test_finds_facility_room_vendor_project(self, app):
        fid = _facility(app, 'Search Findable Facility')
        with app.app_context():
            from application.models import Room, Vendor, Project
            from main import db
            db.session.add(Room(site_id=1, facility_id=fid, room_number='SEARCHROOM1', room_name='Findable Room'))
            db.session.add(Vendor(name='Search Findable Vendor'))
            db.session.add(Project(site_id=1, name='Search Findable Project'))
            db.session.commit()
            fac_results = search.global_search('Search Findable Facility')
            room_results = search.global_search('SEARCHROOM1')
            vendor_results = search.global_search('Search Findable Vendor')
            project_results = search.global_search('Search Findable Project')
        assert any(label == 'Facilities' for label, _ in fac_results)
        assert any(label == 'Rooms' for label, _ in room_results)
        assert any(label == 'Vendors' for label, _ in vendor_results)
        assert any(label == 'Projects' for label, _ in project_results)

    def test_site_scoping_excludes_other_sites(self, app):
        other_site = _site2(app)
        other_fid = _facility(app, 'Search Scoped Other Site Facility', site_id=other_site)
        with app.app_context():
            from application.models import Facility
            unrestricted = search.global_search('Search Scoped Other Site Facility', site_ids=None)
            scoped = search.global_search('Search Scoped Other Site Facility', site_ids=[1])
        assert any(label == 'Facilities' for label, _ in unrestricted)
        assert scoped == []  # facility belongs to site 2, not in the allowed [1]

    def test_users_only_included_when_include_users_true(self, app):
        _user(app, 'search-findme@test.com', 3, first='Findablefirstname')
        with app.app_context():
            without = search.global_search('Findablefirstname', include_users=False)
            with_users = search.global_search('Findablefirstname', include_users=True)
        assert without == []
        assert any(label == 'Users' for label, _ in with_users)

    def test_route_hides_users_group_for_non_admin(self, app):
        # The search box echoes the query text into its value="..." attribute
        # regardless of results, so assert on the rendered full name (as a
        # result row would show it) rather than the bare query string, and
        # "Users" itself also appears in the nav sidebar as a management link.
        _user(app, 'search-route-findme@test.com', 3, first='Routefindableuser')
        tech_id = _user(app, 'search-route-tech@test.com', 3, first='RouteSearchTech')
        tech_client = _client_as(app, tech_id)
        r_tech = tech_client.get('/search?q=Routefindableuser')
        assert b'font-weight-bold">Routefindableuser' not in r_tech.data

    def test_route_shows_users_group_for_admin(self, app):
        _user(app, 'search-route-findme2@test.com', 3, first='Routefindableuser2')
        admin_id = _user(app, 'search-route-admin@test.com', 1, first='RouteSearchAdmin')
        admin_client = _client_as(app, admin_id)
        r_admin = admin_client.get('/search?q=Routefindableuser2')
        assert b'font-weight-bold">Routefindableuser2' in r_admin.data

    def test_route_too_short_message(self, admin_client):
        r = admin_client.get('/search?q=a')
        assert r.status_code == 200 and b'at least 2 characters' in r.data

    def test_route_requires_login(self, client):
        assert client.get('/search?q=test').status_code in (302, 401)


# ---------------------------------------------------------------------------
# CSV Import — validation
# ---------------------------------------------------------------------------

class TestCsvImportValidation:
    def test_facility_valid_row_imports(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV New Facility', 'facility_type': '', 'building_code': '',
                    'address': '', 'year_built': '2000', 'square_footage': '', 'notes': ''}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'valid'
        assert len(valid) == 1 and valid[0]['name'] == 'CSV New Facility'

    def test_facility_missing_required_field_is_error(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': ''}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'error' and 'name' in report[0]['message']
        assert valid == []

    def test_facility_unknown_site_is_error(self, app):
        with app.app_context():
            rows = [{'site_name': 'Nonexistent Site XYZ', 'name': 'X'}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'error' and 'Nonexistent Site XYZ' in report[0]['message']

    def test_facility_duplicate_in_db_is_flagged(self, app):
        _facility(app, 'CSV Existing Facility')
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV Existing Facility'}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'duplicate' and valid == []

    def test_facility_duplicate_within_file_is_flagged(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV Same Row Twice'},
                   {'site_name': 'Main School', 'name': 'CSV Same Row Twice'}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'valid'
        assert report[1]['status'] == 'duplicate' and 'earlier row' in report[1]['message']
        assert len(valid) == 1

    def test_facility_bad_number_is_error(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV Bad Year', 'year_built': 'not-a-year'}]
            report, valid = csv_import.validate_rows('facilities', rows)
        assert report[0]['status'] == 'error' and 'year_built' in report[0]['message']

    def test_valid_and_invalid_rows_coexist_in_one_file(self, app):
        with app.app_context():
            rows = [
                {'site_name': 'Main School', 'name': 'CSV Mixed Valid'},
                {'site_name': 'Main School', 'name': ''},
                {'site_name': 'Bogus Site', 'name': 'CSV Mixed Bad Site'},
            ]
            report, valid = csv_import.validate_rows('facilities', rows)
        statuses = [r['status'] for r in report]
        assert statuses == ['valid', 'error', 'error']
        assert len(valid) == 1

    def test_room_requires_existing_facility(self, app):
        _facility(app, 'CSV Room Parent Facility')
        with app.app_context():
            rows = [{'site_name': 'Main School', 'facility_name': 'CSV Room Parent Facility', 'room_number': '200'}]
            report, valid = csv_import.validate_rows('rooms', rows)
        assert report[0]['status'] == 'valid'

    def test_room_unknown_facility_is_error(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'facility_name': 'Does Not Exist', 'room_number': '1'}]
            report, valid = csv_import.validate_rows('rooms', rows)
        assert report[0]['status'] == 'error'

    def test_asset_requires_existing_type(self, app):
        fid = _facility(app, 'CSV Asset Parent Facility')
        with app.app_context():
            rows = [{'site_name': 'Main School', 'facility_name': 'CSV Asset Parent Facility',
                    'asset_tag': 'CSV-A-1', 'name': 'Csv Asset', 'asset_type_name': 'Nonexistent Type'}]
            report, valid = csv_import.validate_rows('assets', rows)
        assert report[0]['status'] == 'error' and 'Nonexistent Type' in report[0]['message']

    def test_asset_bad_date_is_error(self, app):
        fid = _facility(app, 'CSV Asset Date Facility')
        type_id = _asset_type(app)
        with app.app_context():
            rows = [{'site_name': 'Main School', 'facility_name': 'CSV Asset Date Facility', 'asset_tag': 'CSV-A-2',
                    'name': 'Csv Asset 2', 'asset_type_name': 'Search Asset Type', 'install_date': '13/45/2020'}]
            report, valid = csv_import.validate_rows('assets', rows)
        assert report[0]['status'] == 'error' and 'install_date' in report[0]['message']

    def test_asset_duplicate_tag_globally(self, app):
        fid = _facility(app, 'CSV Asset Dup Facility')
        type_id = _asset_type(app)
        with app.app_context():
            from application.models import Asset
            from main import db
            db.session.add(Asset(site_id=1, facility_id=fid, asset_type_id=type_id, asset_tag='CSV-DUP-TAG', name='x'))
            db.session.commit()
            rows = [{'site_name': 'Main School', 'facility_name': 'CSV Asset Dup Facility', 'asset_tag': 'CSV-DUP-TAG',
                    'name': 'y', 'asset_type_name': 'Search Asset Type'}]
            report, valid = csv_import.validate_rows('assets', rows)
        assert report[0]['status'] == 'duplicate'

    def test_vendor_valid_and_bad_email(self, app):
        with app.app_context():
            ok, _ = csv_import.validate_rows('vendors', [{'name': 'CSV Vendor OK', 'email': 'ok@example.com'}])
            bad, _ = csv_import.validate_rows('vendors', [{'name': 'CSV Vendor Bad', 'email': 'not-an-email'}])
        assert ok[0]['status'] == 'valid'
        assert bad[0]['status'] == 'error'

    def test_user_requires_existing_role_and_site(self, app):
        with app.app_context():
            rows = [{'first_name': 'Csv', 'last_name': 'User', 'email': 'csv-user@test.com',
                    'role_name': 'Technician', 'site_name': 'Main School'}]
            report, valid = csv_import.validate_rows('users', rows)
            bad_role, _ = csv_import.validate_rows('users', [{'first_name': 'Csv', 'last_name': 'User',
                'email': 'csv-user2@test.com', 'role_name': 'Nonexistent Role', 'site_name': 'Main School'}])
        assert report[0]['status'] == 'valid'
        assert bad_role[0]['status'] == 'error'

    def test_user_duplicate_email_is_flagged(self, app):
        with app.app_context():
            from application.utils import hash_email
            from application.models import User
            existing = User.query.filter_by(email_hash=hash_email('admin@test.com', 'test-secret-key-for-pytest')).first()
            assert existing is not None
            rows = [{'first_name': 'Dup', 'last_name': 'User', 'email': 'admin@test.com',
                    'role_name': 'Admin', 'site_name': 'Main School'}]
            report, valid = csv_import.validate_rows('users', rows)
        assert report[0]['status'] == 'duplicate'


# ---------------------------------------------------------------------------
# CSV Import — commit & rollback
# ---------------------------------------------------------------------------

class TestCsvImportCommit:
    def test_commit_writes_valid_rows(self, app):
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV Commit Facility'}]
            report, valid = csv_import.validate_rows('facilities', rows)
            count, err = csv_import.commit_rows('facilities', valid)
            from application.models import Facility
            created = Facility.query.filter_by(name='CSV Commit Facility').first()
        assert count == 1 and err is None
        assert created is not None

    def test_commit_empty_valid_list_is_a_noop(self, app):
        with app.app_context():
            count, err = csv_import.commit_rows('facilities', [])
        assert count == 0 and err is None

    def test_commit_failure_rolls_back_entire_batch(self, app, monkeypatch):
        """Simulates an unexpected DB-level failure mid-commit: nothing from
        this batch should be written, even the rows before the failure."""
        with app.app_context():
            rows = [{'site_name': 'Main School', 'name': 'CSV Rollback A'},
                   {'site_name': 'Main School', 'name': 'CSV Rollback B'}]
            report, valid = csv_import.validate_rows('facilities', rows)
            assert len(valid) == 2

            def boom(data):
                raise RuntimeError('simulated database failure')

            monkeypatch.setitem(csv_import.ENTITY_SPECS['facilities'], 'build', boom)
            count, err = csv_import.commit_rows('facilities', valid)
            from application.models import Facility
            a = Facility.query.filter_by(name='CSV Rollback A').first()
            b = Facility.query.filter_by(name='CSV Rollback B').first()
        assert count == 0 and err is not None
        assert a is None and b is None

    def test_partial_failure_does_not_corrupt_prior_successful_import(self, app, monkeypatch):
        """A failed batch must not affect rows already committed by an
        earlier, successful import."""
        with app.app_context():
            # First, a clean successful import.
            report1, valid1 = csv_import.validate_rows('facilities', [{'site_name': 'Main School', 'name': 'CSV Prior Success'}])
            count1, err1 = csv_import.commit_rows('facilities', valid1)
            assert count1 == 1 and err1 is None

            # Then a batch that fails during commit.
            report2, valid2 = csv_import.validate_rows('facilities', [{'site_name': 'Main School', 'name': 'CSV Then Fails'}])

            def boom(data):
                raise RuntimeError('simulated failure')
            monkeypatch.setitem(csv_import.ENTITY_SPECS['facilities'], 'build', boom)
            count2, err2 = csv_import.commit_rows('facilities', valid2)

            from application.models import Facility
            prior_still_exists = Facility.query.filter_by(name='CSV Prior Success').first() is not None
            failed_one_absent = Facility.query.filter_by(name='CSV Then Fails').first() is None
        assert count2 == 0 and err2 is not None
        assert prior_still_exists and failed_one_absent


# ---------------------------------------------------------------------------
# CSV Import — routes
# ---------------------------------------------------------------------------

class TestCsvImportRoutes:
    def test_index_requires_admin(self, user_client):
        assert user_client.get('/csv_import').status_code == 403

    def test_index_ok_for_admin(self, admin_client):
        assert admin_client.get('/csv_import').status_code == 200

    def test_unknown_entity_type_404s(self, admin_client):
        assert admin_client.get('/csv_import/bogus').status_code == 404
        assert admin_client.get('/csv_import/bogus/template').status_code == 404

    def test_template_download(self, admin_client):
        r = admin_client.get('/csv_import/vendors/template')
        assert r.status_code == 200
        assert r.headers['Content-Type'].startswith('text/csv')
        assert 'vendors_import_template.csv' in r.headers['Content-Disposition']
        assert b'name' in r.data

    def test_upload_report_shows_success_duplicate_error_counts(self, admin_client):
        csv_text = ('site_name,name,facility_type,building_code,address,year_built,square_footage,notes\n'
                   'Main School,CSV Route Valid,,,,,,\n'
                   'Main School,CSV Route Valid,,,,,,\n'
                   'Bogus Site,CSV Route Bad,,,,,,\n')
        data = {'csv_file': (io.BytesIO(csv_text.encode()), 'facilities.csv')}
        r = admin_client.post('/csv_import/facilities', data=data, content_type='multipart/form-data')
        assert r.status_code == 200
        body = r.data.decode()
        assert 'imported' in body.lower() and 'duplicate' in body.lower() and 'error' in body.lower()

    def test_upload_report_route_actually_creates_the_facility(self, app, admin_client):
        csv_text = 'site_name,name\nMain School,CSV Route Created Facility\n'
        data = {'csv_file': (io.BytesIO(csv_text.encode()), 'facilities.csv')}
        admin_client.post('/csv_import/facilities', data=data, content_type='multipart/form-data')
        with app.app_context():
            from application.models import Facility
            assert Facility.query.filter_by(name='CSV Route Created Facility').first() is not None

    def test_upload_logs_to_csv_import_log(self, app, admin_client):
        csv_text = 'name\nCSV Log Vendor\n'
        data = {'csv_file': (io.BytesIO(csv_text.encode()), 'vendors.csv')}
        admin_client.post('/csv_import/vendors', data=data, content_type='multipart/form-data')
        with app.app_context():
            from application.models import CsvImportLog
            log = CsvImportLog.query.filter_by(entity_type='vendors').order_by(CsvImportLog.id.desc()).first()
        assert log is not None and log.success_count >= 1 and log.status == 'success'

    def test_non_csv_file_rejected(self, admin_client):
        data = {'csv_file': (io.BytesIO(b'not a csv'), 'facilities.txt')}
        r = admin_client.post('/csv_import/facilities', data=data, content_type='multipart/form-data', follow_redirects=True)
        assert r.status_code == 200
        assert b'CSV files only' in r.data or b'CSV' in r.data
