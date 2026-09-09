"""
Audit log (Phase 12): who changed what, old value, new value, when — for
work orders, assets, facilities, rooms, users, roles/permissions, costs,
projects, vendors and SLA rules.

Implemented as ONE SQLAlchemy Session `before_flush` listener rather than
per-route bookkeeping, so every code path that saves a tracked model
(routes, the PM generator, the CSV importer, the notification sweep, a
future phase) is audited without remembering to call anything. For each
tracked instance in the flush: a 'create' row for new objects, one 'update'
row PER CHANGED COLUMN for dirty ones (via the attribute history SQLAlchemy
already keeps), and a 'delete' row for deleted ones.

Sensitive columns (password hash, encrypted/hashed email, lockout state)
are still logged as "changed" but with their values masked — the audit
trail should show that a password was reset, never what it was.

The acting user is read from flask_login's current_user when there is a
request context; background jobs log user_id=None ("System").
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

TRACKED = ('WorkOrder', 'Asset', 'Facility', 'Room', 'User', 'Role', 'CostRecord', 'WorkOrderLabor',
           'WorkOrderMaterial', 'Project', 'ProjectCost', 'Vendor', 'SLARule', 'AssetConditionHistory')

IGNORED_FIELDS = frozenset(('id', 'created_at', 'updated_at', 'wo_number', 'last_generated_at'))
MASKED_FIELDS = frozenset(('password', 'email_enc', 'email_hash', 'failed_login_attempts', 'locked_until'))
MASK = '***'


def _label(obj):
    for attr in ('wo_number', 'asset_tag', 'name', 'room_number', 'role_name'):
        value = getattr(obj, attr, None)
        if value:
            return str(value)[:200]
    if hasattr(obj, 'get_full_name'):
        return obj.get_full_name()[:200]
    return None


def _fmt(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)[:2000]


def _acting_user_id():
    try:
        from flask import has_request_context
        from flask_login import current_user
        if has_request_context() and getattr(current_user, 'is_authenticated', False):
            return current_user.id
    except Exception:
        pass
    return None


def _committed_value(session, state, attr):
    """The value currently in the database for one column — used when the
    attribute was expired by an earlier commit, so SQLAlchemy's history has
    no 'deleted' side to read the old value from. Core SELECT on the flush's
    own connection: no autoflush, so no recursion into this listener."""
    from sqlalchemy import select
    if not state.persistent or not state.identity:
        return None
    pk = state.mapper.primary_key[0]
    return session.connection().execute(select(attr.columns[0]).where(pk == state.identity[0])).scalar()


def _entries_for(session, obj, action):
    from application.models import AuditLog
    entity_type = type(obj).__name__
    user_id = _acting_user_id()
    if action != 'update':
        return [AuditLog(entity_type=entity_type, entity_id=getattr(obj, 'id', None), entity_label=_label(obj),
                         action=action, user_id=user_id)]
    rows = []
    state = inspect(obj)
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in IGNORED_FIELDS:
            continue
        hist = state.attrs[key].history
        if not hist.has_changes():
            continue
        old = hist.deleted[0] if hist.deleted else _committed_value(session, state, attr)
        new = hist.added[0] if hist.added else None
        if _fmt(old) == _fmt(new):
            continue
        if key in MASKED_FIELDS:
            old, new = (MASK if old is not None else None), (MASK if new is not None else None)
        rows.append(AuditLog(entity_type=entity_type, entity_id=obj.id, entity_label=_label(obj), action='update',
                             field=key, old_value=_fmt(old), new_value=_fmt(new), user_id=user_id))
    return rows


@event.listens_for(Session, 'before_flush')
def _audit_before_flush(session, flush_context, instances):
    from application.models import AuditLog
    pending = []
    for obj in list(session.new):
        if type(obj).__name__ in TRACKED:
            pending.append(obj)
    for obj in list(session.dirty):
        if type(obj).__name__ in TRACKED and session.is_modified(obj, include_collections=False):
            pending.extend(_entries_for(session, obj, 'update'))
    for obj in list(session.deleted):
        if type(obj).__name__ in TRACKED:
            pending.extend(_entries_for(session, obj, 'delete'))
    # New objects have no id until after flush — record them in after_flush.
    session.info.setdefault('audit_new', []).extend(o for o in pending if not isinstance(o, AuditLog))
    for row in pending:
        if isinstance(row, AuditLog):
            session.add(row)


@event.listens_for(Session, 'after_flush')
def _audit_after_flush(session, flush_context):
    from application.models import AuditLog
    new_objs = session.info.pop('audit_new', [])
    if not new_objs:
        return
    user_id = _acting_user_id()
    for obj in new_objs:
        session.add(AuditLog(entity_type=type(obj).__name__, entity_id=getattr(obj, 'id', None),
                             entity_label=_label(obj), action='create', user_id=user_id))
