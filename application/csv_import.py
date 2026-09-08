"""
Generalized CSV import for Facilities, Rooms, Assets, Vendors, and Users
(Phase 11) — a separate, self-contained tool from the legacy
/bulk-data-upload flow (Users+Sites, FTP-schedulable, kept as-is for
AssistItK12 compatibility). This one covers PROJECT_PLAN.md's five named
entity types with one uniform two-phase import:

1. `validate_rows()` — a pure, no-DB-writes pass over every row. A row
   missing a required field, referencing something that doesn't exist (an
   unknown site/facility/role/asset type), or malformed (bad date/number) is
   marked 'error'. A row whose natural key already exists in the database,
   or repeats an earlier row in the same file, is marked 'duplicate'.
   Neither is fatal to the rest of the file — both are simply excluded from
   the commit and reported.
2. `commit_rows()` — every row that passed validation is added to ONE
   session and committed together. If that commit raises (a genuine,
   unexpected DB-level failure), the whole transaction rolls back and
   nothing is written — so a partially-bad file can produce a partial
   import (the valid rows), but a failure INSIDE the write step can never
   leave the database half-written.

Each entity type's `ENTITY_SPECS` entry carries its required/optional
column headers (for the downloadable template), a `validate` function
(row -> (data, error)), a `key` function (data -> a hashable dedup key), an
`exists` function (data -> bool, checked against the DB), and a `build`
function (data -> a new, unadded model instance).
"""
from datetime import date


def _clean(row, name):
    return (row.get(name) or '').strip()


def _parse_int(value, field):
    value = (value or '').strip()
    if not value:
        return None, None
    try:
        return int(value), None
    except ValueError:
        return None, f'"{field}" must be a whole number, got "{value}".'


def _parse_decimal(value, field):
    value = (value or '').strip()
    if not value:
        return None, None
    try:
        return float(value), None
    except ValueError:
        return None, f'"{field}" must be a number, got "{value}".'


def _parse_date(value, field):
    value = (value or '').strip()
    if not value:
        return None, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, f'"{field}" must be YYYY-MM-DD, got "{value}".'


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------

def _validate_facility(row):
    from application.models import Site
    site_name = _clean(row, 'site_name')
    site = Site.query.filter_by(site_name=site_name).first()
    if not site:
        return None, f'Site "{site_name}" not found.'
    year_built, err = _parse_int(row.get('year_built'), 'year_built')
    if err:
        return None, err
    sqft, err = _parse_int(row.get('square_footage'), 'square_footage')
    if err:
        return None, err
    return {
        'site_id': site.id, 'site_name': site.site_name, 'name': _clean(row, 'name'),
        'facility_type': _clean(row, 'facility_type') or None, 'building_code': _clean(row, 'building_code') or None,
        'address': _clean(row, 'address') or None, 'year_built': year_built, 'square_footage': sqft,
        'notes': _clean(row, 'notes') or None,
    }, None


def _facility_exists(data):
    from application.models import Facility
    return Facility.query.filter_by(site_id=data['site_id'], name=data['name']).first() is not None


def _build_facility(data):
    from application.models import Facility
    return Facility(site_id=data['site_id'], name=data['name'], facility_type=data['facility_type'],
                    building_code=data['building_code'], address=data['address'],
                    year_built=data['year_built'], square_footage=data['square_footage'], notes=data['notes'])


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------

def _validate_room(row):
    from application.models import Site, Facility, Floor
    site_name = _clean(row, 'site_name')
    site = Site.query.filter_by(site_name=site_name).first()
    if not site:
        return None, f'Site "{site_name}" not found.'
    facility_name = _clean(row, 'facility_name')
    facility = Facility.query.filter_by(site_id=site.id, name=facility_name, is_active=True).first()
    if not facility:
        return None, f'Facility "{facility_name}" not found at site "{site_name}".'
    floor_id = None
    floor_name = _clean(row, 'floor_name')
    if floor_name:
        floor = Floor.query.filter_by(facility_id=facility.id, name=floor_name).first()
        if not floor:
            return None, f'Floor "{floor_name}" not found on facility "{facility_name}".'
        floor_id = floor.id
    capacity, err = _parse_int(row.get('capacity'), 'capacity')
    if err:
        return None, err
    sqft, err = _parse_int(row.get('square_footage'), 'square_footage')
    if err:
        return None, err
    return {
        'site_id': site.id, 'facility_id': facility.id, 'facility_name': facility.name, 'floor_id': floor_id,
        'room_number': _clean(row, 'room_number'), 'room_name': _clean(row, 'room_name') or None,
        'room_type': _clean(row, 'room_type') or None, 'capacity': capacity, 'square_footage': sqft,
        'notes': _clean(row, 'notes') or None,
    }, None


