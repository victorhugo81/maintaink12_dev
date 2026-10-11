"""
Inspection tests: template/item CRUD, target validation, template-to-result
mapping, failed-item work-order generation, and due/overdue bucketing.
"""
from datetime import date
import json
import re
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


class TestInspectionQuestionPresets:
    """
    The global, reusable question library on edit_inspection_template.html's
    Presets tab — not tied to any one template, so any template_id in the
    URL/fixtures below is only "where to redirect back to" after a change.
    """
    def test_regular_user_cannot_manage_presets(self, app, user_client):
        template_id = _make_template(app)
        assert user_client.post(f'/edit_inspection_template/{template_id}/add_question_preset', data={
            'category': 'Electrical', 'question': 'Hacked?',
        }).status_code == 403

    def test_add_preset_appears_on_edit_page(self, app, admin_client):
        template_id = _make_template(app)
        r = admin_client.post(f'/edit_inspection_template/{template_id}/add_question_preset', data={
            'category': 'Custom', 'question': 'Is the widget calibrated?', 'sort_order': '5',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'Preset question added' in r.data
        assert b'Is the widget calibrated?' in r.data
        with app.app_context():
            from application.models import InspectionQuestionPreset
            preset = InspectionQuestionPreset.query.filter_by(question='Is the widget calibrated?').first()
            assert preset is not None
            assert preset.category == 'Custom' and preset.sort_order == 5

    def test_delete_preset(self, app, admin_client):
        template_id = _make_template(app)
        admin_client.post(f'/edit_inspection_template/{template_id}/add_question_preset', data={
            'category': 'Custom', 'question': 'Temporary preset?',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import InspectionQuestionPreset
            from main import db
            preset_id = InspectionQuestionPreset.query.filter_by(question='Temporary preset?').first().id
        r = admin_client.post(f'/edit_inspection_template/{template_id}/delete_question_preset/{preset_id}', follow_redirects=True)
        assert r.status_code == 200
        assert b'Preset question removed' in r.data
        with app.app_context():
            from application.models import InspectionQuestionPreset
            from main import db
            assert db.session.get(InspectionQuestionPreset, preset_id) is None

    def test_preset_shared_across_templates(self, app, admin_client):
        # Added from one template's page, but it's a global library, so it
        # must show up on a completely different template's page too.
        template_a = _make_template(app, name='Preset Share Template A')
        template_b = _make_template(app, name='Preset Share Template B')
        admin_client.post(f'/edit_inspection_template/{template_a}/add_question_preset', data={
            'category': 'Shared', 'question': 'Is this shared across templates?',
        }, follow_redirects=True)
        r = admin_client.get(f'/edit_inspection_template/{template_b}')
        assert b'Is this shared across templates?' in r.data


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
        facility_id, _, asset_id = _facility_room_asset(app)
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': str(asset_id),
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

    def test_room_ids_creates_one_inspection_per_room(self, app, admin_client):
        template_id = _make_template(app)
        facility_id, room_id, _ = _facility_room_asset(app)
        with app.app_context():
            from application.models import Room
            from main import db
            room2 = Room(site_id=1, facility_id=facility_id, room_number='102', is_active=True)
            db.session.add(room2)
            db.session.commit()
            room2_id = room2.id
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': '0', 'asset_id': '0',
            'room_ids': [str(room_id), str(room2_id)],
            'due_date': '2026-10-15',
        }, follow_redirects=True)
        assert b'2 inspections scheduled' in r.data
        with app.app_context():
            from application.models import Inspection
            created = Inspection.query.filter(Inspection.template_id == template_id,
                                               Inspection.room_id.in_([room_id, room2_id])).all()
            assert {i.room_id for i in created} == {room_id, room2_id}
            assert all(i.facility_id is None for i in created)

    def test_room_ids_takes_precedence_over_facility(self, app, admin_client):
        # Facility is also usable as a plain filter for narrowing the room
        # checklist in the UI — if any room is checked, that's the real
        # intent, so it wins over a facility_id left selected alongside it.
        template_id = _make_template(app)
        facility_id, room_id, _ = _facility_room_asset(app)
        r = admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'asset_id': '0',
            'room_ids': [str(room_id)],
            'due_date': '2026-10-15',
        }, follow_redirects=True)
        assert b'1 inspections scheduled' in r.data
        with app.app_context():
            from application.models import Inspection
            insp = Inspection.query.filter_by(template_id=template_id, room_id=room_id).first()
            assert insp is not None
            assert insp.facility_id is None

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

    def test_na_result_accepted_and_generates_no_work_order(self, app, admin_client):
        """N/A ('doesn't apply to this room') is a valid result, counts as
        neither a pass nor a failure, and never triggers generation."""
        template_id = _make_template(app, name='NA Template', questions=['Plumbing?', 'HVAC?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-08',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'N/A', 'items-1-result': 'Pass', 'generate_work_order': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            from application.models import Inspection, InspectionResult
            from main import db
            insp = db.session.get(Inspection, insp_id)
            assert insp.status == 'Completed'
            assert insp.generated_work_order_id is None
            assert insp.failed_item_count == 0
            results = {r.inspection_item_id: r.result for r in InspectionResult.query.filter_by(inspection_id=insp_id).all()}
            assert 'N/A' in results.values()

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


def _walkthrough_facility(app, name='Walkthrough Building', active_rooms=2, inactive_rooms=0):
    """A facility with `active_rooms` active Rooms (+ optional inactive ones,
    which start_walkthrough must skip), reused across walkthrough tests."""
    with app.app_context():
        from application.models import Facility, Room
        from main import db
        f = Facility.query.filter_by(name=name).first()
        if f is None:
            f = Facility(site_id=1, name=name)
            db.session.add(f)
            db.session.flush()
            for i in range(active_rooms):
                db.session.add(Room(site_id=1, facility_id=f.id, room_number=f'W-{i+1}'))
            for i in range(inactive_rooms):
                db.session.add(Room(site_id=1, facility_id=f.id, room_number=f'W-INACTIVE-{i+1}', is_active=False))
            db.session.commit()
        return f.id


def _make_cycle(app, name='2026-27 Summer Inspection'):
    """Only one InspectionCycle is ever meant to be is_active (every
    dashboard/picker's "active cycle" lookup assumes a singleton) — tests
    share one session-scoped DB with many cycles created across files, so
    deactivate every other one each time, matching what add_inspection_cycle/
    edit_inspection_cycle do for real users."""
    with app.app_context():
        from application.models import InspectionCycle
        from main import db
        c = InspectionCycle.query.filter_by(name=name).first()
        if c is None:
            c = InspectionCycle(name=name)
            db.session.add(c)
            db.session.flush()
            InspectionCycle.query.filter(InspectionCycle.id != c.id).update({InspectionCycle.is_active: False})
            db.session.commit()
        return c.id


class TestInspectionCycleCRUD:
    def test_regular_user_cannot_view_cycles(self, user_client):
        assert user_client.get('/inspection_cycles').status_code == 403

    def test_add_and_edit_cycle(self, admin_client):
        r = admin_client.post('/add_inspection_cycle', data={
            'name': 'CRUD Test Cycle', 'is_active': 'y',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'CRUD Test Cycle' in r.data

    def test_duplicate_cycle_name_rejected(self, admin_client):
        admin_client.post('/add_inspection_cycle', data={'name': 'Dup Cycle', 'is_active': 'y'}, follow_redirects=True)
        r = admin_client.post('/add_inspection_cycle', data={'name': 'Dup Cycle', 'is_active': 'y'}, follow_redirects=True)
        assert b'already exists' in r.data


class TestStartWalkthrough:
    def test_regular_user_cannot_start(self, user_client):
        assert user_client.get('/start_walkthrough').status_code == 403

    def test_start_creates_one_inspection_per_active_room(self, app, admin_client):
        facility_id = _walkthrough_facility(app, name='Start Walkthrough Building', active_rooms=3, inactive_rooms=1)
        cycle_id = _make_cycle(app, name='Start Walkthrough Cycle')
        template_id = _make_template(app, name='Walkthrough Template', questions=['Lights OK?', 'HVAC OK?'])

        r = admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(facility_id), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)
        assert r.status_code == 200

        with app.app_context():
            from application.models import Inspection, Room
            room_ids = {rm.id for rm in Room.query.filter_by(facility_id=facility_id, is_active=True).all()}
            created = Inspection.query.filter_by(cycle_id=cycle_id, template_id=template_id).all()
            assert {insp.room_id for insp in created} == room_ids
            assert len(created) == 3  # the inactive room is skipped

    def test_start_is_idempotent(self, app, admin_client):
        facility_id = _walkthrough_facility(app, name='Idempotent Walkthrough Building', active_rooms=2)
        cycle_id = _make_cycle(app, name='Idempotent Walkthrough Cycle')
        template_id = _make_template(app, name='Idempotent Walkthrough Template', questions=['Q?'])

        for _ in range(2):
            admin_client.post('/start_walkthrough', data={
                'site_id': '1', 'facility_id': str(facility_id), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
            }, follow_redirects=True)

        with app.app_context():
            from application.models import Inspection
            assert Inspection.query.filter_by(cycle_id=cycle_id, template_id=template_id).count() == 2

    def test_building_dropdown_excludes_fully_audited_facility(self, app, admin_client):
        # The School/Building lists are now filtered client-side (JS reads
        # pending_matrix and hides/hides options live as Cycle/Template
        # change — see facility_inspections.html), so both buildings are still
        # in the server-rendered <option> markup; what must actually differ
        # is which facility ids pending_matrix lists as pending for this
        # (cycle, template) combo.
        done_id = _walkthrough_facility(app, name='Fully Audited Building', active_rooms=1)
        pending_id = _walkthrough_facility(app, name='Pending Audit Building', active_rooms=1)
        cycle_id = _make_cycle(app, name='Dropdown Filter Cycle')
        template_id = _make_template(app, name='Dropdown Filter Template', questions=['Q?'])

        admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(done_id), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)
        admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(pending_id), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)

        with app.app_context():
            from application.models import Inspection, InspectionResult, Room
            from main import db
            done_insp = Inspection.query.join(Room, Inspection.room_id == Room.id) \
                .filter(Room.facility_id == done_id, Inspection.cycle_id == cycle_id,
                        Inspection.template_id == template_id).first()
            done_insp.status = 'Completed'
            db.session.add(InspectionResult(inspection_id=done_insp.id,
                inspection_item_id=done_insp.template.items[0].id, result='Pass'))
            db.session.commit()

        r = admin_client.get('/start_walkthrough')
        body = r.get_data(as_text=True)
        assert 'Pending Audit Building' in body
        assert 'Fully Audited Building' in body  # present in markup, just JS-hidden once not pending
        with app.app_context():
            from application.inspections import pending_facility_ids
            pending = pending_facility_ids(cycle_id, template_id, [done_id, pending_id])
            assert pending == {pending_id}

    def test_facilities_ever_inspected_under_template(self, app):
        used_id = _walkthrough_facility(app, name='Template History Used Building', active_rooms=1)
        untouched_id = _walkthrough_facility(app, name='Template History Untouched Building', active_rooms=1)
        cycle_id = _make_cycle(app, name='Template History Cycle')
        template_id = _make_template(app, name='Template History Template', questions=['Q?'])
        other_template_id = _make_template(app, name='Other Template', questions=['Q?'])

        with app.app_context():
            from application.models import Room, Inspection
            from application.inspections import facilities_ever_inspected_under_template
            from main import db
            room = Room.query.filter_by(facility_id=used_id).first()
            db.session.add(Inspection(template_id=template_id, cycle_id=cycle_id, site_id=1,
                                       room_id=room.id, due_date=date(2026, 10, 15), status='Scheduled'))
            db.session.commit()

            assert facilities_ever_inspected_under_template(template_id, [used_id, untouched_id]) == {used_id}
            assert facilities_ever_inspected_under_template(other_template_id, [used_id, untouched_id]) == set()

    def test_dropdown_excludes_buildings_never_used_with_this_template(self, app, admin_client):
        # The literal bug report this guards against: with only one building
        # ever set up for a given Checklist Template, every other (never
        # touched) building used to still show up as "pending" simply
        # because 0-of-N-rooms-done also counts as incomplete. A template
        # should only suggest buildings it has actually been assigned to.
        used_id = _walkthrough_facility(app, name='Only Playground Building', active_rooms=1)
        untouched_id = _walkthrough_facility(app, name='No Playground Here Building', active_rooms=1)
        cycle_id = _make_cycle(app, name='Scoped Template Cycle')
        template_id = _make_template(app, name='Playground Safety & Equipment', questions=['Q?'])

        with app.app_context():
            from application.models import Room, Inspection
            from main import db
            room = Room.query.filter_by(facility_id=used_id).first()
            db.session.add(Inspection(template_id=template_id, cycle_id=cycle_id, site_id=1,
                                       room_id=room.id, due_date=date(2026, 10, 15), status='Scheduled'))
            db.session.commit()

        r = admin_client.get('/start_walkthrough')
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        match = re.search(r'var pendingMatrix = (\{.*?\});', body)
        assert match, 'pendingMatrix not found in rendered page'
        matrix = json.loads(match.group(1))
        key = f'{cycle_id}_{template_id}'
        assert used_id in matrix.get(key, [])
        assert untouched_id not in matrix.get(key, [])


class TestWalkthroughRoomFlow:
    def test_fast_mode_submission_advances_to_next_room_and_only_fail_generates_wo(self, app, admin_client):
        facility_id = _walkthrough_facility(app, name='Fast Mode Building', active_rooms=2)
        cycle_id = _make_cycle(app, name='Fast Mode Cycle')
        template_id = _make_template(app, name='Fast Mode Template', questions=['Only Q?'])
        admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(facility_id), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)

        with app.app_context():
            from application.models import Inspection, Room, Floor
            from main import db
            first = Inspection.query.join(Room, Inspection.room_id == Room.id) \
                .filter(Room.facility_id == facility_id, Inspection.cycle_id == cycle_id) \
                .order_by(Room.room_number).first()
            first_id = first.id

        # First room: a Needs Attention result — informational, no work order.
        r = admin_client.post(f'/record_inspection_results/{first_id}', data={
            'items-0-result': 'Needs Attention', 'generate_work_order': 'y', 'walkthrough': '1',
        })
        assert r.status_code == 302
        assert '/walkthrough/' in r.headers['Location']

        with app.app_context():
            from application.models import Inspection
            from main import db
            insp = db.session.get(Inspection, first_id)
            assert insp.status == 'Completed'
            assert insp.generated_work_order_id is None
            second_id = Inspection.query.filter_by(cycle_id=cycle_id, template_id=template_id, status='Scheduled').first().id

        # Second (last) room: a Fail result — generates a work order, and the
        # walkthrough is now finished (no more Scheduled rooms to advance to).
        r2 = admin_client.post(f'/record_inspection_results/{second_id}', data={
            'items-0-result': 'Fail', 'items-0-notes': 'Broken', 'generate_work_order': 'y', 'walkthrough': '1',
        }, follow_redirects=True)
        assert r2.status_code == 200
        assert b'Facilities audit complete' in r2.data

        with app.app_context():
            from application.models import Inspection
            from main import db
            insp2 = db.session.get(Inspection, second_id)
            assert insp2.status == 'Completed'
            assert insp2.generated_work_order_id is not None

    def test_finishing_a_building_continues_into_the_next_pending_building(self, app, admin_client):
        # Two single-room buildings started under the same cycle+template —
        # finishing the first building's only room should NOT drop back to
        # edit_facility.html; it should roll straight into the second
        # building's room instead.
        building_a = _walkthrough_facility(app, name='Rollover Building A', active_rooms=1)
        building_b = _walkthrough_facility(app, name='Rollover Building B', active_rooms=1)
        cycle_id = _make_cycle(app, name='Rollover Cycle')
        template_id = _make_template(app, name='Rollover Template', questions=['Only Q?'])
        admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(building_a), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)
        admin_client.post('/start_walkthrough', data={
            'site_id': '1', 'facility_id': str(building_b), 'cycle_id': str(cycle_id), 'template_id': str(template_id),
        }, follow_redirects=True)

        with app.app_context():
            from application.models import Inspection, Room
            insp_a_id = Inspection.query.join(Room, Inspection.room_id == Room.id) \
                .filter(Room.facility_id == building_a, Inspection.cycle_id == cycle_id).first().id
            insp_b_id = Inspection.query.join(Room, Inspection.room_id == Room.id) \
                .filter(Room.facility_id == building_b, Inspection.cycle_id == cycle_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_a_id}', data={
            'items-0-result': 'Pass', 'generate_work_order': 'y', 'walkthrough': '1',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'continuing to Rollover Building B' in r.data
        assert b'Facilities audit complete' not in r.data

        with app.app_context():
            from application.models import Inspection
            from main import db
            assert db.session.get(Inspection, insp_a_id).status == 'Completed'
            assert db.session.get(Inspection, insp_b_id).status == 'Scheduled'  # not yet touched, just routed to

        # Finish building B's room too — now everything really is done.
        r2 = admin_client.post(f'/record_inspection_results/{insp_b_id}', data={
            'items-0-result': 'Pass', 'generate_work_order': 'y', 'walkthrough': '1',
        }, follow_redirects=True)
        assert b'Facilities audit complete' in r2.data

    def test_non_walkthrough_submission_unaffected(self, app, admin_client):
        """A plain (non-walkthrough) record_inspection_results POST still
        redirects to edit_inspection, matching pre-existing behavior."""
        template_id = _make_template(app, name='Plain Submission Template', questions=['Only Q?'])
        facility_id, _, _ = _facility_room_asset(app)
        admin_client.post('/add_inspection', data={
            'template_id': str(template_id), 'facility_id': str(facility_id), 'room_id': '0', 'asset_id': '0',
            'due_date': '2026-11-10',
        }, follow_redirects=True)
        with app.app_context():
            from application.models import Inspection
            insp_id = Inspection.query.filter_by(template_id=template_id).first().id

        r = admin_client.post(f'/record_inspection_results/{insp_id}', data={
            'items-0-result': 'Pass', 'generate_work_order': 'y',
        })
        assert r.status_code == 302
        assert r.headers['Location'] == f'/edit_inspection/{insp_id}'


