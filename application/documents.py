"""
Document Library (IncidentIQ-inspired addition): a single searchable view
across every attachment table in the app — FacilityAttachment,
RoomAttachment, AssetAttachment, WorkOrderAttachment, InspectionAttachment,
ProjectDocument. Each already has its own upload/download/delete routes
scoped to its one entity (see routes.py's _save_attachment and the six
download_*_attachment/download_project_document routes); this module adds
nothing new there — it just aggregates what already exists into one list,
the way search.py (Phase 11) aggregates six entity types into one search
result instead of building a seventh polymorphic table.

Not a full-text index: a filename ILIKE scan per table, same size/scale
tradeoff search.py documents, capped at PER_TYPE_LIMIT per table so a single
huge attachment history can't blow out the combined list.
"""
PER_TYPE_LIMIT = 300

TYPE_LABELS = {
    'facility': 'Facility', 'room': 'Room', 'asset': 'Asset',
    'work_order': 'Work Order', 'inspection': 'Inspection', 'project': 'Project',
}
TYPE_ICONS = {
    'facility': 'domain', 'room': 'meeting_room', 'asset': 'precision_manufacturing',
    'work_order': 'build', 'inspection': 'checklist', 'project': 'construction',
}


def _like(query_text):
    return f'%{query_text}%'


