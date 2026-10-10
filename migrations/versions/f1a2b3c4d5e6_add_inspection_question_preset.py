"""add inspection question preset

Revision ID: f1a2b3c4d5e6
Revises: 9fc3d4c5bb3e
Create Date: 2026-10-10 00:00:00.000000

"""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = '9fc3d4c5bb3e'
branch_labels = None
depends_on = None

# Starter library — the same set offered as a hardcoded <select> before this
# table existed (see edit_inspection_template.html); seeded here so the
# dropdown isn't empty on day one, not because the app depends on any of it.
_PRESETS = [
    ('Safety & Life Safety', 'Exit signs illuminated and visible?'),
    ('Safety & Life Safety', 'Fire extinguisher present, charged, and accessible?'),
    ('Safety & Life Safety', 'Smoke detector present and functional?'),
    ('Safety & Life Safety', 'Emergency lighting operational?'),
    ('Safety & Life Safety', 'Exits and aisles unobstructed?'),
    ('Safety & Life Safety', 'Floors free of trip/slip hazards?'),
    ('Electrical', 'Light fixtures functioning properly?'),
    ('Electrical', 'No exposed wiring or electrical hazards?'),
    ('Electrical', 'Outlets and switches in good condition?'),
    ('HVAC & Plumbing', 'HVAC system operating properly?'),
    ('HVAC & Plumbing', 'No signs of water leaks or damage?'),
    ('HVAC & Plumbing', 'Plumbing fixtures functioning properly?'),
    ('HVAC & Plumbing', 'Pressure gauge in operable range?'),
    ('Structural & Housekeeping', 'Ceiling tiles in place and undamaged?'),
    ('Structural & Housekeeping', 'Walls and doors in good repair?'),
    ('Structural & Housekeeping', 'Windows functioning and free of damage?'),
    ('Structural & Housekeeping', 'Room clean and free of clutter?'),
    ('Playground & Outdoor', 'Playground equipment free of visible damage?'),
    ('Playground & Outdoor', 'Surfacing material adequate and free of hazards?'),
    ('Playground & Outdoor', 'Fencing and gates secure?'),
    ('Technology', 'Projector/display equipment functioning?'),
    ('Technology', 'Network/data jacks functional?'),
]


def upgrade():
    op.create_table('inspection_question_preset',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('question', sa.String(length=255), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    preset_table = sa.table('inspection_question_preset',
        sa.column('category', sa.String), sa.column('question', sa.String),
        sa.column('sort_order', sa.Integer), sa.column('created_at', sa.DateTime))
    now = datetime.now(timezone.utc)
    op.bulk_insert(preset_table, [
        {'category': category, 'question': question, 'sort_order': i, 'created_at': now}
        for i, (category, question) in enumerate(_PRESETS)
    ])


def downgrade():
    op.drop_table('inspection_question_preset')