def _room_exists(data):
    from application.models import Room
    return Room.query.filter_by(facility_id=data['facility_id'], room_number=data['room_number']).first() is not None


def _build_room(data):
    from application.models import Room
    return Room(site_id=data['site_id'], facility_id=data['facility_id'], floor_id=data['floor_id'],
               room_number=data['room_number'], room_name=data['room_name'], room_type=data['room_type'],
               capacity=data['capacity'], square_footage=data['square_footage'], notes=data['notes'])


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def _validate_asset(row):
    from application.models import Site, Facility, Room, AssetType
    site_name = _clean(row, 'site_name')
    site = Site.query.filter_by(site_name=site_name).first()
    if not site:
        return None, f'Site "{site_name}" not found.'
    facility_name = _clean(row, 'facility_name')
    facility = Facility.query.filter_by(site_id=site.id, name=facility_name, is_active=True).first()
    if not facility:
        return None, f'Facility "{facility_name}" not found at site "{site_name}".'
    type_name = _clean(row, 'asset_type_name')
    asset_type = AssetType.query.filter_by(name=type_name).first()
    if not asset_type:
        return None, f'Asset type "{type_name}" not found.'
    room_id = None
    room_number = _clean(row, 'room_number')
    if room_number:
        room = Room.query.filter_by(facility_id=facility.id, room_number=room_number).first()
        if not room:
            return None, f'Room "{room_number}" not found on facility "{facility_name}".'
        room_id = room.id
    for field in ('install_date', 'purchase_date', 'warranty_expiration'):
        _, err = _parse_date(row.get(field), field)
        if err:
            return None, err
    purchase_cost, err = _parse_decimal(row.get('purchase_cost'), 'purchase_cost')
    if err:
        return None, err
    expected_life_years, err = _parse_int(row.get('expected_life_years'), 'expected_life_years')
    if err:
        return None, err
    return {
        'site_id': site.id, 'facility_id': facility.id, 'room_id': room_id, 'asset_type_id': asset_type.id,
        'asset_tag': _clean(row, 'asset_tag'), 'name': _clean(row, 'name'),
        'manufacturer': _clean(row, 'manufacturer') or None, 'model_number': _clean(row, 'model_number') or None,
        'serial_number': _clean(row, 'serial_number') or None,
        'install_date': _parse_date(row.get('install_date'), 'install_date')[0],
        'purchase_date': _parse_date(row.get('purchase_date'), 'purchase_date')[0],
        'purchase_cost': purchase_cost,
        'warranty_expiration': _parse_date(row.get('warranty_expiration'), 'warranty_expiration')[0],
        'expected_life_years': expected_life_years, 'notes': _clean(row, 'notes') or None,
    }, None


def _asset_exists(data):
    from application.models import Asset
    return Asset.query.filter_by(asset_tag=data['asset_tag']).first() is not None


def _build_asset(data):
    from application.models import Asset
    return Asset(site_id=data['site_id'], facility_id=data['facility_id'], room_id=data['room_id'],
                asset_type_id=data['asset_type_id'], asset_tag=data['asset_tag'], name=data['name'],
                manufacturer=data['manufacturer'], model_number=data['model_number'], serial_number=data['serial_number'],
                install_date=data['install_date'], purchase_date=data['purchase_date'], purchase_cost=data['purchase_cost'],
                warranty_expiration=data['warranty_expiration'], expected_life_years=data['expected_life_years'],
                notes=data['notes'])


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------

