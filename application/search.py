"""
Global search (Phase 11): work orders, facilities, rooms, assets, vendors,
projects, and users — one ILIKE query per entity type against the columns
PROJECT_PLAN.md names (asset tag, serial number, work order number, room,
facility, description), each capped at RESULTS_PER_TYPE so a broad query
can never return an unbounded set. Not a full-text index — out of scope for
this stack (PROJECT_PLAN.md's non-goals keep dependencies light); an ILIKE
scan over a handful of indexed/short columns is the appropriate size for
this project's stated scale.

Users are searchable by name only — email is encrypted at rest
(User.email_enc/email_hash), so a partial ILIKE match against it is not
possible; only an exact hash match is (see application/utils.hash_email).
The Users group is also restricted to Admins, matching every other
user-management view in this project.
"""
MIN_QUERY_LENGTH = 2
RESULTS_PER_TYPE = 10


def _like(query_text):
    return f'%{query_text}%'


def _search_work_orders(like, site_ids):
    from application.models import WorkOrder
    q = WorkOrder.query.filter(
        WorkOrder.wo_number.ilike(like) | WorkOrder.title.ilike(like) | WorkOrder.description.ilike(like))
    if site_ids:
        q = q.filter(WorkOrder.site_id.in_(site_ids))
    rows = q.order_by(WorkOrder.created_at.desc()).limit(RESULTS_PER_TYPE).all()
    return [{'title': f'{wo.wo_number} — {wo.title}', 'subtitle': f'{wo.status} · {wo.location_label}',
            'endpoint': 'routes.edit_work_order', 'params': {'work_order_id': wo.id}} for wo in rows]


def _search_facilities(like, site_ids):
    from application.models import Facility
    q = Facility.query.filter(Facility.is_active.is_(True)).filter(
        Facility.name.ilike(like) | Facility.building_code.ilike(like))
    if site_ids:
        q = q.filter(Facility.site_id.in_(site_ids))
    rows = q.order_by(Facility.name).limit(RESULTS_PER_TYPE).all()
    return [{'title': fac.name, 'subtitle': fac.site.site_name if fac.site else '',
            'endpoint': 'routes.edit_facility', 'params': {'facility_id': fac.id}} for fac in rows]


def _search_rooms(like, site_ids):
    from main import db
    from application.models import Room, Facility
    q = Room.query.join(Facility, Room.facility_id == Facility.id).filter(Room.is_active.is_(True)).filter(
        Room.room_number.ilike(like) | Room.room_name.ilike(like))
    if site_ids:
        q = q.filter(Room.site_id.in_(site_ids))
    rows = q.options(db.joinedload(Room.facility)).order_by(Room.room_number).limit(RESULTS_PER_TYPE).all()
    return [{'title': f'Room {room.room_number}' + (f' — {room.room_name}' if room.room_name else ''),
            'subtitle': room.facility.name if room.facility else '',
            'endpoint': 'routes.edit_room', 'params': {'room_id': room.id}} for room in rows]


def _search_assets(like, site_ids):
    from application.models import Asset
    q = Asset.query.filter(Asset.is_active.is_(True)).filter(
        Asset.asset_tag.ilike(like) | Asset.serial_number.ilike(like) | Asset.name.ilike(like))
    if site_ids:
        q = q.filter(Asset.site_id.in_(site_ids))
    rows = q.order_by(Asset.asset_tag).limit(RESULTS_PER_TYPE).all()
    return [{'title': f'{asset.asset_tag} — {asset.name}',
            'subtitle': asset.facility.name if asset.facility else '',
            'endpoint': 'routes.edit_asset', 'params': {'asset_id': asset.id}} for asset in rows]


def _search_vendors(like):
    from application.models import Vendor
    rows = Vendor.query.filter(Vendor.is_active.is_(True)).filter(
        Vendor.name.ilike(like) | Vendor.contact_name.ilike(like)).order_by(Vendor.name).limit(RESULTS_PER_TYPE).all()
    return [{'title': vendor.name, 'subtitle': vendor.contact_name or '',
            'endpoint': 'routes.edit_vendor', 'params': {'vendor_id': vendor.id}} for vendor in rows]


def _search_projects(like, site_ids):
    from application.models import Project
    q = Project.query.filter(Project.name.ilike(like))
    if site_ids:
        q = q.filter(Project.site_id.in_(site_ids))
    rows = q.order_by(Project.name).limit(RESULTS_PER_TYPE).all()
    return [{'title': project.name, 'subtitle': project.status,
            'endpoint': 'routes.edit_project', 'params': {'project_id': project.id}} for project in rows]


def _search_users(like, site_ids, include_users):
    if not include_users:
        return []
    from application.models import User
    q = User.query.filter(User.first_name.ilike(like) | User.last_name.ilike(like))
    if site_ids:
        q = q.filter(User.site_id.in_(site_ids))
    rows = q.order_by(User.first_name, User.last_name).limit(RESULTS_PER_TYPE).all()
    return [{'title': user.get_full_name(), 'subtitle': user.role.role_name if user.role else '',
            'endpoint': 'routes.edit_user', 'params': {'user_id': user.id}} for user in rows]


def global_search(query_text, site_ids=None, include_users=False):
    """
    Returns [(group_label, [{'title','subtitle','endpoint','params'}...]), ...]
    with empty groups omitted; [] if the query is shorter than
    MIN_QUERY_LENGTH. site_ids=None means unrestricted (Admin/Specialist);
    a list restricts every site-scoped entity type to those sites.
    """
    q = (query_text or '').strip()
    if len(q) < MIN_QUERY_LENGTH:
        return []
    like = _like(q)
    groups = [
        ('Work Orders', _search_work_orders(like, site_ids)),
        ('Facilities', _search_facilities(like, site_ids)),
        ('Rooms', _search_rooms(like, site_ids)),
        ('Assets', _search_assets(like, site_ids)),
        ('Vendors', _search_vendors(like)),
        ('Projects', _search_projects(like, site_ids)),
        ('Users', _search_users(like, site_ids, include_users)),
    ]
    return [(label, rows) for label, rows in groups if rows]
