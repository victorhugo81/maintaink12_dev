"""
Inspection tests: template/item CRUD, target validation, template-to-result
mapping, failed-item work-order generation, and due/overdue bucketing.
"""
from datetime import date
import pytest


def _ids(app):
    with app.app_context():
        from application.models import Priority, Category
        return (Priority.query.filter_by(name='High').first().id,
                Category.query.filter_by(name='Fire/Life Safety').first().id)


def _facility_room_asset(app):
    """A facility + room + asset at site 1, reused across inspection tests."""
    with app.app_context():
        from application.models import Facility, Room, AssetType, Asset
        from main import db
        f = Facility.query.filter_by(name='Inspection Test Building').first()
        if f is None:
            f = Facility(site_id=1, name='Inspection Test Building')
            db.session.add(f)
            db.session.flush()
            db.session.add(Room(site_id=1, facility_id=f.id, room_number='I-1'))
            db.session.commit()
        room = Room.query.filter_by(facility_id=f.id, room_number='I-1').first()

        at = AssetType.query.filter_by(name='Fire Extinguisher').first()
        if at is None:
            at = AssetType(name='Fire Extinguisher')
            db.session.add(at)
            db.session.commit()
        asset = Asset.query.filter_by(asset_tag='EXT-1').first()
        if asset is None:
            asset = Asset(site_id=1, facility_id=f.id, room_id=room.id, asset_type_id=at.id,
                          asset_tag='EXT-1', name='Hallway Extinguisher')
            db.session.add(asset)
            db.session.commit()
        return f.id, room.id, asset.id


def _make_template(app, name='Fire Extinguisher Inspection', questions=None):
    questions = questions or ['Present?', 'Accessible?', 'Pressure OK?', 'Tag current?']
    pri, cat = _ids(app)
    with app.app_context():
        from application.models import InspectionTemplate, InspectionItem
        from main import db
        t = InspectionTemplate.query.filter_by(name=name).first()
        if t is None:
            t = InspectionTemplate(name=name, category_id=cat, priority_id=pri)
            db.session.add(t)
            db.session.flush()
            for i, q in enumerate(questions):
                db.session.add(InspectionItem(template_id=t.id, question=q, sort_order=i))
            db.session.commit()
        return t.id


