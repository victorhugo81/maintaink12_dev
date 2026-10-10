"""
Default admin-editable reference data from docs/PROJECT_PLAN.md. Seeding is
idempotent — existing rows are left alone — so it's safe for seed_data.py,
tests, and re-runs.
"""

DEFAULT_PRIORITIES = (
    # name, sort_order, badge color, description
    ('Emergency', 1, 'danger', 'Life safety, major damage, or critical infrastructure — immediate response'),
    ('Critical', 2, 'danger', 'Major operational impact'),
    ('High', 3, 'warning', 'Prompt attention'),
    ('Medium', 4, 'info', 'Normal request'),
    ('Low', 5, 'secondary', 'Routine'),
)

DEFAULT_CATEGORIES = (
    'HVAC', 'Electrical', 'Plumbing', 'Roofing', 'Doors & Locks', 'Grounds', 'Landscaping',
    'Custodial', 'Pest Control', 'Fire/Life Safety', 'Security', 'Lighting', 'Flooring',
    'Painting', 'Carpentry', 'Furniture', 'Playground', 'Kitchen Equipment', 'Appliances',
    'Irrigation', 'Parking', 'Transportation', 'General Maintenance', 'Other',
)

# Starter library for the Preset Question dropdown on
# edit_inspection_template.html's "Add Item" form (InspectionQuestionPreset,
# Phase 14) — same list migrations/versions/f1a2b3c4d5e6_*.py seeds for an
# existing install; kept duplicated here (not imported from there) since a
# migration's data should stay frozen in time, not track application code.
DEFAULT_INSPECTION_QUESTION_PRESETS = (
    # category, question
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
)


def seed_reference_data(db):
    from application.models import Priority, Category, InspectionQuestionPreset

    added = 0
    for name, sort_order, color, description in DEFAULT_PRIORITIES:
        if not Priority.query.filter_by(name=name).first():
            db.session.add(Priority(name=name, sort_order=sort_order, color=color, description=description))
            added += 1
    for sort_order, name in enumerate(DEFAULT_CATEGORIES, start=1):
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, sort_order=sort_order))
            added += 1
    for sort_order, (category, question) in enumerate(DEFAULT_INSPECTION_QUESTION_PRESETS):
        if not InspectionQuestionPreset.query.filter_by(question=question).first():
            db.session.add(InspectionQuestionPreset(category=category, question=question, sort_order=sort_order))
            added += 1
    db.session.commit()
    return added
