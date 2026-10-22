"""add project task scheduling (start_date, percent_complete, is_milestone,
updated_at columns + project_task_dependency table)

Revision ID: 9ac19aab71d8
Revises: 735bd883f0ec
Create Date: 2026-10-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9ac19aab71d8'
down_revision = '735bd883f0ec'
branch_labels = None
depends_on = None


def upgrade():
    # Plain scalar columns, no FK involved -> no batch_alter_table needed
    # even on SQLite (that's only required when adding a FK to an existing
    # table, like work_order.maintenance_plan_id in the prior migration).
    op.add_column('project_task', sa.Column('start_date', sa.Date(), nullable=True))
    op.add_column('project_task', sa.Column('percent_complete', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('project_task', sa.Column('is_milestone', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('project_task', sa.Column('updated_at', sa.DateTime(), nullable=True))
    # Adding a CHECK constraint to an existing table needs batch mode on
    # SQLite (same class of limitation as adding a FK — see the prior
    # migration's work_order.maintenance_plan_id); plain ALTER on MySQL.
    with op.batch_alter_table('project_task', schema=None) as batch_op:
        batch_op.create_check_constraint(
            'ck_project_task_percent_complete_range',
            'percent_complete >= 0 AND percent_complete <= 100',
        )

    op.create_table('project_task_dependency',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('predecessor_id', sa.Integer(), nullable=False),
    sa.Column('successor_id', sa.Integer(), nullable=False),
    sa.Column('dependency_type', sa.String(length=2), nullable=False),
    sa.Column('lag_days', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['predecessor_id'], ['project_task.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['successor_id'], ['project_task.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('predecessor_id', 'successor_id', name='uq_project_task_dependency_pair'),
    sa.CheckConstraint('predecessor_id != successor_id', name='ck_project_task_dependency_not_self'),
    )
    op.create_index(op.f('ix_project_task_dependency_predecessor_id'), 'project_task_dependency', ['predecessor_id'], unique=False)
    op.create_index(op.f('ix_project_task_dependency_successor_id'), 'project_task_dependency', ['successor_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_project_task_dependency_successor_id'), table_name='project_task_dependency')
    op.drop_index(op.f('ix_project_task_dependency_predecessor_id'), table_name='project_task_dependency')
    op.drop_table('project_task_dependency')

    with op.batch_alter_table('project_task', schema=None) as batch_op:
        batch_op.drop_constraint('ck_project_task_percent_complete_range', type_='check')
    op.drop_column('project_task', 'updated_at')
    op.drop_column('project_task', 'is_milestone')
    op.drop_column('project_task', 'percent_complete')
    op.drop_column('project_task', 'start_date')