class TestInspectionTemplateCRUD:
    def test_regular_user_cannot_view_templates(self, user_client):
        assert user_client.get('/inspection_templates').status_code == 403

    def test_add_template_via_route(self, app, admin_client):
        pri, cat = _ids(app)
        r = admin_client.post('/add_inspection_template', data={
            'name': 'Playground Safety Check', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import InspectionTemplate
            t = InspectionTemplate.query.filter_by(name='Playground Safety Check').first()
            assert t is not None and t.is_active is True

    def test_duplicate_template_name_rejected(self, admin_client, app):
        pri, cat = _ids(app)
        r = admin_client.post('/add_inspection_template', data={
            'name': 'Playground Safety Check', 'category_id': str(cat), 'priority_id': str(pri),
        }, follow_redirects=True)
        assert b'already exists' in r.data

    def test_add_and_deactivate_item(self, app, admin_client):
        template_id = _make_template(app, name='Solo Item Template', questions=['Q1?'])
        r = admin_client.post(f'/add_inspection_item/{template_id}', data={'question': 'Q2?', 'sort_order': '1'}, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import InspectionItem
            item = InspectionItem.query.filter_by(template_id=template_id, question='Q2?').first()
            assert item is not None
            item_id = item.id
        # Unused item: hard delete.
        r = admin_client.post(f'/delete_inspection_item/{item_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import InspectionItem
            from main import db
            assert db.session.get(InspectionItem, item_id) is None

    def test_cannot_delete_template_with_inspections(self, app, admin_client):
        template_id = _make_template(app)
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-10-01',
        }, follow_redirects=True)
        r = admin_client.post(f'/delete_inspection_template/{template_id}', follow_redirects=True)
        assert b'Cannot delete' in r.data


class TestScheduling:
    def test_regular_user_cannot_schedule(self, user_client):
        assert user_client.get('/add_inspection').status_code == 403

    def test_schedule_against_asset(self, app, admin_client):
        template_id = _make_template(app)
        _, _, asset_id = _facility_room_asset(app)
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': '0', 'room_id': '0', 'asset_id': str(asset_id),
            'due_date': '2026-10-15',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Inspection
            insp = Inspection.query.filter_by(template_id=template_id, asset_id=asset_id).first()
            assert insp is not None
            assert insp.site_id == 1
            assert insp.status == 'Scheduled'
            assert insp.facility_id is None and insp.room_id is None

    def test_choosing_two_targets_rejected(self, app, admin_client):
        template_id = _make_template(app)
        facility_id, room_id, _ = _facility_room_asset(app)
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': str(room_id), 'asset_id': '0',
            'due_date': '2026-10-15',
        }, follow_redirects=True)
        assert b'exactly one' in r.data

    def test_choosing_no_target_rejected(self, app, admin_client):
        template_id = _make_template(app)
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': '0', 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-10-15',
        }, follow_redirects=True)
        assert b'exactly one' in r.data

    def test_scheduled_inspection_deletable_completed_is_not(self, app, admin_client):
        template_id = _make_template(app, name='Deletable Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-01',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp = Inspection.query.filter_by(template_id=template_id).first()
            insp_id = insp.id
        r = admin_client.post(f'/delete_inspection/{insp_id}', follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Inspection
            from main import db
            assert db.session.get(Inspection, insp_id) is None


class TestTemplateToResultMapping:
    def test_all_active_items_get_a_result_row(self, app, admin_client):
        template_id = _make_template(app, name='Mapping Template', questions=['Q1?', 'Q2?', 'Q3?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-10-20',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection, InspectionItem
            insp = Inspection.query.filter_by(template_id=template_id).first()
            insp_id = insp.id
            items = InspectionItem.query.filter_by(template_id=template_id).order_by(InspectionItem.sort_order).all()
            item_ids = [i.id for i in items]

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Pass', 'items-0-notes': '',
            'items-1-result': 'Fail', 'items-1-notes': 'Cracked housing',
            'items-2-result': 'Needs Attention', 'items-2-notes': '',
            'generate_work_order': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Inspection, InspectionResult
            from main import db
            insp = db.session.get(Inspection, insp_id)
            assert insp.status == 'Completed'
            assert insp.completed_at is not None
            results = InspectionResult.query.filter_by(inspection_id=insp_id).all()
            assert len(results) == 3
            by_item = {r.inspection_item_id: r for r in results}
            assert set(by_item.keys()) == set(item_ids)
            assert by_item[item_ids[0]].result == 'Pass'
            assert by_item[item_ids[1]].result == 'Fail'
            assert by_item[item_ids[1]].notes == 'Cracked housing'
            assert by_item[item_ids[2]].result == 'Needs Attention'

    def test_missing_result_for_an_item_rejected(self, app, admin_client):
        template_id = _make_template(app, name='Incomplete Template', questions=['Q1?', 'Q2?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-10-21',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Pass',
            # items-1 omitted entirely
            'generate_work_order': 'y',
        }, follow_redirects=True)
        assert b'Every checklist item requires a result' in r.data
        with app.app_context():
            from application.models import Inspection, InspectionResult
            from main import db
            insp = db.session.get(Inspection, insp_id)
            assert insp.status == 'Scheduled'  # not completed
            assert InspectionResult.query.filter_by(inspection_id=insp_id).count() == 0

    def test_deactivated_item_excluded_from_new_inspections(self, app, admin_client):
        template_id = _make_template(app, name='Partial Deactivation Template', questions=['Keep?', 'Drop?'])
        with app.app_context():
            from application.models import InspectionItem
            from main import db
            drop_item = InspectionItem.query.filter_by(template_id=template_id, question='Drop?').first()
            drop_item.is_active = False
            db.session.commit()

        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-10-22',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id
        r = admin_client.get(f'/edit_inspection/{insp_id}')
        assert b'Keep?' in r.data
        assert b'Drop?' not in r.data

    def test_regular_user_cannot_record_results(self, app, user_client):
        template_id = _make_template(app, name='Access Control Template', questions=['Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        with app.app_context():
            from application.models import Inspection
            from main import db
            insp = Inspection(template_id=template_id, site_id=1, facility_id=facility_id, due_date=date(2026, 10, 25))
            db.session.add(insp)
            db.session.commit()
            insp_id = insp.id
        r = user_client.post(f'/record_inspection_results/{insp_id}', data={'items-0-result': 'Pass'})
        assert r.status_code == 403


class TestFailedItemWorkOrderGeneration:
    def test_failure_generates_work_order_with_correct_classification(self, app, admin_client):
        pri, cat = _ids(app)
        template_id = _make_template(app, name='WO-Gen Template', questions=['Charged?', 'Sealed?'])
        _, _, asset_id = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': '0', 'room_id': '0', 'asset_id': str(asset_id),
            'due_date': '2026-10-30',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Fail', 'items-0-notes': 'No charge shown on gauge',
            'items-1-result': 'Pass', 'items-1-notes': '',
            'generate_work_order': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'Work order' in r.data

        with app.app_context():
            from application.models import Inspection, WorkOrder
            from application import workflow
            from main import db
            insp = db.session.get(Inspection, insp_id)
            assert insp.generated_work_order_id is not None
            wo = db.session.get(WorkOrder, insp.generated_work_order_id)
            assert wo.source == workflow.SOURCE_INSPECTION
            assert wo.category_id == cat and wo.priority_id == pri
            assert wo.asset_id == asset_id
            assert 'No charge shown on gauge' in wo.description
            assert 'Sealed?' not in wo.description  # only the failed item is listed

    def test_all_pass_generates_no_work_order(self, app, admin_client):
        template_id = _make_template(app, name='All Pass Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-05',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Pass', 'generate_work_order': 'y',
        }, follow_redirects=True)
        assert b'all items passed' in r.data
        with app.app_context():
            from application.models import Inspection
            from main import db
            assert db.session.get(Inspection, insp_id).generated_work_order_id is None

    def test_opt_out_of_generation_leaves_failure_unaddressed(self, app, admin_client):
        template_id = _make_template(app, name='Opt Out Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-06',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Fail',
            # generate_work_order checkbox omitted entirely == unchecked
        }, follow_redirects=True)
        assert b'no work order generated' in r.data
        with app.app_context():
            from application.models import Inspection
            from main import db
            assert db.session.get(Inspection, insp_id).generated_work_order_id is None

    def test_needs_attention_alone_does_not_generate_a_work_order(self, app, admin_client):
        """Only Fail triggers generation — Needs Attention is informational."""
        template_id = _make_template(app, name='Needs Attention Only Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-07',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Needs Attention', 'generate_work_order': 'y',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            from main import db
            assert db.session.get(Inspection, insp_id).generated_work_order_id is None


class TestDueOverdueLogic:
    def test_buckets_split_correctly_including_boundary(self):
        from application.inspections import dashboard_buckets

        class FakeInspection:
            def __init__(self, due):
                self.due_date = due

        today = date(2026, 6, 15)
        items = [
            FakeInspection(date(2026, 6, 1)),    # overdue
            FakeInspection(date(2026, 6, 14)),   # overdue (yesterday)
            FakeInspection(date(2026, 6, 15)),   # due today
            FakeInspection(date(2026, 6, 22)),   # due this week (exact +7 boundary)
            FakeInspection(date(2026, 6, 23)),   # upcoming (+8)
        ]
        buckets = dashboard_buckets(items, today=today)
        assert len(buckets['overdue']) == 2
        assert len(buckets['due_today']) == 1
        assert len(buckets['due_this_week']) == 1
        assert len(buckets['upcoming']) == 1

    def test_completed_inspections_excluded_from_dashboard(self, app, admin_client):
        template_id = _make_template(app, name='Dashboard Exclusion Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2020-01-01',  # deliberately far overdue
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.get('/inspection_dashboard')
        assert r.status_code == 200
        assert b'Dashboard Exclusion Template' in r.data  # shows up while Scheduled + overdue

        admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Pass', 'generate_work_order': 'y',
        }, follow_redirects=True)

        r = admin_client.get('/inspection_dashboard')
        assert b'Dashboard Exclusion Template' not in r.data  # completed, no longer "due"

    def test_regular_user_cannot_view_dashboard(self, user_client):
        assert user_client.get('/inspection_dashboard').status_code == 403

    def test_technician_sees_only_own_site_inspections(self, app):
        # Only `app` — see application/CLAUDE.md's "Test-writing gotchas" note on why
        # mixing multiple authenticated clients within one test is avoided project-wide.
        template_id = _make_template(app, name='Site Scoped Inspection Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        with app.app_context():
            from application.models import Inspection, Site, User
            from application.utils import hash_email
            from werkzeug.security import generate_password_hash
            from main import db

            insp = Inspection(template_id=template_id, site_id=1, facility_id=facility_id, due_date=date(2026, 1, 1))
            db.session.add(insp)
            db.session.commit()
            insp_id = insp.id

            other_site = Site.query.filter_by(site_name='Inspection Other School').first()
            if other_site is None:
                other_site = Site(site_name='Inspection Other School', site_acronyms='IOS', site_code='099',
                                  site_cds='00-000-0000099', site_address='9 Other St', site_type='Elementary')
                db.session.add(other_site)
                db.session.commit()
            tech = User.query.filter_by(email_hash=hash_email('insp-tech@test.com', app.config['SECRET_KEY'])).first()
            if tech is None:
                tech = User(first_name='Insp', last_name='Tech', status='Active',
                           password=generate_password_hash('Some@Password1'), must_change_password=False,
                           failed_login_attempts=0, role_id=3, site_id=other_site.id)
                tech.email = 'insp-tech@test.com'
                db.session.add(tech)
                db.session.commit()
            tech_id = tech.id

        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(tech_id)
                sess['_fresh'] = True
            r = c.get('/inspection_dashboard')
            assert r.status_code == 200
            assert b'Site Scoped Inspection Template' not in r.data
            assert c.get(f'/edit_inspection/{insp_id}').status_code == 403
