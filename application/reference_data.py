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


def seed_reference_data(db):
    from application.models import Priority, Category

    added = 0
    for name, sort_order, color, description in DEFAULT_PRIORITIES:
        if not Priority.query.filter_by(name=name).first():
            db.session.add(Priority(name=name, sort_order=sort_order, color=color, description=description))
            added += 1
    for sort_order, name in enumerate(DEFAULT_CATEGORIES, start=1):
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, sort_order=sort_order))
            added += 1
    db.session.commit()
    return added
