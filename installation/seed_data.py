# seed_data.py
import os
from getpass import getpass
from dotenv import load_dotenv

# Adjust these imports if installation/ is not a package or outside PYTHONPATH
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import create_app, db
from application.models import Organization, User, Role, Site, Category, Priority, InspectionTemplate, InspectionItem

from werkzeug.security import generate_password_hash
from sqlalchemy.exc import SQLAlchemyError






# Load environment variables
load_dotenv()
app = create_app()

admin_email = input("Enter admin email (e.g., admin@yourdomain.edu): ")
admin_password = getpass("Enter admin password (min 10 chars, letters, digits, symbol): ")
admin_first_name = input("Enter admin first name (default: Admin): ") or "Admin"
admin_last_name = input("Enter admin last name (default: User): ") or "User"

with app.app_context():
    try:
        db.create_all()
        
        # --- Organization ---
        school_district_name = os.getenv('DEFAULT_ORGANIZATION_NAME') or 'Default Organization'
        organization = db.session.get(Organization, 1)

        if organization:
            organization.organization_name = school_district_name
            organization.site_version = '1.0'
            print("Organization updated.")
        else:
            organization = Organization(id=1, organization_name=school_district_name, site_version='1.0')
            db.session.add(organization)
            print("Organization created.")

        # --- Roles ---
        # Only 3 permission tiers actually exist in code (User.is_admin /
        # is_tech_role in application/models.py): Admin, Specialist+Technician
        # ("M&O staff"), and everyone else. Staff covers any site user who
        # submits/views their own requests — teacher, office staff,
        # custodian, etc. — since nothing distinguishes them further.
        roles = [
            ('1', 'Admin'),
            ('2', 'Specialist'),
            ('3', 'Technician'),
            ('4', 'Staff'),
        ]
        for role_id, role_name in roles:
            if not Role.query.filter_by(role_name=role_name).first():
                db.session.add(Role(id=int(role_id), role_name=role_name))
                print(f"Role added: {role_name}")
            else:
                print(f"Role already exists: {role_name}")

        # --- Site ---
        site_data = {
            'id': 1,
            'site_name': 'District Office',
            'site_cds': '99-99999-9999999',
            'site_code': '012345',
            'site_address': '1234 Main St.',
            'site_type': 'District Office',
            'site_acronyms': 'DO'
        }
        existing_site = Site.query.filter_by(site_cds=site_data['site_cds']).first()
        if existing_site:
            for key, value in site_data.items():
                setattr(existing_site, key, value)
            print("Site updated.")
        else:
            db.session.add(Site(**site_data))
            print("Site created.")

        # --- Admin User ---
        user = User.query.filter_by(email=admin_email).first()
        if user:
            user.first_name = admin_first_name
            user.last_name = admin_last_name
            user.password = generate_password_hash(admin_password)
            print("Admin user updated.")
        else:
            db.session.add(User(
                first_name=admin_first_name,
                middle_name='',
                last_name=admin_last_name,
                email=admin_email,
                password=generate_password_hash(admin_password),
                status='Active',
                rm_num='999',
                site_id=1,
                role_id=1
            ))
            print("Admin user created.")

        # --- Work order priorities/categories & inspection question presets (Maintaink12) ---
        from application.reference_data import seed_reference_data
        added = seed_reference_data(db)
        print(f"Reference data: {added} priority/category/preset-question rows added.")

        # --- Inspection templates (Maintaink12) ---
        # The 6 starter checklist templates this app ships with, each filled
        # with a relevant subset of the preset questions just seeded above
        # (same subsets used to backfill these templates in the dev DB).
        SAFETY = [
            'Exit signs illuminated and visible?',
            'Fire extinguisher present, charged, and accessible?',
            'Smoke detector present and functional?',
            'Emergency lighting operational?',
            'Exits and aisles unobstructed?',
            'Floors free of trip/slip hazards?',
        ]
        ELECTRICAL = [
            'Light fixtures functioning properly?',
            'No exposed wiring or electrical hazards?',
            'Outlets and switches in good condition?',
        ]
        HVAC_PLUMBING = [
            'HVAC system operating properly?',
            'No signs of water leaks or damage?',
            'Plumbing fixtures functioning properly?',
            'Pressure gauge in operable range?',
        ]
        STRUCTURAL = [
            'Ceiling tiles in place and undamaged?',
            'Walls and doors in good repair?',
            'Windows functioning and free of damage?',
            'Room clean and free of clutter?',
        ]
        PLAYGROUND = [
            'Playground equipment free of visible damage?',
            'Surfacing material adequate and free of hazards?',
            'Fencing and gates secure?',
        ]
        TECH = [
            'Projector/display equipment functioning?',
            'Network/data jacks functional?',
        ]
        DEFAULT_INSPECTION_TEMPLATES = (
            # name, category name, priority name, questions
            ('Annual Facility Audit', 'General Maintenance', 'Medium',
             SAFETY + ELECTRICAL + HVAC_PLUMBING + STRUCTURAL + PLAYGROUND + TECH),
            ('Playgrounds & Outdoor Facilities', 'Playground', 'Medium', PLAYGROUND),
            ('Safety & Equipment', 'Fire/Life Safety', 'Medium', SAFETY),
            ('Office Spaces', 'HVAC', 'Medium', ELECTRICAL + STRUCTURAL + TECH),
            ('Classrooms', 'Other', 'Medium', SAFETY + STRUCTURAL + TECH),
            ('HVAC Systems', 'HVAC', 'Medium', HVAC_PLUMBING),
        )
        templates_added = 0
        items_added = 0
        for name, category_name, priority_name, questions in DEFAULT_INSPECTION_TEMPLATES:
            category = Category.query.filter_by(name=category_name).first()
            priority = Priority.query.filter_by(name=priority_name).first()
            if not category or not priority:
                print(f"Skipping template {name!r}: category/priority not found.")
                continue
            template = InspectionTemplate.query.filter_by(name=name).first()
            if not template:
                template = InspectionTemplate(name=name, category_id=category.id, priority_id=priority.id)
                db.session.add(template)
                db.session.flush()
                templates_added += 1
                print(f"Inspection template added: {name}")
            existing_questions = {it.question for it in template.items}
            for sort_order, question in enumerate(questions):
                if question not in existing_questions:
                    db.session.add(InspectionItem(template_id=template.id, question=question, sort_order=sort_order))
                    items_added += 1
        db.session.commit()
        print(f"Inspection templates: {templates_added} added, {items_added} checklist items added.")

        print("All data seeded successfully.")

    except SQLAlchemyError as err:
        db.session.rollback()
        print(f"SQLAlchemy Error: {err}")