def _validate_vendor(row):
    for field in ('contract_start_date', 'contract_end_date', 'insurance_expiration', 'license_expiration'):
        _, err = _parse_date(row.get(field), field)
        if err:
            return None, err
    email = _clean(row, 'email')
    if email and '@' not in email:
        return None, f'"email" is not a valid address: "{email}".'
    return {
        'name': _clean(row, 'name'), 'contact_name': _clean(row, 'contact_name') or None,
        'phone': _clean(row, 'phone') or None, 'email': email or None, 'address': _clean(row, 'address') or None,
        'contract_start_date': _parse_date(row.get('contract_start_date'), 'contract_start_date')[0],
        'contract_end_date': _parse_date(row.get('contract_end_date'), 'contract_end_date')[0],
        'insurance_expiration': _parse_date(row.get('insurance_expiration'), 'insurance_expiration')[0],
        'license_expiration': _parse_date(row.get('license_expiration'), 'license_expiration')[0],
        'notes': _clean(row, 'notes') or None,
    }, None


def _vendor_exists(data):
    from application.models import Vendor
    return Vendor.query.filter_by(name=data['name']).first() is not None


def _build_vendor(data):
    from application.models import Vendor
    return Vendor(name=data['name'], contact_name=data['contact_name'], phone=data['phone'], email=data['email'],
                 address=data['address'], contract_start_date=data['contract_start_date'],
                 contract_end_date=data['contract_end_date'], insurance_expiration=data['insurance_expiration'],
                 license_expiration=data['license_expiration'], notes=data['notes'])


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _validate_user(row):
    from application.models import Role, Site
    role_name = _clean(row, 'role_name')
    role = Role.query.filter_by(role_name=role_name).first()
    if not role:
        return None, f'Role "{role_name}" not found.'
    site_name = _clean(row, 'site_name')
    site = Site.query.filter_by(site_name=site_name).first()
    if not site:
        return None, f'Site "{site_name}" not found.'
    email = _clean(row, 'email')
    if '@' not in email:
        return None, f'"email" is not a valid address: "{email}".'
    return {
        'first_name': _clean(row, 'first_name'), 'middle_name': _clean(row, 'middle_name') or None,
        'last_name': _clean(row, 'last_name'), 'email': email, 'role_id': role.id, 'site_id': site.id,
        'status': _clean(row, 'status') or 'Active',
    }, None


def _user_exists(data):
    from flask import current_app
    from application.models import User
    from application.utils import hash_email
    key = current_app.config['SECRET_KEY']
    return User.query.filter_by(email_hash=hash_email(data['email'], key)).first() is not None


def _build_user(data):
    import secrets
    from werkzeug.security import generate_password_hash
    from application.models import User
    user = User(first_name=data['first_name'], middle_name=data['middle_name'], last_name=data['last_name'],
               status=data['status'], password=generate_password_hash(secrets.token_urlsafe(16)),
               must_change_password=True, failed_login_attempts=0, role_id=data['role_id'], site_id=data['site_id'])
    user.email = data['email']
    return user


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ENTITY_SPECS = {
    'facilities': {
        'label': 'Facilities', 'required': ('site_name', 'name'),
        'optional': ('facility_type', 'building_code', 'address', 'year_built', 'square_footage', 'notes'),
        'example': {'site_name': 'Jefferson Elementary', 'name': 'Main Building', 'facility_type': 'Academic',
                   'building_code': 'MB', 'address': '', 'year_built': '1975', 'square_footage': '45000', 'notes': ''},
        'validate': _validate_facility, 'key': lambda d: (d['site_id'], d['name'].lower()),
        'exists': _facility_exists, 'build': _build_facility,
    },
    'rooms': {
        'label': 'Rooms', 'required': ('site_name', 'facility_name', 'room_number'),
        'optional': ('room_name', 'room_type', 'capacity', 'square_footage', 'floor_name', 'notes'),
        'example': {'site_name': 'Jefferson Elementary', 'facility_name': 'Main Building', 'room_number': '101',
                   'room_name': 'Room 101', 'room_type': 'Classroom', 'capacity': '28', 'square_footage': '900',
                   'floor_name': '', 'notes': ''},
        'validate': _validate_room, 'key': lambda d: (d['facility_id'], d['room_number'].lower()),
        'exists': _room_exists, 'build': _build_room,
    },
    'assets': {
        'label': 'Assets', 'required': ('site_name', 'facility_name', 'asset_tag', 'name', 'asset_type_name'),
        'optional': ('room_number', 'manufacturer', 'model_number', 'serial_number', 'install_date', 'purchase_date',
                    'purchase_cost', 'warranty_expiration', 'expected_life_years', 'notes'),
        'example': {'site_name': 'Jefferson Elementary', 'facility_name': 'Main Building', 'asset_tag': 'HVAC-101',
                   'name': 'Rooftop Unit 1', 'asset_type_name': 'Rooftop HVAC Unit', 'room_number': '',
                   'manufacturer': 'Carrier', 'model_number': '', 'serial_number': '', 'install_date': '2020-06-01',
                   'purchase_date': '', 'purchase_cost': '', 'warranty_expiration': '2025-06-01',
                   'expected_life_years': '15', 'notes': ''},
        'validate': _validate_asset, 'key': lambda d: d['asset_tag'].lower(),
        'exists': _asset_exists, 'build': _build_asset,
    },
    'vendors': {
        'label': 'Vendors', 'required': ('name',),
        'optional': ('contact_name', 'phone', 'email', 'address', 'contract_start_date', 'contract_end_date',
                    'insurance_expiration', 'license_expiration', 'notes'),
        'example': {'name': 'Acme HVAC Co', 'contact_name': 'Jane Doe', 'phone': '555-0100',
                   'email': 'jane@acmehvac.example', 'address': '', 'contract_start_date': '2026-01-01',
                   'contract_end_date': '2027-01-01', 'insurance_expiration': '2027-01-01',
                   'license_expiration': '2027-01-01', 'notes': ''},
        'validate': _validate_vendor, 'key': lambda d: d['name'].lower(),
        'exists': _vendor_exists, 'build': _build_vendor,
    },
    'users': {
        'label': 'Users', 'required': ('first_name', 'last_name', 'email', 'role_name', 'site_name'),
        'optional': ('middle_name', 'status'),
        'example': {'first_name': 'Jamie', 'last_name': 'Rivera', 'email': 'jamie.rivera@example.org',
                   'role_name': 'Technician', 'site_name': 'Jefferson Elementary', 'middle_name': '', 'status': 'Active'},
        'validate': _validate_user, 'key': lambda d: d['email'].lower(),
        'exists': _user_exists, 'build': _build_user,
    },
}

