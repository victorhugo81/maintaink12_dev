from main import db  # Import db from main.py where it's initialized
from flask_login import UserMixin
from sqlalchemy import event
from datetime import datetime, timezone


def _utcnow():
    """Return current UTC time as a naive datetime (compatible with legacy DateTime columns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    organization_name = db.Column(db.String(100), nullable=False)
    site_version = db.Column(db.String(100), nullable=False)
    organization_logo = db.Column(db.String(100), nullable=True)
    # Flask-Mail configuration
    mail_server = db.Column(db.String(255), nullable=True)
    mail_port = db.Column(db.Integer, nullable=True)
    mail_use_tls = db.Column(db.Boolean, default=False, nullable=True)
    mail_use_ssl = db.Column(db.Boolean, default=False, nullable=True)
    mail_username = db.Column(db.String(255), nullable=True)
    mail_password = db.Column(db.String(255), nullable=True)
    mail_default_sender = db.Column(db.String(255), nullable=True)
    # FTP configuration (host, username, password stored encrypted)
    ftp_host_enc = db.Column(db.String(512), nullable=True)
    ftp_port = db.Column(db.Integer, default=21, nullable=True)
    ftp_username_enc = db.Column(db.String(512), nullable=True)
    ftp_password_enc = db.Column(db.String(512), nullable=True)
    ftp_path = db.Column(db.String(512), nullable=True)
    ftp_use_tls = db.Column(db.Boolean, default=False, nullable=True)
    # FTP schedule
    ftp_schedule_enabled = db.Column(db.Boolean, default=False, nullable=True)
    ftp_schedule_hour    = db.Column(db.Integer, nullable=True)
    ftp_schedule_minute  = db.Column(db.Integer, default=0, nullable=True)
    ftp_schedule_days    = db.Column(db.String(50), default='*', nullable=True)  # '*' or 'mon,tue,...'
    ftp_last_run_at      = db.Column(db.DateTime, nullable=True)
    ftp_last_run_status  = db.Column(db.String(20), nullable=True)
    ftp_schedule_start_date = db.Column(db.Date, nullable=True)
    ftp_schedule_stop_date  = db.Column(db.Date, nullable=True)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    msg_name = db.Column(db.String(100), unique=True, nullable=False)
    msg_content = db.Column(db.String(255), nullable=False)
    msg_status = db.Column(db.String(10), nullable=False)


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    first_name = db.Column(db.String(50), nullable=False)
    middle_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=False)
    email_enc  = db.Column(db.Text, nullable=False)
    email_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    status = db.Column(db.String(120), nullable=False)

    @property
    def email(self):
        from flask import current_app
        from application.utils import decrypt_mail_password
        return decrypt_mail_password(self.email_enc or '', current_app.config['SECRET_KEY'])

    @email.setter
    def email(self, value):
        from flask import current_app
        from application.utils import encrypt_mail_password, hash_email
        key = current_app.config['SECRET_KEY']
        self.email_enc = encrypt_mail_password(value or '', key)
        self.email_hash = hash_email(value or '', key)
    password = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    rm_num = db.Column(db.String(45), nullable=True)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id', ondelete='CASCADE'), nullable=False)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False)

    def get_full_name(self):
        return f"{self.first_name} {self.middle_name or ''} {self.last_name}".strip()
    
    @property
    def is_admin(self):
        return self.role and self.role.role_name.lower() == "admin"

    @property
    def is_tech_role(self):
        return self.role and self.role.role_name.lower() in ["specialist", "technician"]


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role_name = db.Column(db.String(50), unique=True, nullable=False)
    users = db.relationship('User', backref='role', lazy=True)


class Site(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_name = db.Column(db.String(100), nullable=False, unique=True)
    site_acronyms = db.Column(db.String(36), nullable=False)
    site_cds = db.Column(db.String(100), nullable=False)
    site_code = db.Column(db.String(100), nullable=False)
    site_address = db.Column(db.String(100), nullable=False)
    site_type = db.Column(db.String(100), nullable=False)
    # Optional — not in SITE_REQUIRED (routes.py), so existing sites and the
    # manual Add/Edit Site form both work without them. Populated by the
    # sites.csv bulk importer's sitecity/sitestate/sitezip/prnfirstn/prnlastn/
    # email/phone columns (application/routes.py's _process_sites_rows).
    site_city = db.Column(db.String(100), nullable=True)
    site_state = db.Column(db.String(50), nullable=True)
    site_zip = db.Column(db.String(20), nullable=True)
    principal_first_name = db.Column(db.String(100), nullable=True)
    principal_last_name = db.Column(db.String(100), nullable=True)
    principal_email = db.Column(db.String(255), nullable=True)
    principal_phone = db.Column(db.String(30), nullable=True)
    users = db.relationship('User', backref='site', lazy=True)
    facilities = db.relationship('Facility', backref='site', lazy=True)


class BulkUploadLog(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    total_records = db.Column(db.Integer, default=0)
    users_added = db.Column(db.Integer, default=0)
    users_updated = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='success')
    error_message = db.Column(db.Text, nullable=True)

    uploader = db.relationship('User', foreign_keys=[uploaded_by_id])


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Facility / Floor / Room
#
# Hierarchy: Site -> Facility -> Floor -> Room. Facility and Room carry their
# own site_id (denormalized from Facility) so site-scoped queries never need
# an extra join; Floor doesn't need one since it's always accessed through
# its Facility.
#
# "Delete" on Facility/Room is a soft delete (is_active=False), not a DB row
# delete: PROJECT_PLAN.md requires history (work orders, assets, inspections
# added in later phases) to survive a facility/room being retired.
# ---------------------------------------------------------------------------

class Facility(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    facility_type = db.Column(db.String(100), nullable=True)
    building_code = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    year_built = db.Column(db.Integer, nullable=True)
    square_footage = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
    floors = db.relationship('Floor', backref='facility', cascade='all, delete-orphan',
                              order_by='Floor.sort_order')
    rooms = db.relationship('Room', backref='facility', cascade='all, delete-orphan')
    attachments = db.relationship('FacilityAttachment', backref='facility', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('site_id', 'name', name='uq_facility_site_name'),
    )


class Floor(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    rooms = db.relationship('Room', backref='floor')

    __table_args__ = (
        db.UniqueConstraint('facility_id', 'name', name='uq_floor_facility_name'),
    )


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='CASCADE'), nullable=False, index=True)
    floor_id = db.Column(db.Integer, db.ForeignKey('floor.id', ondelete='SET NULL'), nullable=True, index=True)
    room_number = db.Column(db.String(50), nullable=False)
    room_name = db.Column(db.String(150), nullable=True)
    room_type = db.Column(db.String(100), nullable=True)
    capacity = db.Column(db.Integer, nullable=True)
    square_footage = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    site = db.relationship('Site')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    attachments = db.relationship('RoomAttachment', backref='room', cascade='all, delete-orphan')

    __table_args__ = (
        db.UniqueConstraint('facility_id', 'room_number', name='uq_room_facility_number'),
    )


class FacilityAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='CASCADE'), nullable=False)
    attach_file = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


class RoomAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=False)
    attach_file = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Assets & Asset Condition (Phase 2)
#
# AssetType is global reference data (admin-managed). Asset belongs to a
# Facility and optionally a Room, and carries site_id denormalized from the
# Facility like Room does. Condition is recorded as an append-only history;
# Asset.condition_score / condition_label are a cache of the latest entry,
# refreshed by Asset.apply_condition() — never edited directly.
# ---------------------------------------------------------------------------

# PROJECT_PLAN.md condition scale: (lower bound, label), checked top-down.
CONDITION_SCALE = (
    (90, 'Excellent'),
    (75, 'Good'),
    (50, 'Fair'),
    (25, 'Poor'),
    (0, 'Critical'),
)


def condition_label_for_score(score):
    for floor_score, label in CONDITION_SCALE:
        if score >= floor_score:
            return label
    return 'Critical'


class AssetType(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    category = db.Column(db.String(100), nullable=True)
    expected_life_years = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    assets = db.relationship('Asset', backref='asset_type')


class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='CASCADE'), nullable=False, index=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='SET NULL'), nullable=True, index=True)
    asset_type_id = db.Column(db.Integer, db.ForeignKey('asset_type.id'), nullable=False, index=True)
    asset_tag = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(150), nullable=False)
    manufacturer = db.Column(db.String(100), nullable=True)
    model_number = db.Column(db.String(100), nullable=True)
    serial_number = db.Column(db.String(100), nullable=True, index=True)
    install_date = db.Column(db.Date, nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    purchase_cost = db.Column(db.Numeric(12, 2), nullable=True)
    warranty_expiration = db.Column(db.Date, nullable=True)
    expected_life_years = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    condition_score = db.Column(db.Integer, nullable=True)
    condition_label = db.Column(db.String(20), nullable=True)
    # Phase 8 additions: not derivable from any existing data, so they're
    # admin-set fields, not computed — see application/risk.py. 0-100, higher
    # = more critical. NULL means "not assessed", excluded from the risk
    # score rather than defaulted to 0 (which would silently understate risk).
    safety_impact = db.Column(db.Integer, nullable=True)
    operational_importance = db.Column(db.Integer, nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='SET NULL'), nullable=True, index=True)  # Phase 8
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    site = db.relationship('Site')
    facility = db.relationship('Facility', backref='assets')
    room = db.relationship('Room', backref='assets')
    project = db.relationship('Project', backref='assets')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    condition_history = db.relationship(
        'AssetConditionHistory', backref='asset',
        order_by='desc(AssetConditionHistory.assessed_at), desc(AssetConditionHistory.id)')
    attachments = db.relationship('AssetAttachment', backref='asset', cascade='all, delete-orphan')

    def apply_condition(self, history_entry):
        """Refresh the cached current-condition columns from a newly appended history row."""
        self.condition_score = history_entry.score
        self.condition_label = history_entry.condition


class AssetConditionHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), nullable=False, index=True)
    assessed_at = db.Column(db.Date, nullable=False, index=True)
    condition = db.Column(db.String(20), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    inspector_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reason = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    recommended_action = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    inspector = db.relationship('User', foreign_keys=[inspector_id])
    photos = db.relationship('AssetAttachment', backref='condition_entry')

    __table_args__ = (
        db.CheckConstraint('score >= 0 AND score <= 100', name='ck_asset_condition_score_range'),
    )


class AssetAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), nullable=False)
    # Set when the photo was uploaded as part of a condition assessment.
    condition_history_id = db.Column(db.Integer, db.ForeignKey('asset_condition_history.id', ondelete='SET NULL'), nullable=True)
    attach_file = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Work Orders (Phase 3)
#
# WorkOrder was built as a new model rather than an extension of the original
# AssistItK12 Ticket (see docs/PHASE_0_ARCHITECTURE_ANALYSIS.md, Recommendation
# 2): requester is optional (PM/Inspection/Manual sources), the status
# vocabulary is the 11-state workflow in application/workflow.py, and it links
# to the Facility/Room/Asset hierarchy. Ticket/Title were removed entirely in
# Phase 13 once WorkOrder covered the same ground (docs/PHASE_13_REPORT.md) —
# this history is kept here since it explains WorkOrder's shape. Status
# changes must go through
# workflow.apply_transition(), which writes WorkOrderStatusHistory.
# ---------------------------------------------------------------------------

class Priority(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)  # 1 = most urgent
    color = db.Column(db.String(20), default='secondary', nullable=False)  # bootstrap badge suffix
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    work_orders = db.relationship('WorkOrder', backref='priority')


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    subcategories = db.relationship('Subcategory', backref='category', cascade='all, delete-orphan',
                                    order_by='Subcategory.name')
    work_orders = db.relationship('WorkOrder', backref='category')


class Subcategory(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    work_orders = db.relationship('WorkOrder', backref='subcategory')

    __table_args__ = (
        db.UniqueConstraint('category_id', 'name', name='uq_subcategory_category_name'),
    )


class WorkOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    wo_number = db.Column(db.String(20), unique=True, nullable=True)  # set from id after flush
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='SET NULL'), nullable=True, index=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='SET NULL'), nullable=True, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='SET NULL'), nullable=True, index=True)

    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, index=True)  # Request / Manual / PM / Inspection
    status = db.Column(db.String(30), nullable=False, index=True)
    priority_id = db.Column(db.Integer, db.ForeignKey('priority.id'), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False, index=True)
    subcategory_id = db.Column(db.Integer, db.ForeignKey('subcategory.id'), nullable=True)

    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    assigned_team = db.Column(db.String(100), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=True, index=True)  # Phase 6 addition
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='SET NULL'), nullable=True, index=True)  # Phase 8
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    scheduled_date = db.Column(db.Date, nullable=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)  # Phase 9: dashboard "completed in period" queries
    closed_at = db.Column(db.DateTime, nullable=True)
    resolution = db.Column(db.Text, nullable=True)
    estimated_cost = db.Column(db.Numeric(12, 2), nullable=True)
    actual_cost = db.Column(db.Numeric(12, 2), nullable=True)

    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)

    site = db.relationship('Site')
    facility = db.relationship('Facility')
    room = db.relationship('Room')
    asset = db.relationship('Asset', backref='work_orders')
    requester = db.relationship('User', foreign_keys=[requester_id], backref='requested_work_orders')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id], backref='assigned_work_orders')
    vendor = db.relationship('Vendor', backref='work_orders')
    project = db.relationship('Project', backref='work_orders')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    comments = db.relationship('WorkOrderComment', backref='work_order', cascade='all, delete-orphan',
                               order_by='WorkOrderComment.created_at')
    attachments = db.relationship('WorkOrderAttachment', backref='work_order', cascade='all, delete-orphan')
    status_history = db.relationship('WorkOrderStatusHistory', backref='work_order', cascade='all, delete-orphan',
                                     order_by='desc(WorkOrderStatusHistory.changed_at), desc(WorkOrderStatusHistory.id)')
    labor_entries = db.relationship('WorkOrderLabor', backref='work_order', cascade='all, delete-orphan',
                                    order_by='WorkOrderLabor.created_at')
    materials = db.relationship('WorkOrderMaterial', backref='work_order', cascade='all, delete-orphan',
                                order_by='WorkOrderMaterial.created_at')
    cost_record = db.relationship('CostRecord', backref='work_order', uselist=False, cascade='all, delete-orphan')

    def assign_number(self):
        """Call after flush; gives the human-facing WO-000123 identifier."""
        if self.id and not self.wo_number:
            self.wo_number = f"WO-{self.id:06d}"

    @property
    def location_label(self):
        parts = [self.site.site_name if self.site else None,
                 self.facility.name if self.facility else None,
                 f"Rm {self.room.room_number}" if self.room else None]
        return ' · '.join(p for p in parts if p)


class WorkOrderComment(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    user = db.relationship('User')


class WorkOrderAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False)
    attach_file = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


class WorkOrderStatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False, index=True)
    from_status = db.Column(db.String(30), nullable=True)  # None for the creating entry
    to_status = db.Column(db.String(30), nullable=False)
    changed_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    note = db.Column(db.String(255), nullable=True)

    changed_by = db.relationship('User')


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Preventive Maintenance (Phase 4)
#
# MaintenancePlan is the template (tied to a specific Asset OR to every Asset
# of an AssetType — exactly one, enforced by a CheckConstraint). Because a
# type-based plan covers many concrete assets, MaintenanceSchedule is the
# per-(plan, asset) "next due" tracker that actually gets queried by the
# generator and the dashboard — it resolves a type-based plan down to one row
# per real asset. next_due_date is mutated in place by application/pm.py;
# it is not an append-only history like AssetConditionHistory.
# ---------------------------------------------------------------------------

class MaintenancePlan(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), nullable=True, index=True)
    asset_type_id = db.Column(db.Integer, db.ForeignKey('asset_type.id', ondelete='CASCADE'), nullable=True, index=True)
    frequency = db.Column(db.String(20), nullable=False)
    custom_interval_days = db.Column(db.Integer, nullable=True)  # required when frequency == 'Custom'
    start_date = db.Column(db.Date, nullable=True)  # seeds the first schedule's due date; defaults to "today" if unset
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    priority_id = db.Column(db.Integer, db.ForeignKey('priority.id'), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assigned_team = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    asset = db.relationship('Asset', backref='maintenance_plans')
    asset_type = db.relationship('AssetType', backref='maintenance_plans')
    category = db.relationship('Category')
    priority = db.relationship('Priority')
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    schedules = db.relationship('MaintenanceSchedule', backref='plan', cascade='all, delete-orphan')

    __table_args__ = (
        db.CheckConstraint(
            '(asset_id IS NOT NULL AND asset_type_id IS NULL) OR (asset_id IS NULL AND asset_type_id IS NOT NULL)',
            name='ck_maintenance_plan_single_target'
        ),
    )

    @property
    def target_label(self):
        return self.asset.asset_tag if self.asset_id else f"All {self.asset_type.name}"


class MaintenanceSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    maintenance_plan_id = db.Column(db.Integer, db.ForeignKey('maintenance_plan.id', ondelete='CASCADE'), nullable=False, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), nullable=False, index=True)
    next_due_date = db.Column(db.Date, nullable=False, index=True)
    last_generated_at = db.Column(db.DateTime, nullable=True)
    last_work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    asset = db.relationship('Asset')
    last_work_order = db.relationship('WorkOrder')

    __table_args__ = (
        db.UniqueConstraint('maintenance_plan_id', 'asset_id', name='uq_maintenance_schedule_plan_asset'),
    )


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Inspections (Phase 5)
#
# InspectionTemplate is an admin-defined, reusable checklist (its
# InspectionItems). An Inspection is one run of a template against exactly
# one of a Facility, Room, or Asset (CheckConstraint), and is dual-purpose
# like Phase 4's MaintenanceSchedule: it starts life as a due-dated
# "Scheduled" row with no results, and becomes "Completed" IN PLACE the
# moment record_inspection_results() (routes.py) writes its InspectionResult
# rows — there is no separate schedule table. Due/overdue tracking (Phase 5
# item 4) is computed purely from due_date on still-Scheduled rows, exactly
# like application/pm.py's dashboard_buckets() — never a stored flag.
#
# Failed-item work-order generation stores the link on Inspection
# (generated_work_order_id), not on WorkOrder — so, unlike the maintenance-
# plan/inspection FK columns Phase 3 deferred because these tables didn't
# exist yet, no migration touching the work_order table was needed here.
# ---------------------------------------------------------------------------

INSPECTION_RESULTS = ('Pass', 'Fail', 'Needs Attention')


class InspectionTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    # Used to classify any WorkOrder generated from a failed item on this template.
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    priority_id = db.Column(db.Integer, db.ForeignKey('priority.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    category = db.relationship('Category')
    priority = db.relationship('Priority')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    items = db.relationship('InspectionItem', backref='template', cascade='all, delete-orphan',
                            order_by='InspectionItem.sort_order')


class InspectionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    template_id = db.Column(db.Integer, db.ForeignKey('inspection_template.id', ondelete='CASCADE'), nullable=False, index=True)
    question = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)


class Inspection(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    template_id = db.Column(db.Integer, db.ForeignKey('inspection_template.id'), nullable=False, index=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facility.id', ondelete='CASCADE'), nullable=True, index=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=True, index=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), nullable=True, index=True)
    due_date = db.Column(db.Date, nullable=False, index=True)
    inspector_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Scheduled')  # 'Scheduled' | 'Completed'
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    generated_work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    template = db.relationship('InspectionTemplate')
    site = db.relationship('Site')
    facility = db.relationship('Facility')
    room = db.relationship('Room')
    asset = db.relationship('Asset')
    inspector = db.relationship('User', foreign_keys=[inspector_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    generated_work_order = db.relationship('WorkOrder')
    results = db.relationship('InspectionResult', backref='inspection', cascade='all, delete-orphan')

    __table_args__ = (
        db.CheckConstraint(
            '(CASE WHEN facility_id IS NOT NULL THEN 1 ELSE 0 END) + '
            '(CASE WHEN room_id IS NOT NULL THEN 1 ELSE 0 END) + '
            '(CASE WHEN asset_id IS NOT NULL THEN 1 ELSE 0 END) = 1',
            name='ck_inspection_single_target'
        ),
    )

    @property
    def target(self):
        return self.facility or self.room or self.asset

    @property
    def target_label(self):
        if self.facility_id:
            return f"Facility: {self.facility.name}"
        if self.room_id:
            return f"Room: {self.room.facility.name} - {self.room.room_number}"
        return f"Asset: {self.asset.asset_tag}"

    @property
    def failed_item_count(self):
        return sum(1 for r in self.results if r.result == 'Fail')


class InspectionResult(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('inspection.id', ondelete='CASCADE'), nullable=False, index=True)
    inspection_item_id = db.Column(db.Integer, db.ForeignKey('inspection_item.id'), nullable=False)
    result = db.Column(db.String(20), nullable=False)  # one of INSPECTION_RESULTS
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    item = db.relationship('InspectionItem')

    __table_args__ = (
        db.UniqueConstraint('inspection_id', 'inspection_item_id', name='uq_inspection_result_item'),
        db.CheckConstraint("result IN ('Pass', 'Fail', 'Needs Attention')", name='ck_inspection_result_value'),
    )


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Vendors (Phase 6)
#
# Vendor is standalone reference data; WorkOrder.vendor_id is the one link
# between them, added here as a nullable additive column — the first
# migration in this project to ALTER an existing M&O table rather than only
# create new ones (still purely additive: ADD COLUMN, nullable, no data
# migration needed since every existing WorkOrder simply has no vendor yet).
# ---------------------------------------------------------------------------

class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    contact_name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(30), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    contract_start_date = db.Column(db.Date, nullable=True)
    contract_end_date = db.Column(db.Date, nullable=True)
    insurance_expiration = db.Column(db.Date, nullable=True)
    license_expiration = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Labor & Cost Tracking (Phase 7)
#
# WorkOrderMaterial wasn't built in Phase 3 (deferred, like Vendor/Inspection
# FKs, until something needed it) — it's built here because CostRecord's
# material cost has nowhere else to come from. CostRecord is a per-WorkOrder
# cost-summary CACHE: labor_cost/material_cost/total_cost are recomputed by
# application/costs.py.refresh_cost_record() whenever a labor or material
# entry changes; vendor_cost/other_cost are entered directly (nothing to sum
# them from). "Department" in PROJECT_PLAN.md's rollup dimension list has no
# backing model anywhere in this schema — mapped to WorkOrder.assigned_team,
# the closest existing concept (see docs/PHASE_7_REPORT.md).
# ---------------------------------------------------------------------------

LABOR_TYPES = ('Regular', 'Overtime', 'Emergency', 'Contractor')


class WorkOrderMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), nullable=False, default=1)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    vendor = db.relationship('Vendor')
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def total_cost(self):
        return (self.quantity or 0) * (self.unit_cost or 0)


class WorkOrderLabor(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False, index=True)
    technician_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    labor_hours = db.Column(db.Numeric(6, 2), nullable=False)
    labor_type = db.Column(db.String(30), nullable=False, default='Regular')
    hourly_rate = db.Column(db.Numeric(8, 2), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    technician = db.relationship('User', foreign_keys=[technician_id])
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def labor_cost(self):
        return (self.labor_hours or 0) * (self.hourly_rate or 0)


class CostRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_order.id', ondelete='CASCADE'), nullable=False, unique=True)
    labor_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    material_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    vendor_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    other_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow, nullable=True)


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Projects & Capital Planning (Phase 8)
#
# A project groups WorkOrders and Assets via simple nullable FKs on those
# tables (WorkOrder.project_id, Asset.project_id — one project has many of
# each, matching how a capital project actually spans many work orders and
# assets at a time) and Vendors via a plain many-to-many table (a project
# typically involves several vendors, and a vendor works many projects).
# Documents mirror the FacilityAttachment/RoomAttachment/AssetAttachment
# pattern from earlier phases. ProjectCost is separate from Phase 7's
# CostRecord: CostRecord is a per-WorkOrder cache; ProjectCost is direct
# project-level spend (design fees, permits, contingency) that isn't tied to
# any single work order. application/projects.py's project_cost_rollup()
# combines both: direct ProjectCost entries + every linked WorkOrder's cost.
# ---------------------------------------------------------------------------

PROJECT_STATUSES = ('Planning', 'Approved', 'In Progress', 'On Hold', 'Completed', 'Cancelled')
PROJECT_TASK_STATUSES = ('Not Started', 'In Progress', 'Completed')
PROJECT_COST_TYPES = ('Design', 'Permit', 'Labor', 'Material', 'Contingency', 'Other')

project_vendor = db.Table(
    'project_vendor',
    db.Column('project_id', db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), primary_key=True),
    db.Column('vendor_id', db.Integer, db.ForeignKey('vendor.id', ondelete='CASCADE'), primary_key=True),
)


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    site_id = db.Column(db.Integer, db.ForeignKey('site.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Planning')
    budget = db.Column(db.Numeric(14, 2), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    target_end_date = db.Column(db.Date, nullable=True)
    actual_end_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    site = db.relationship('Site')
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    vendors = db.relationship('Vendor', secondary=project_vendor, backref='projects')
    tasks = db.relationship('ProjectTask', backref='project', cascade='all, delete-orphan',
                            order_by='ProjectTask.sort_order')
    costs = db.relationship('ProjectCost', backref='project', cascade='all, delete-orphan',
                            order_by='desc(ProjectCost.incurred_date)')
    documents = db.relationship('ProjectDocument', backref='project', cascade='all, delete-orphan')
    # .assets and .work_orders come from the backref side of Asset.project / WorkOrder.project


class ProjectTask(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Not Started')
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    assigned_to = db.relationship('User')


class ProjectCost(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True)
    description = db.Column(db.String(200), nullable=False)
    cost_type = db.Column(db.String(20), nullable=False, default='Other')
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=True)
    incurred_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    vendor = db.relationship('Vendor')
    created_by = db.relationship('User', foreign_keys=[created_by_id])


class ProjectDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id', ondelete='CASCADE'), nullable=False)
    attach_file = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — SLA, Notifications, CSV Import (Phase 11)
#
# SLARule is one-to-one with Priority (unique priority_id) — a priority with
# no row simply has no SLA target, never a fabricated default. Response and
# resolution deadlines are computed from WorkOrder.created_at at read time
# (application/sla.py), not stored on WorkOrder: a rule change should apply
# to a work order's remaining lifetime, not freeze in a stale snapshot.
#
# NotificationPreference is one-to-one with User; a user with no row is
# treated as "everything on" by application/notifications.py, so newly
# created users don't silently miss alerts before ever visiting the
# preferences page. NotificationLog is the de-duplication ledger: one row
# per (event_type, entity_type, entity_id, bucket, user_id) — "bucket" is a
# day or ISO week string (application/notifications.EVENT_CADENCE) so a
# transient condition (due today) reminds once, and a persistent one
# (overdue, expiring) reminds on a cadence instead of every single day.
# ---------------------------------------------------------------------------

class SLARule(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    priority_id = db.Column(db.Integer, db.ForeignKey('priority.id', ondelete='CASCADE'), nullable=False, unique=True)
    response_hours = db.Column(db.Integer, nullable=False)
    resolution_hours = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)

    priority = db.relationship('Priority', backref=db.backref('sla_rule', uselist=False))

    __table_args__ = (
        db.CheckConstraint('response_hours > 0 AND resolution_hours > 0', name='ck_sla_rule_positive_hours'),
    )


NOTIFICATION_EVENTS = ('pm_due', 'pm_overdue', 'inspection_due', 'inspection_failed',
                       'vendor_contract_expiring', 'asset_warranty_expiring', 'sla_warning', 'sla_breach')

NOTIFICATION_EVENT_LABELS = {
    'pm_due': 'Preventive maintenance due today',
    'pm_overdue': 'Preventive maintenance overdue',
    'inspection_due': 'Inspection due today',
    'inspection_failed': 'Inspection failed an item',
    'vendor_contract_expiring': 'Vendor contract expiring/expired',
    'asset_warranty_expiring': 'Asset warranty expiring/expired',
    'sla_warning': 'Work order approaching its SLA deadline',
    'sla_breach': 'Work order missed its SLA deadline',
}


class NotificationPreference(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, unique=True)
    pm_due = db.Column(db.Boolean, default=True, nullable=False)
    pm_overdue = db.Column(db.Boolean, default=True, nullable=False)
    inspection_due = db.Column(db.Boolean, default=True, nullable=False)
    inspection_failed = db.Column(db.Boolean, default=True, nullable=False)
    vendor_contract_expiring = db.Column(db.Boolean, default=True, nullable=False)
    asset_warranty_expiring = db.Column(db.Boolean, default=True, nullable=False)
    sla_warning = db.Column(db.Boolean, default=True, nullable=False)
    sla_breach = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, onupdate=_utcnow, nullable=True)

    user = db.relationship('User', backref=db.backref('notification_preference', uselist=False))


class NotificationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    bucket = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    sent_at = db.Column(db.DateTime, default=_utcnow, nullable=False)

    user = db.relationship('User')

    __table_args__ = (
        db.UniqueConstraint('event_type', 'entity_type', 'entity_id', 'bucket', 'user_id', name='uq_notification_dedup'),
    )


class CsvImportLog(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entity_type = db.Column(db.String(20), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=_utcnow, nullable=False)
    total_rows = db.Column(db.Integer, default=0)
    success_count = db.Column(db.Integer, default=0)
    duplicate_count = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='success')
    error_message = db.Column(db.Text, nullable=True)

    uploader = db.relationship('User', foreign_keys=[uploaded_by_id])


# ---------------------------------------------------------------------------
# Maintaink12 M&O models — Audit Log (Phase 12)
#
# One row per changed FIELD (not per save): who, what entity, which field,
# old value, new value, when. Written automatically by application/audit.py's
# Session before_flush listener for every model in audit.TRACKED — routes
# never write these by hand, so a new field or a new route can't forget to.
# Append-only by convention (no route edits or deletes rows; there is no
# ORM guard like AssetConditionHistory's because the admin page is
# read-only and nothing else references the table).
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entity_type = db.Column(db.String(40), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=True, index=True)
    entity_label = db.Column(db.String(200), nullable=True)
    action = db.Column(db.String(10), nullable=False)  # create | update | delete
    field = db.Column(db.String(60), nullable=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=_utcnow, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])


class ConditionHistoryImmutableError(Exception):
    """Raised when code tries to update or delete an AssetConditionHistory row."""


@event.listens_for(AssetConditionHistory, 'before_update')
def _forbid_condition_history_update(mapper, connection, target):
    raise ConditionHistoryImmutableError(
        f'AssetConditionHistory #{target.id} is append-only and cannot be modified.')


@event.listens_for(AssetConditionHistory, 'before_delete')
def _forbid_condition_history_delete(mapper, connection, target):
    raise ConditionHistoryImmutableError(
        f'AssetConditionHistory #{target.id} is append-only and cannot be deleted.')
