"""add inspection cycles and inspection attachments

Revision ID: 6022eeaa1e8d
Revises: 13e58200527c
Create Date: 2026-10-04 18:25:57.515261

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6022eeaa1e8d'
down_revision = '13e58200527c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('inspection_cycle',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('inspection_attachment',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('inspection_id', sa.Integer(), nullable=False),
    sa.Column('attach_file', sa.String(length=255), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['inspection_id'], ['inspection.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cycle_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_inspection_cycle_id'), ['cycle_id'], unique=False)

    # Same reasoning as a370d33e0db2 (Vendor/WorkOrder.vendor_id): SQLite
    # can't add a column with a new FK constraint to an EXISTING table
    # without a full table recreate, which requires every one of the
    # table's pre-existing constraints to be reflectable with a name --
    # ours aren't. MySQL (production) has no such limitation. SQLite gets
    # the column and the ORM-level relationship (Inspection.cycle /
    # models.py), just not a database-enforced constraint on this column.
    if op.get_bind().dialect.name != 'sqlite':
        with op.batch_alter_table('inspection', schema=None) as batch_op:
            batch_op.create_foreign_key(None, 'inspection_cycle', ['cycle_id'], ['id'])


def downgrade():
    if op.get_bind().dialect.name != 'sqlite':
        with op.batch_alter_table('inspection', schema=None) as batch_op:
            batch_op.drop_constraint(None, type_='foreignkey')

    with op.batch_alter_table('inspection', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inspection_cycle_id'))
        batch_op.drop_column('cycle_id')

    op.drop_table('inspection_attachment')
    op.drop_table('inspection_cycle')
