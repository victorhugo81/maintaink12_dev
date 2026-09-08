"""add vendor table and work order vendor link

Revision ID: a370d33e0db2
Revises: e7efb8ead9da
Create Date: 2026-09-07 19:11:33.079738

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a370d33e0db2'
down_revision = 'e7efb8ead9da'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('vendor',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('contact_name', sa.String(length=100), nullable=True),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('address', sa.String(length=255), nullable=True),
    sa.Column('contract_start_date', sa.Date(), nullable=True),
    sa.Column('contract_end_date', sa.Date(), nullable=True),
    sa.Column('insurance_expiration', sa.Date(), nullable=True),
    sa.Column('license_expiration', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    with op.batch_alter_table('work_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vendor_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_work_order_vendor_id'), ['vendor_id'], unique=False)

    # SQLite can't add a column with a new FK constraint to an EXISTING table
    # without a full table recreate, and that recreate requires every one of
    # the table's pre-existing constraints to be reflectable with a name --
    # ours aren't (plain `db.ForeignKey(...)` on a Column, never given an
    # explicit `name=`). MySQL (production) has no such limitation: a plain
    # ADD COLUMN + ADD CONSTRAINT works there without recreating anything. So
    # the DB-level constraint is added only on backends where it's safe;
    # SQLite gets the column and the ORM-level relationship (application/
    # models.py's WorkOrder.vendor / Vendor.work_orders), just not a
    # database-enforced constraint on this one column.
    if op.get_bind().dialect.name != 'sqlite':
        with op.batch_alter_table('work_order', schema=None) as batch_op:
            batch_op.create_foreign_key(None, 'vendor', ['vendor_id'], ['id'])


def downgrade():
    if op.get_bind().dialect.name != 'sqlite':
        with op.batch_alter_table('work_order', schema=None) as batch_op:
            batch_op.drop_constraint(None, type_='foreignkey')

    with op.batch_alter_table('work_order', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_work_order_vendor_id'))
        batch_op.drop_column('vendor_id')

    op.drop_table('vendor')
