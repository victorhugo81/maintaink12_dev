"""add maintenance checklist (PM checklists)

Revision ID: 735bd883f0ec
Revises: f1a2b3c4d5e6
Create Date: 2026-10-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '735bd883f0ec'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('maintenance_checklist_item',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('maintenance_plan_id', sa.Integer(), nullable=False),
    sa.Column('task', sa.String(length=255), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['maintenance_plan_id'], ['maintenance_plan.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_maintenance_checklist_item_maintenance_plan_id'), 'maintenance_checklist_item', ['maintenance_plan_id'], unique=False)

    with op.batch_alter_table('work_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('maintenance_plan_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_work_order_maintenance_plan_id'), ['maintenance_plan_id'], unique=False)
        batch_op.create_foreign_key('fk_work_order_maintenance_plan_id', 'maintenance_plan', ['maintenance_plan_id'], ['id'], ondelete='SET NULL')

    op.create_table('work_order_checklist_result',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('work_order_id', sa.Integer(), nullable=False),
    sa.Column('checklist_item_id', sa.Integer(), nullable=False),
    sa.Column('is_checked', sa.Boolean(), nullable=False),
    sa.Column('checked_at', sa.DateTime(), nullable=True),
    sa.Column('checked_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['work_order_id'], ['work_order.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['checklist_item_id'], ['maintenance_checklist_item.id'], ),
    sa.ForeignKeyConstraint(['checked_by_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('work_order_id', 'checklist_item_id', name='uq_wo_checklist_result_item')
    )
    op.create_index(op.f('ix_work_order_checklist_result_work_order_id'), 'work_order_checklist_result', ['work_order_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_work_order_checklist_result_work_order_id'), table_name='work_order_checklist_result')
    op.drop_table('work_order_checklist_result')

    with op.batch_alter_table('work_order', schema=None) as batch_op:
        batch_op.drop_constraint('fk_work_order_maintenance_plan_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_work_order_maintenance_plan_id'))
        batch_op.drop_column('maintenance_plan_id')

    op.drop_index(op.f('ix_maintenance_checklist_item_maintenance_plan_id'), table_name='maintenance_checklist_item')
    op.drop_table('maintenance_checklist_item')