ENTITY_ORDER = ['facilities', 'rooms', 'assets', 'vendors', 'users']


def validate_rows(entity_type, rows):
    """
    Returns (report, valid_data) — report is a list of {'row' (1-indexed,
    header is row 1), 'status' ('valid'|'duplicate'|'error'), 'message',
    'raw'} dicts in file order; valid_data is the cleaned dict for every
    'valid' row only, ready for `commit_rows()`.
    """
    spec = ENTITY_SPECS[entity_type]
    seen_keys = set()
    report = []
    valid_data = []
    for i, raw in enumerate(rows, start=2):
        missing = [c for c in spec['required'] if not (raw.get(c) or '').strip()]
        if missing:
            report.append({'row': i, 'status': 'error', 'message': f'Missing required field(s): {", ".join(missing)}.', 'raw': raw})
            continue
        data, error = spec['validate'](raw)
        if error:
            report.append({'row': i, 'status': 'error', 'message': error, 'raw': raw})
            continue
        key = spec['key'](data)
        if key in seen_keys:
            report.append({'row': i, 'status': 'duplicate', 'message': 'Duplicate of an earlier row in this file.', 'raw': raw})
            continue
        if spec['exists'](data):
            report.append({'row': i, 'status': 'duplicate', 'message': 'Already exists in the database.', 'raw': raw})
            continue
        seen_keys.add(key)
        report.append({'row': i, 'status': 'valid', 'message': 'OK', 'raw': raw})
        valid_data.append(data)
    return report, valid_data


def commit_rows(entity_type, valid_data):
    """
    Adds every row to ONE session and commits once. On any exception, rolls
    back the whole transaction (nothing written) and returns the error
    instead of a count — the guarantee that an unexpected failure never
    leaves a partially-imported file's rows half-applied.
    """
    from main import db
    spec = ENTITY_SPECS[entity_type]
    if not valid_data:
        return 0, None
    try:
        for data in valid_data:
            db.session.add(spec['build'](data))
        db.session.commit()
        return len(valid_data), None
    except Exception as e:
        db.session.rollback()
        return 0, str(e)


def template_csv(entity_type):
    import csv
    import io
    spec = ENTITY_SPECS[entity_type]
    columns = list(spec['required']) + list(spec['optional'])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerow([spec['example'].get(c, '') for c in columns])
    return buf.getvalue()