def _facility_rows(like, site_ids):
    from main import db
    from application.models import FacilityAttachment, Facility, User
    q = (db.session.query(FacilityAttachment, Facility, User)
         .join(Facility, FacilityAttachment.facility_id == Facility.id)
         .outerjoin(User, FacilityAttachment.user_id == User.id))
    if site_ids:
        q = q.filter(Facility.site_id.in_(site_ids))
    if like:
        q = q.filter(FacilityAttachment.attach_file.ilike(like))
    rows = []
    for att, fac, user in q.order_by(FacilityAttachment.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        rows.append(_row('facility', att, fac.name, fac.site.site_name if fac.site else '', user,
                          'routes.download_facility_attachment', {'attachment_id': att.id},
                          'routes.edit_facility', {'facility_id': fac.id}))
    return rows


def _room_rows(like, site_ids):
    from main import db
    from application.models import RoomAttachment, Room, Facility, User
    q = (db.session.query(RoomAttachment, Room, User)
         .join(Room, RoomAttachment.room_id == Room.id)
         .outerjoin(User, RoomAttachment.user_id == User.id))
    if site_ids:
        q = q.filter(Room.site_id.in_(site_ids))
    if like:
        q = q.filter(RoomAttachment.attach_file.ilike(like))
    rows = []
    for att, room, user in q.order_by(RoomAttachment.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        label = f"Room {room.room_number}" + (room.facility and f" — {room.facility.name}" or '')
        rows.append(_row('room', att, label, room.site.site_name if room.site else '', user,
                          'routes.download_room_attachment', {'attachment_id': att.id},
                          'routes.edit_room', {'room_id': room.id}))
    return rows


def _asset_rows(like, site_ids):
    from main import db
    from application.models import AssetAttachment, Asset, User
    q = (db.session.query(AssetAttachment, Asset, User)
         .join(Asset, AssetAttachment.asset_id == Asset.id)
         .outerjoin(User, AssetAttachment.user_id == User.id))
    if site_ids:
        q = q.filter(Asset.site_id.in_(site_ids))
    if like:
        q = q.filter(AssetAttachment.attach_file.ilike(like))
    rows = []
    for att, asset, user in q.order_by(AssetAttachment.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        rows.append(_row('asset', att, f"{asset.asset_tag} — {asset.name}", asset.site.site_name if asset.site else '', user,
                          'routes.download_asset_attachment', {'attachment_id': att.id},
                          'routes.edit_asset', {'asset_id': asset.id}))
    return rows


def _work_order_rows(like, site_ids):
    from main import db
    from application.models import WorkOrderAttachment, WorkOrder, User
    q = (db.session.query(WorkOrderAttachment, WorkOrder, User)
         .join(WorkOrder, WorkOrderAttachment.work_order_id == WorkOrder.id)
         .outerjoin(User, WorkOrderAttachment.user_id == User.id))
    if site_ids:
        q = q.filter(WorkOrder.site_id.in_(site_ids))
    if like:
        q = q.filter(WorkOrderAttachment.attach_file.ilike(like))
    rows = []
    for att, wo, user in q.order_by(WorkOrderAttachment.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        rows.append(_row('work_order', att, f"{wo.wo_number} — {wo.title}", wo.site.site_name if wo.site else '', user,
                          'routes.download_work_order_attachment', {'attachment_id': att.id},
                          'routes.edit_work_order', {'work_order_id': wo.id}))
    return rows


def _inspection_rows(like, site_ids):
    from main import db
    from application.models import InspectionAttachment, Inspection, User
    q = (db.session.query(InspectionAttachment, Inspection, User)
         .join(Inspection, InspectionAttachment.inspection_id == Inspection.id)
         .outerjoin(User, InspectionAttachment.user_id == User.id))
    if site_ids:
        q = q.filter(Inspection.site_id.in_(site_ids))
    if like:
        q = q.filter(InspectionAttachment.attach_file.ilike(like))
    rows = []
    for att, insp, user in q.order_by(InspectionAttachment.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        rows.append(_row('inspection', att, insp.target_label, insp.site.site_name if insp.site else '', user,
                          'routes.download_inspection_attachment', {'attachment_id': att.id},
                          'routes.edit_inspection', {'inspection_id': insp.id}))
    return rows


def _project_rows(like, site_ids):
    from main import db
    from application.models import ProjectDocument, Project, User
    q = (db.session.query(ProjectDocument, Project, User)
         .join(Project, ProjectDocument.project_id == Project.id)
         .outerjoin(User, ProjectDocument.user_id == User.id))
    if site_ids:
        q = q.filter(Project.site_id.in_(site_ids))
    if like:
        q = q.filter(ProjectDocument.attach_file.ilike(like))
    rows = []
    for att, project, user in q.order_by(ProjectDocument.uploaded_at.desc()).limit(PER_TYPE_LIMIT).all():
        rows.append(_row('project', att, project.name, project.site.site_name if project.site else '', user,
                          'routes.download_project_document', {'document_id': att.id},
                          'routes.edit_project', {'project_id': project.id}))
    return rows


def _row(entity_type, att, entity_label, site_name, user, download_endpoint, download_params,
         entity_endpoint, entity_params):
    return {
        'type': entity_type, 'type_label': TYPE_LABELS[entity_type], 'icon': TYPE_ICONS[entity_type],
        'filename': att.attach_file, 'uploaded_at': att.uploaded_at,
        'uploaded_by': user.get_full_name() if user else None,
        'entity_label': entity_label, 'site_name': site_name,
        'download_endpoint': download_endpoint, 'download_params': download_params,
        'entity_endpoint': entity_endpoint, 'entity_params': entity_params,
    }


_COLLECTORS = {
    'facility': _facility_rows, 'room': _room_rows, 'asset': _asset_rows,
    'work_order': _work_order_rows, 'inspection': _inspection_rows, 'project': _project_rows,
}


def list_documents(site_ids=None, q=None, type_filter=None):
    """
    Combined, newest-first list of every attachment/document in scope.
    site_ids=None means unrestricted (Admin/Specialist); a list restricts
    every entity type to those sites, matching search.py's convention.
    type_filter narrows to one of TYPE_LABELS' keys; falsy means all types.
    """
    like = _like(q.strip()) if q and q.strip() else None
    types = (type_filter,) if type_filter in _COLLECTORS else _COLLECTORS.keys()
    rows = []
    for t in types:
        rows.extend(_COLLECTORS[t](like, site_ids))
    rows.sort(key=lambda r: r['uploaded_at'], reverse=True)
    return rows
