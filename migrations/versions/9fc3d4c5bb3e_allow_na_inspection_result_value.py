"""allow N/A inspection result value

Revision ID: 9fc3d4c5bb3e
Revises: 6022eeaa1e8d
Create Date: 2026-10-05 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '9fc3d4c5bb3e'
down_revision = '6022eeaa1e8d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('inspection_result', schema=None) as batch_op:
        batch_op.drop_constraint('ck_inspection_result_value', type_='check')
        batch_op.create_check_constraint('ck_inspection_result_value',
                                          "result IN ('Pass', 'Fail', 'Needs Attention', 'N/A')")


def downgrade():
    with op.batch_alter_table('inspection_result', schema=None) as batch_op:
        batch_op.drop_constraint('ck_inspection_result_value', type_='check')
        batch_op.create_check_constraint('ck_inspection_result_value',
                                          "result IN ('Pass', 'Fail', 'Needs Attention')")