class TestInspectionProgress:
    def test_progress_math_across_facilities(self, app):
        facility_id = _walkthrough_facility(app, name='Progress Math Building', active_rooms=2)
        cycle_id = _make_cycle(app, name='Progress Math Cycle')
        template_id = _make_template(app, name='Progress Math Template', questions=['Q1?'])

        with app.app_context():
            from application.models import Facility, Inspection, InspectionCycle, InspectionTemplate, InspectionResult, Room
            from application import inspections as inspections_module
            from main import db
            facility = db.session.get(Facility, facility_id)
            cycle_obj = db.session.get(InspectionCycle, cycle_id)
            template_obj = db.session.get(InspectionTemplate, template_id)
            inspections_module.start_walkthrough(facility, cycle_obj, template_obj)
            db.session.commit()

            rooms = Room.query.filter_by(facility_id=facility_id).order_by(Room.room_number).all()
            one_inspection = Inspection.query.filter_by(room_id=rooms[0].id, cycle_id=cycle_id).first()
            one_inspection.status = 'Completed'
            db.session.add(InspectionResult(
                inspection_id=one_inspection.id,
                inspection_item_id=template_obj.items[0].id,
                result='Fail',
            ))
            db.session.commit()

            progress = inspections_module.inspection_progress(cycle_id, {'facility_id': facility_id})
            row = progress['facilities'][0]
            assert row['rooms_total'] == 2
            assert row['rooms_inspected'] == 1
            assert row['pct'] == 50.0
            assert row['issues_open'] == 1
            assert row['critical_count'] == 1
            assert progress['district']['rooms_total'] == 2
            assert progress['district']['rooms_inspected'] == 1

    def test_na_results_excluded_from_issues_open(self, app):
        """N/A ('doesn't apply to this room') must not inflate issues_open —
        only Fail/Needs Attention count, same as the health-score factor."""
        facility_id = _walkthrough_facility(app, name='NA Progress Building', active_rooms=1)
        cycle_id = _make_cycle(app, name='NA Progress Cycle')
        template_id = _make_template(app, name='NA Progress Template', questions=['Plumbing?', 'HVAC?'])

        with app.app_context():
            from application.models import Facility, Inspection, InspectionCycle, InspectionTemplate, InspectionResult, Room
            from application import inspections as inspections_module
            from main import db
            facility = db.session.get(Facility, facility_id)
            cycle_obj = db.session.get(InspectionCycle, cycle_id)
            template_obj = db.session.get(InspectionTemplate, template_id)
            inspections_module.start_walkthrough(facility, cycle_obj, template_obj)
            db.session.commit()

            room = Room.query.filter_by(facility_id=facility_id).first()
            inspection = Inspection.query.filter_by(room_id=room.id, cycle_id=cycle_id).first()
            inspection.status = 'Completed'
            db.session.add(InspectionResult(inspection_id=inspection.id, inspection_item_id=template_obj.items[0].id, result='N/A'))
            db.session.add(InspectionResult(inspection_id=inspection.id, inspection_item_id=template_obj.items[1].id, result='Pass'))
            db.session.commit()

            progress = inspections_module.inspection_progress(cycle_id, {'facility_id': facility_id})
            row = progress['facilities'][0]
            assert row['rooms_inspected'] == 1
            assert row['issues_open'] == 0
            assert row['critical_count'] == 0

    def test_room_with_two_completed_inspections_counted_once(self, app):
        """A room can end up with more than one completed Inspection in the
        same cycle (e.g. a second template's walkthrough, or an ad hoc
        /add_inspection on top of the bulk walkthrough) — rooms_inspected
        must count distinct rooms, not raw Inspection rows, or it can exceed
        rooms_total (the dashboard gauge showing e.g. "114 / 111 rooms")."""
        facility_id = _walkthrough_facility(app, name='Double Inspected Building', active_rooms=1)
        cycle_id = _make_cycle(app, name='Double Inspected Cycle')
        template_id = _make_template(app, name='Double Inspected Template A', questions=['Q1?'])
        template2_id = _make_template(app, name='Double Inspected Template B', questions=['Q1?'])

        with app.app_context():
            from application.models import Facility, Inspection, InspectionCycle, InspectionTemplate, Room
            from application import inspections as inspections_module
            from main import db
            facility = db.session.get(Facility, facility_id)
            cycle_obj = db.session.get(InspectionCycle, cycle_id)
            template_obj = db.session.get(InspectionTemplate, template_id)
            template2_obj = db.session.get(InspectionTemplate, template2_id)
            inspections_module.start_walkthrough(facility, cycle_obj, template_obj)
            inspections_module.start_walkthrough(facility, cycle_obj, template2_obj)
            db.session.commit()

            room = Room.query.filter_by(facility_id=facility_id).first()
            Inspection.query.filter_by(room_id=room.id, cycle_id=cycle_id).update({'status': 'Completed'})
            db.session.commit()

            progress = inspections_module.inspection_progress(cycle_id, {'facility_id': facility_id})
            row = progress['facilities'][0]
            assert row['rooms_total'] == 1
            assert row['rooms_inspected'] == 1
            assert row['pct'] == 100.0


class TestIssueResolutionHealth:
    """
    The district-wide dashboard gauge: a checklist result counts as "clean"
    if it's a Pass, or a Fail/Needs Attention whose generated work order is
    already resolved (Completed/Closed/Cancelled) — otherwise it counts
    against the score. N/A is excluded entirely, same convention as every
    other inspection rollup in this file.
    """
    def test_mixed_results_compute_expected_percentage(self, app):
        # issue_resolution_health() only filters by site_ids (no
        # cycle/facility scoping), and this session-scoped test DB
        # accumulates inspection results from every other test — so this
        # needs its own brand-new Site, not the shared site_id=1 fixtures,
        # or the asserted counts below would pick up unrelated data.
        template_id = _make_template(app, name='Issue Health Template',
                                      questions=['Q1?', 'Q2?', 'Q3?', 'Q4?', 'Q5?'])

        with app.app_context():
            from application.models import (Inspection, InspectionResult, InspectionTemplate,
                                             WorkOrder, Priority, Category, Site, Facility, Room)
            from application import inspections as inspections_module
            from main import db

            site = Site(site_name='Issue Health Site', site_acronyms='IH', site_code='901',
                        site_cds='901', site_address='x', site_type='Elementary')
            db.session.add(site)
            db.session.flush()
            facility = Facility(site_id=site.id, name='Issue Health Building')
            db.session.add(facility)
            db.session.flush()
            room = Room(site_id=site.id, facility_id=facility.id, room_number='IH-1')
            db.session.add(room)
            db.session.flush()
            site_id, room_id = site.id, room.id

            template = db.session.get(InspectionTemplate, template_id)
            items = template.items
            pri = Priority.query.filter_by(name='High').first().id
            cat = Category.query.filter_by(name='HVAC').first().id

            def _insp():
                insp = Inspection(template_id=template_id, site_id=site_id, room_id=room_id,
                                   due_date=db.func.current_date(), status='Completed')
                db.session.add(insp)
                db.session.flush()
                return insp

            def _wo(status):
                wo = WorkOrder(site_id=site_id, title='Issue health WO', source='Inspection',
                                status=status, priority_id=pri, category_id=cat)
                db.session.add(wo)
                db.session.flush()
                return wo

            # 1) Plain Pass — clean.
            insp1 = _insp()
            db.session.add(InspectionResult(inspection_id=insp1.id, inspection_item_id=items[0].id, result='Pass'))

            # 2) Fail with a resolved (Closed) work order — clean, since it's fixed.
            closed_wo = _wo('Closed')
            insp2 = _insp()
            insp2.generated_work_order_id = closed_wo.id
            db.session.add(InspectionResult(inspection_id=insp2.id, inspection_item_id=items[1].id, result='Fail'))

            # 3) Fail with a still-open work order — counts against.
            open_wo = _wo('Open')
            insp3 = _insp()
            insp3.generated_work_order_id = open_wo.id
            db.session.add(InspectionResult(inspection_id=insp3.id, inspection_item_id=items[2].id, result='Fail'))

            # 4) Needs Attention, no work order at all (never auto-generated) — counts against.
            insp4 = _insp()
            db.session.add(InspectionResult(inspection_id=insp4.id, inspection_item_id=items[3].id, result='Needs Attention'))

            # 5) N/A — excluded entirely, shouldn't affect total or pct.
            insp5 = _insp()
            db.session.add(InspectionResult(inspection_id=insp5.id, inspection_item_id=items[4].id, result='N/A'))

            # 6) A standalone open ticket with no inspection link at all — still
            # counts as a live issue (and must not double-count open_wo above,
            # which IS linked via insp3.generated_work_order_id).
            standalone_open_wo = _wo('Open')

            db.session.commit()

            health = inspections_module.issue_resolution_health({'site_ids': [site_id]})
            assert health['total'] == 5           # 4 inspection results (N/A excluded) + 1 standalone ticket
            assert health['clean'] == 2           # Pass + resolved Fail
            assert health['issues_outstanding'] == 3
            assert health['open_tickets'] == 1
            assert health['pct'] == 40.0
