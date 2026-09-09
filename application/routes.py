from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, current_app, send_from_directory, send_file, jsonify, session
from flask_limiter.util import get_remote_address
from flask_login import login_user, login_required, logout_user, current_user
from flask_paginate import Pagination, get_page_args
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from .models import User, Role, Site, Notification, Organization, Ticket, Title, Ticket_content, Ticket_attachment, BulkUploadLog, Facility, Floor, Room, FacilityAttachment, RoomAttachment, AssetType, Asset, AssetConditionHistory, AssetAttachment, condition_label_for_score, CONDITION_SCALE, Priority, Category, Subcategory, WorkOrder, WorkOrderComment, WorkOrderAttachment, WorkOrderStatusHistory, MaintenancePlan, MaintenanceSchedule, InspectionTemplate, InspectionItem, Inspection, InspectionResult, INSPECTION_RESULTS, Vendor, WorkOrderMaterial, WorkOrderLabor, CostRecord, Project, ProjectTask, ProjectCost, ProjectDocument, PROJECT_STATUSES, SLARule, NotificationPreference, NotificationLog, NOTIFICATION_EVENTS, NOTIFICATION_EVENT_LABELS, CsvImportLog, AuditLog
from .forms import LoginForm, UserForm, RoleForm, SiteForm, NotificationForm, OrganizationForm, EmailConfigForm, TicketForm, TitleForm, TicketContentForm, FacilityForm, FloorForm, RoomForm, AssetTypeForm, AssetForm, AssetConditionForm, PriorityForm, CategoryForm, SubcategoryForm, WorkOrderRequestForm, WorkOrderForm, WorkOrderStatusForm, WorkOrderCommentForm, MaintenancePlanForm, InspectionTemplateForm, InspectionItemForm, InspectionForm, InspectionResultsForm, InspectionResultItemForm, VendorForm, WorkOrderLaborForm, WorkOrderMaterialForm, CostRecordForm, ProjectForm, ProjectTaskForm, ProjectCostForm, ProjectVendorForm, AssetRiskFieldsForm, SLARuleForm, NotificationPreferenceForm, CsvImportUploadForm
from .utils import validate_password, validate_file_upload, encrypt_mail_password, decrypt_mail_password, hash_email, get_app_version
from .email_utils import send_ticket_notification, send_temp_password_email, send_password_updated_email, send_work_order_notification
from . import workflow
from .workflow import WorkflowError
from . import pm
from . import inspections as inspections_module
from . import vendors as vendors_module
from . import costs as costs_module
from . import risk
from . import projects as projects_module
from . import analytics
from . import reports as reports_module
from . import sla as sla_module
from . import notifications as notifications_module
from . import search as search_module
from . import csv_import as csv_import_module
from main import db, login_manager, mail, limiter, scheduler
from flask_mail import Message
from datetime import datetime, timedelta, timezone
import time, os, re, csv, logging, secrets, ftplib, io, socket
import qrcode
from sqlalchemy.sql import func
from flask_caching import Cache
from sqlalchemy import case

# Cache configuration for storing database query results
# Using simple cache type with 2-hour expiration for assigned users query
cache = Cache(config={'CACHE_TYPE': 'simple'})

# Cached function to retrieve users with specific roles (1 and 2)
# This avoids repeated database queries for frequently accessed user data
@cache.cached(timeout=7200, key_prefix='assigned_users')
def get_assigned_users():
    """
    Retrieve all users with role IDs 1 or 2 from the database.
    Results are cached for 2 hours to improve performance.
    
    Returns:
        list: List of User objects with role_id 1 or 2
    """
    return User.query.filter(User.role_id.in_([1, 2])).all()


# Create a Blueprint for organizing routes
# This allows for modular application structure and route organization
routes_blueprint = Blueprint('routes', __name__)

# Fixed hash checked (and discarded) when a login is attempted for an email
# that doesn't exist, so check_password_hash() always runs and the response
# time can't be used to enumerate which accounts exist.
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

@routes_blueprint.app_context_processor
def inject_active_notifications():
    try:
        notifications = Notification.query.filter_by(msg_status='Active').all()
    except Exception:
        notifications = []
    return dict(active_notifications=notifications)


@routes_blueprint.app_context_processor
def inject_app_version():
    """Expose the current app version (parsed from CHANGELOG.md) to every
    template — used by the About modal in the footer."""
    return dict(app_version=get_app_version())


# *****************************************************************
#-------------------- Core Setup -------------------------
# -------------- Do not change this section --------------
# *****************************************************************


# ****************** Force Password Change Enforcement *************
@routes_blueprint.before_request
def enforce_password_change():
    """Redirect users with a temporary password to the set-password page before they can do anything else."""
    if current_user.is_authenticated and getattr(current_user, 'must_change_password', False):
        allowed = {'routes.set_password', 'routes.logout', 'static'}
        if request.endpoint not in allowed:
            return redirect(url_for('routes.set_password'))



# ****************** Set Password (temp password flow) *************
@routes_blueprint.route('/set-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_remote_address)
@login_required
def set_password():
    org = db.session.get(Organization, 1)
    organization_name = org.organization_name if org else 'AssistITk12'

    if request.method == 'POST':
        new_password     = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not new_password or not confirm_password:
            flash('Both fields are required.', 'danger')
            return render_template('change_password.html', organization_name=organization_name)

        if new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return render_template('change_password.html', organization_name=organization_name)

        is_valid, error_message = validate_password(new_password)
        if not is_valid:
            flash(error_message, 'danger')
            return render_template('change_password.html', organization_name=organization_name)

        current_user.password = generate_password_hash(new_password)
        current_user.must_change_password = False
        db.session.add(current_user)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"set_password failed for user {current_user.id}: {e}", exc_info=True)
            flash('An error occurred while saving your password. Please try again.', 'danger')
            return render_template('change_password.html', organization_name=organization_name)
        flash('Password updated successfully. Welcome!', 'success')
        return redirect(url_for('routes.index'))

    return render_template('change_password.html', organization_name=organization_name)



# ****************** Login Setup *******************************
@login_manager.user_loader
def load_user(user_id):
    """
    Flask-Login user loader callback.
    Loads a user from the database for session management.
    
    Args:
        user_id (str): The user ID to load from database
        
    Returns:
        User: The User object for the specified ID
    """
    return db.session.get(User, int(user_id))

# ****************** Admin *******************************
def is_admin():
    """
    Check if the current user has admin privileges.
    Abort with 403 Forbidden if the user is not an admin.
    
    Assumes role_id 1 represents Admin status.
    """
    if not current_user.is_authenticated or current_user.role_id != 1:  # Assuming 1 = Admin
        abort(403)

def is_tech_role():
    """
    Check if the current user has a technical role.
    Abort with 403 Forbidden if the user is not in a tech role.

    Technical roles are Specialist (role_id=2) and Technician (role_id=3).
    """
    if not current_user.is_authenticated or current_user.role_id not in [2, 3]:  # Assuming 2 = Specialist, 3 = Technician
        abort(403)

def can_access_ticket(ticket):
    """
    Central authorization check for ticket detail/comment/attachment routes.

    Admins and Specialists (1, 2) can access any ticket. Technicians (3) are
    scoped to their own site — matching the filtering already applied on the
    /tickets list route — so a Technician can't reach another site's ticket
    just by guessing/incrementing the ticket_id in the URL. Everyone else may
    only access tickets they created or are assigned to.
    """
    if current_user.role_id in (1, 2):
        return True
    if current_user.role_id == 3:
        return ticket.site_id == current_user.site_id
    return current_user.id == ticket.user_id or current_user.id == ticket.assigned_to_id

def can_access_site(site_id):
    """
    Site-scoping check for Facility/Room routes, mirroring can_access_ticket.

    Admins and Specialists (1, 2) can view/manage facilities and rooms at any
    site. Everyone else (Technicians included) is scoped to their own site,
    matching the site-level access described in PROJECT_PLAN.md.
    """
    if current_user.role_id in (1, 2):
        return True
    return current_user.site_id == site_id

# ****************** Forbidden Error Page *******************************
@routes_blueprint.app_errorhandler(403)
def forbidden_error(error):
    """
    Custom 403 error handler for the application.
    Renders a custom error page when access is forbidden.
    
    Args:
        error: The error that triggered this handler
        
    Returns:
        tuple: Rendered error template and 403 status code
    """
    return render_template('error.html'), 403


# ****************** Login Page *******************************
@routes_blueprint.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", key_func=get_remote_address)
def login():
    """
    Handle user login requests.

    GET: Display the login form
    POST: Process the login form submission

    Returns:
        Response: Rendered login template or redirect to index on successful login
    """
    # Fetch organization name for display on login page
    organization = db.session.get(Organization, 1)
    organization_name = organization.organization_name if organization else "AssistITk12"

    _MAX_ATTEMPTS = 5
    _LOCKOUT_MINUTES = 15
    _GENERIC_FAILURE = 'Login failed. Please check your credentials.'

    form = LoginForm()
    if form.validate_on_submit():
        _key = current_app.config['SECRET_KEY']
        user = User.query.filter_by(email_hash=hash_email(form.email.data, _key)).first()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        locked = bool(user and user.locked_until and user.locked_until > now)

        # Always run a hash comparison, even for a non-existent account or a
        # locked one, so response time doesn't reveal which case occurred
        # (account enumeration via timing side-channel).
        password_ok = check_password_hash(
            user.password if user else _DUMMY_PASSWORD_HASH, form.password.data
        )

        if user and not locked and password_ok and user.status == 'Active':
            # Successful login — reset lockout counters
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            session.clear()
            session.permanent = True  # enforce PERMANENT_SESSION_LIFETIME
            login_user(user)
            if user.must_change_password:
                return redirect(url_for('routes.set_password'))
            return redirect(url_for('routes.index'))

        # Every other outcome (no such user, wrong password, inactive, locked)
        # gets the same generic message so the response can't be used to
        # enumerate which accounts exist or their current lock state.
        # Only a genuinely wrong password counts toward the lockout counter —
        # a correct password on a merely-inactive account isn't a guess.
        if user and not locked and not password_ok:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= _MAX_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=_LOCKOUT_MINUTES)
                user.failed_login_attempts = 0
            db.session.commit()
        flash(_GENERIC_FAILURE, 'danger')

    return render_template(
        'login.html',
        form=form,
        organization_name=organization_name
    )


# ****************** Logout *******************************
@routes_blueprint.route('/logout')
@login_required
def logout():
    """
    Log out the currently authenticated user.
    Redirects to the login page after logout.
    
    Returns:
        Response: Redirect to login page
    """
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('routes.login'))



# ****************** Update Organization Page *******************************
@routes_blueprint.route('/organization', methods=['GET', 'POST'])
@login_required
def organization():
    is_admin()
    """
    Display and process organization settings form.
    
    GET: Display the organization settings form
    POST: Process the form submission to update organization details
    
    Returns:
        Response: Rendered organization template or redirect on successful update
    """
    # Map URL paths to readable page names for navigation
    page_names = {'/organization': 'Data Integration'}
    # Get current path for navigation highlighting
    current_path = request.path
    # Get page name for display in UI
    current_page_name = page_names.get(current_path, 'Unknown Page')
    
    # Hardcoding organization_id to 1
    # NOTE: This assumes a single organization in the system
    organization_id = 1
    organization = Organization.query.get_or_404(organization_id)
    
    # Initialize form with current organization data
    form = OrganizationForm(obj=organization)

    # Initialize email config form (pre-populate from DB, but never show password)
    email_form = EmailConfigForm(obj=organization)
    email_form.mail_password.data = ''

    if form.validate_on_submit():
        # Check for duplicate organization names (excluding the current one)
        existing_organization = Organization.query.filter(
            Organization.organization_name == form.organization_name.data,
            Organization.id != organization.id
        ).first()

        if existing_organization:
            flash('An organization with that name already exists.', 'danger')
            return render_template('organization.html', form=form, email_form=email_form, organization=organization)

        # Update organization with form data
        organization.organization_name = form.organization_name.data
        organization.site_version = form.site_version.data
        db.session.commit()  # Save changes to database

        flash('Organization updated successfully!', 'success')
        return redirect(url_for('routes.organization'))

    # For GET requests or invalid form submissions, display the form
    return render_template('organization.html',
                          form=form,
                          email_form=email_form,
                          organization=organization,
                          current_path=current_path,
                          current_page_name=current_page_name)

# *****************************************************************
#-------------------- END Core Setup ---------------------
# -------------- Do not change this section --------------
# *****************************************************************


# ****************** Email Configuration *******************************
@routes_blueprint.route('/email-config', methods=['POST'])
@login_required
def email_config():
    """
    Save Flask-Mail SMTP configuration from the organization settings page.
    Updates the Organization record and immediately applies settings to the running app.
    """
    is_admin()
    organization = Organization.query.get_or_404(1)
    email_form = EmailConfigForm()

    if email_form.validate_on_submit():
        organization.mail_server = email_form.mail_server.data or None
        organization.mail_port = email_form.mail_port.data or None
        organization.mail_use_tls = email_form.mail_use_tls.data
        organization.mail_use_ssl = email_form.mail_use_ssl.data
        organization.mail_username = email_form.mail_username.data or None
        if email_form.mail_password.data:
            organization.mail_password = encrypt_mail_password(
                email_form.mail_password.data, current_app.config['SECRET_KEY']
            )
        organization.mail_default_sender = email_form.mail_default_sender.data or None
        db.session.commit()

        # Apply updated settings to the running Flask-Mail instance
        current_app.config['MAIL_SERVER'] = organization.mail_server or 'localhost'
        current_app.config['MAIL_PORT'] = organization.mail_port or 587
        current_app.config['MAIL_USE_TLS'] = bool(organization.mail_use_tls)
        current_app.config['MAIL_USE_SSL'] = bool(organization.mail_use_ssl)
        current_app.config['MAIL_USERNAME'] = organization.mail_username
        current_app.config['MAIL_PASSWORD'] = decrypt_mail_password(
            organization.mail_password or '', current_app.config['SECRET_KEY']
        )
        current_app.config['MAIL_DEFAULT_SENDER'] = organization.mail_default_sender
        mail.init_app(current_app)

        flash('Email settings updated successfully!', 'success')
    else:
        for field, errors in email_form.errors.items():
            for error in errors:
                flash(f'{field}: {error}', 'danger')

    return redirect(url_for('routes.organization'))


# ****************** Test Email *******************************
@routes_blueprint.route('/email-config/test', methods=['POST'])
@limiter.limit("10 per minute", key_func=get_remote_address)
@login_required
def test_email():
    """
    Send a test email to verify the current Flask-Mail configuration.
    Returns JSON with success/error details.
    """
    is_admin()
    recipient = request.form.get('test_recipient', '').strip()
    if not recipient:
        return jsonify({'success': False, 'message': 'Recipient email is required.'}), 400

    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', recipient):
        return jsonify({'success': False, 'message': 'Invalid recipient email address.'}), 400

    try:
        msg = Message(
            subject='Test Email – AssistITK12',
            recipients=[recipient],
            body=(
                'This is a test email sent from AssistITK12.\n\n'
                'Your email configuration is working correctly.\n\n'
                '— AssistITK12 System'
            )
        )
        mail.send(msg)
        current_app.logger.info(f"Test email sent to {recipient} by user {current_user.id}")
        return jsonify({'success': True, 'message': f'Test email sent to {recipient}.'})
    except Exception as e:
        current_app.logger.error(f"Test email failed: {type(e).__name__}: {e}")
        return jsonify({'success': False, 'message': 'Failed to send email. Check server logs for details.'}), 500


# *****************************************************************
#-------------------- Site Template Pages ---------------------
# *****************************************************************

# *********************************************************************
# ****************** Dashboard Page *******************************
@routes_blueprint.route('/', methods=['GET', 'POST'])
@login_required
def index():
    # Mapping paths to page names
    page_names = {'/': 'Dashboard'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    # Get the current user's details
    current_user_role = current_user.role_id
    current_user_site_id = current_user.site_id
    current_user_id = current_user.id

    # Get the current year and selected year from query parameters
    current_year = datetime.now().year
    selected_year = request.args.get('year', type=int)  # Default is None for "All Years"

    # Fetch available years dynamically from ticket data
    available_years = sorted([
        int(year[0]) for year in Ticket.query.with_entities(
            db.func.extract('year', Ticket.created_at).distinct()
        ).all()
    ], reverse=True)


    # Role-based site filtering 
    if current_user_role in [1, 2]:  # Admin or Manager
        sites = Site.query.all()
        selected_site_id = request.args.get('site_id', type=int)  # Selected site from dropdown        
    elif current_user_role == 3:  # Limited user
        sites = Site.query.filter_by(id=current_user_site_id).all()
        selected_site_id = current_user_site_id
    else:  # Regular user
        sites = Site.query.filter_by(id=current_user_site_id).all()
        selected_site_id = current_user_site_id

    # Base query filter
    query_filter = []
    if selected_year:  # If a specific year is selected, filter by year
        query_filter.append(db.func.extract('year', Ticket.created_at) == selected_year)

    # Query ticket counts based on role and filters
    if current_user_role in [1, 2]:  # Admin or Manager
        if selected_site_id:  # Filter by selected site
            query_filter.append(Ticket.site_id == selected_site_id)
        pending_count = Ticket.query.filter(Ticket.tck_status == '1-pending', *query_filter).count()
        in_progress_count = Ticket.query.filter(Ticket.tck_status == '2-progress', *query_filter).count()
        completed_count = Ticket.query.filter(Ticket.tck_status == '3-completed', *query_filter).count()
    elif current_user_role == 3:  # Limited user: tickets for their site
        query_filter.append(Ticket.site_id == current_user_site_id)
        pending_count = Ticket.query.filter(Ticket.tck_status == '1-pending', *query_filter).count()
        in_progress_count = Ticket.query.filter(Ticket.tck_status == '2-progress', *query_filter).count()
        completed_count = Ticket.query.filter(Ticket.tck_status == '3-completed', *query_filter).count()
    else:  # Regular user: only their own tickets
        query_filter.append(Ticket.user_id == current_user_id)
        pending_count = Ticket.query.filter(Ticket.tck_status == '1-pending', *query_filter).count()
        in_progress_count = Ticket.query.filter(Ticket.tck_status == '2-progress', *query_filter).count()
        completed_count = Ticket.query.filter(Ticket.tck_status == '3-completed', *query_filter).count()

    # Calculate the total count
    total_count = pending_count + in_progress_count + completed_count

    # Query to get the top 5 most popular titles with filters applied
    top_titles_query = (
        db.session.query(Title.id, Title.title_name, func.count(Ticket.id).label('ticket_count'))
        .join(Ticket, Title.id == Ticket.title_id).filter(*query_filter)  # Apply the filters
        .group_by(Title.id, Title.title_name).order_by(func.count(Ticket.id).desc()).limit(5).all())

    # Add an index to the top_titles data
    top_titles = [
        {"rank": idx + 1, "title_id": title_id, "title_name": title_name, "ticket_count": ticket_count}
        for idx, (title_id, title_name, ticket_count) in enumerate(top_titles_query)
    ]

    # Initialize counts for all 12 months
    ticket_counts = {month: 0 for month in range(1, 13)}

    # Fetch tickets and count them per month
    for ticket in db.session.query(Ticket).filter(*query_filter).all():
        month = ticket.created_at.month  # Ensure this is between 1 and 12
        if 1 <= month <= 12:  # Extra safeguard
            ticket_counts[month] += 1

    # Use full month names for clarity
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    counts = [ticket_counts[month] for month in range(1, 13)]  # Ensure all 12 months are included

        # Fetch ticket counts for each weekday (Monday to Friday) for the bar chart
    weekday_counts = {day: 0 for day in range(1, 6)}  # Initialize counts for Monday to Friday
    for ticket in db.session.query(Ticket).filter(*query_filter).all():
        weekday = ticket.created_at.weekday() + 1  # Monday = 1, Sunday = 7
        if weekday in weekday_counts:
            weekday_counts[weekday] += 1

    weekdays = ["M", "T", "W", "Th", "F"]
    weekday_counts_list = [weekday_counts[day] for day in range(1, 6)]


    # Render the template with the context
    return render_template(
        'index.html',
        available_years=available_years,
        selected_year=selected_year,
        sites=sites,
        current_page_name=current_page_name,
        selected_site_id=selected_site_id,
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        completed_count=completed_count,
        total_count=total_count,
        top_titles=top_titles,
        months=months,
        counts=counts,
        weekdays=weekdays,
        weekday_counts=weekday_counts_list
    )


# ***************************************************************
# ****************** Profile Page *******************************
@routes_blueprint.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
        # Mapping paths to page names
    page_names = {'/profile': 'My Profile'}
    # Get the current path
    current_path = request.path
    # Get the corresponding page name or default to "Unknown Page"
    current_page_name = page_names.get(current_path, 'Unknown Page')
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        # Verify current password first
        if not current_password or not check_password_hash(current_user.password, current_password):
            flash('Current password is incorrect.', 'danger')
            return render_template('profile.html', user=current_user, role=current_user.role,
                current_path=current_path, current_page_name=current_page_name)
        # Validate new passwords
        if not password or not confirm_password:
            flash('Both password fields are required.', 'danger')
        elif password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
        else:
            # Validate password complexity
            is_valid, error_message = validate_password(password)
            if not is_valid:
                flash(error_message, 'danger')
                return render_template('profile.html', user=current_user, role=current_user.role,
                    current_path=current_path, current_page_name=current_page_name)

            # Password is valid, proceed with update
            current_user.password = generate_password_hash(password)
            current_user.must_change_password = False
            try:
                db.session.commit()
                flash('Password updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"profile password update failed for user {current_user.id}: {e}", exc_info=True)
                flash('An error occurred while updating your password. Please try again.', 'danger')
            return redirect(url_for('routes.profile'))
    role = current_user.role  # Assuming current_user has a 'role' attribute
    return render_template('profile.html', user=current_user, role=role,
        current_path=current_path, 
        current_page_name=current_page_name
    )



# *********************************************************************
# ****************** Users Management Page ****************************
@routes_blueprint.route('/users', methods=['GET'])
@login_required
def users():
    page_names = {'/users': 'Manage Users'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    
    # Ensure only admins and tech roles can access this route
    if not (current_user.is_admin or current_user.is_tech_role):
        abort(403)

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    search = request.args.get('search', '').strip()
    site_filter = request.args.get('site_filter', '').strip()
    role_filter = request.args.get('role_filter', '').strip()
    query = User.query
    # Apply search filter
    if search:
        query = query.filter(
            db.or_(
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%"),
            )
        )
    # Apply site filter
    if site_filter:
        query = query.filter(User.site_id == site_filter)
    # Apply role filter
    if role_filter:
        query = query.filter(User.role_id == role_filter)
    total = query.count()
    users = query.order_by(User.first_name.asc()).offset(offset).limit(per_page).all()
    # Fetch all sites and roles for the filter dropdowns
    sites = Site.query.order_by(Site.site_name.asc()).all()
    roles = Role.query.order_by(Role.role_name.asc()).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template(
        'users.html',
        users=users,
        pagination=pagination,
        per_page=per_page,
        total=total,
        current_path=current_path,
        current_page_name=current_page_name,
        sites=sites,
        roles=roles,
        search=search,
        site_filter=site_filter,
        role_filter=role_filter
    )


# ****************** Add User Page *******************************
@routes_blueprint.route('/add_user', methods=['GET', 'POST'])
@login_required
def add_user():
    is_admin()  # Ensure only admins can access this route
    # Mapping paths to page names
    page_names = {'/add_user': 'Add User'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    form = UserForm()
    form.role_id.choices = [(role.id, role.role_name) for role in Role.query.all()]
    form.site_id.choices = [(site.id, site.site_name) for site in Site.query.all()]
    if form.validate_on_submit():
        # Check if a user with the same email already exists
        _key = current_app.config['SECRET_KEY']
        existing_user = User.query.filter_by(email_hash=hash_email(form.email.data, _key)).first()
        if existing_user:
            flash('A user with this email already exists. Please use a different email.', 'danger')
            return render_template('add_user.html', form=form)
        # Validate password complexity
        password = form.password.data
        is_valid, error_message = validate_password(password)
        if not is_valid:
            flash(error_message, 'danger')
            return render_template('add_user.html', form=form)
        # Proceed with creating the new user
        hashed_password = generate_password_hash(form.password.data)
        new_user = User(
            first_name=form.first_name.data,
            middle_name=form.middle_name.data,
            last_name=form.last_name.data,
            email=form.email.data,
            status=form.status.data,
            rm_num=form.rm_num.data,
            site_id=form.site_id.data,
            role_id=form.role_id.data,
            password=hashed_password
        )
        db.session.add(new_user)
        db.session.commit()
        flash('User added successfully!', 'success')
        return redirect(url_for('routes.users'))
    return render_template('add_user.html', form=form,current_path=current_path,
        current_page_name=current_page_name)




# ****************** Edit User Page *******************************
# ****************** Send Temporary Password (AJAX) *******************************
@routes_blueprint.route('/send_temp_password/<int:user_id>', methods=['POST'])
@login_required
def send_temp_password(user_id):
    if not current_user.is_admin:
        return jsonify({'success': False, 'message': 'Permission denied'}), 403

    user = User.query.get_or_404(user_id)
    temp_password = secrets.token_urlsafe(12)

    try:
        send_temp_password_email(user, temp_password)
    except Exception:
        return jsonify({'success': False, 'message': 'Failed to send email. Check your SMTP configuration.'}), 500

    user.password = generate_password_hash(temp_password)
    user.must_change_password = True
    db.session.commit()

    return jsonify({'success': True, 'message': f'Temporary password sent to {user.email}'})



@routes_blueprint.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    # Ensure only admins and tech roles can access this route
    if not (current_user.is_admin or current_user.is_tech_role):
        abort(403)
    user = User.query.get_or_404(user_id)

    # Tech-role (non-admin) staff may only manage non-admin users at their own site.
    # Without this, any Specialist/Technician could edit an Admin account or a user
    # at another site — see security audit finding C1.
    if not current_user.is_admin and (user.role_id == 1 or user.site_id != current_user.site_id):
        abort(403)

    form = UserForm(obj=user)
    # Populate dynamic choices for role_id and site_id — tech-role staff never get
    # the Admin role or other sites as options, and the choice is re-validated
    # below regardless of what the client actually submits.
    if current_user.is_admin:
        form.role_id.choices = [(role.id, role.role_name) for role in Role.query.all()]
        form.site_id.choices = [(site.id, site.site_name) for site in Site.query.all()]
    else:
        form.role_id.choices = [(role.id, role.role_name) for role in Role.query.filter(Role.id != 1)]
        form.site_id.choices = [(current_user.site_id, current_user.site.site_name)]

    if form.validate_on_submit():
        # Defense in depth: never trust the coerced form value alone.
        if not current_user.is_admin and (form.role_id.data == 1 or form.site_id.data != current_user.site_id):
            abort(403)
        # Check if a user with the same email already exists
        _key = current_app.config['SECRET_KEY']
        existing_user = User.query.filter(
            User.email_hash == hash_email(form.email.data, _key),
            User.id != user.id
        ).first()
        if existing_user:
            flash('A user with this email already exists. Please use a different email.', 'danger')
            return render_template('edit_user.html', form=form, user=user)
        # Track changes to avoid unnecessary updates
        changes_made = False
        # Update user details only if there are changes
        if user.first_name != form.first_name.data:
            user.first_name = form.first_name.data
            changes_made = True
        if user.middle_name != form.middle_name.data:
            user.middle_name = form.middle_name.data
            changes_made = True
        if user.last_name != form.last_name.data:
            user.last_name = form.last_name.data
            changes_made = True
        if user.email != form.email.data:
            user.email = form.email.data
            changes_made = True
        if user.status != form.status.data:
            user.status = form.status.data
            changes_made = True
        if user.rm_num != form.rm_num.data:
            user.rm_num = form.rm_num.data
            changes_made = True
        if user.site_id != form.site_id.data:
            user.site_id = form.site_id.data
            changes_made = True
        if user.role_id != form.role_id.data:
            user.role_id = form.role_id.data
            changes_made = True
        # Validate and update password only if provided
        password_changed = False
        if form.password.data:
            password = form.password.data
            is_valid, error_message = validate_password(password)
            if not is_valid:
                flash(error_message, 'danger')
                return render_template('edit_user.html', form=form, user=user)
            user.password = generate_password_hash(password)
            user.must_change_password = False
            changes_made = True
            password_changed = True
        # Commit changes only if any were made
        if changes_made:
            db.session.commit()
            if password_changed:
                send_password_updated_email(user)
            flash('User updated successfully!', 'success')
            return redirect(url_for('routes.users'))
        else:
            flash('No changes were made.', 'info')
    return render_template('edit_user.html', form=form, user=user)



# ****************** Delete User Page *******************************
@routes_blueprint.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    is_admin()  # Ensure only admins can access this route
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You can't delete your own account while logged in.", 'danger')
        return redirect(url_for('routes.users'))

    if user.role_id == 1 and User.query.filter_by(role_id=1).count() <= 1:
        flash('Cannot delete the last remaining Admin account.', 'danger')
        return redirect(url_for('routes.users'))

    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully!', 'warning')
    return redirect(url_for('routes.users'))



SITE_REQUIRED = ['site_name', 'site_acronyms', 'site_cds', 'site_code', 'site_address', 'site_type']

def _normalize_cds(raw):
    """Convert Excel scientific-notation CDS codes (e.g. '1.23457E+13') to integer strings."""
    raw = raw.strip()
    try:
        return str(int(float(raw)))
    except (ValueError, OverflowError):
        return raw


def _process_sites_rows(rows):
    """Upsert sites from a list of CSV dicts. Returns (added, updated). Raises ValueError on bad data."""
    added = updated = 0

    # Validate all rows first (no DB interaction)
    for i, row in enumerate(rows, start=2):
        missing = [f for f in SITE_REQUIRED if not row.get(f, '').strip()]
        if missing:
            raise ValueError(f'Row {i} is missing required fields: {", ".join(missing)}')

    # Pre-fetch all matching sites in one query to avoid mid-loop auto-flush
    names = [row['site_name'].strip() for row in rows]
    site_cache = {s.site_name: s for s in Site.query.filter(Site.site_name.in_(names)).all()}

    for row in rows:
        name = row['site_name'].strip()
        cds  = _normalize_cds(row['site_cds'])
        site = site_cache.get(name)
        if site:
            site.site_acronyms = row['site_acronyms'].strip()
            site.site_cds      = cds
            site.site_code     = row['site_code'].strip()
            site.site_address  = row['site_address'].strip()
            site.site_type     = row['site_type'].strip()
            updated += 1
        else:
            new_site = Site(
                site_name     = name,
                site_acronyms = row['site_acronyms'].strip(),
                site_cds      = cds,
                site_code     = row['site_code'].strip(),
                site_address  = row['site_address'].strip(),
                site_type     = row['site_type'].strip(),
            )
            db.session.add(new_site)
            site_cache[name] = new_site  # prevent duplicate inserts if name appears twice in CSV
            added += 1
    return added, updated


# ****************** Upload Users Page *******************************
@routes_blueprint.route('/bulk-data-upload', methods=['GET'])
@login_required
def upload_users():
    is_admin()
    log_page  = request.args.get('log_page', 1, type=int)
    per_page  = 10
    user_logs = BulkUploadLog.query.order_by(
        BulkUploadLog.uploaded_at.desc()
    ).paginate(page=log_page, per_page=per_page, error_out=False)
    org  = db.session.get(Organization, 1)
    ftp_host_plain     = ''
    ftp_username_plain = ''
    schedule_time = ''
    if org:
        key = current_app.config['SECRET_KEY']
        ftp_host_plain     = decrypt_mail_password(org.ftp_host_enc or '', key)
        ftp_username_plain = decrypt_mail_password(org.ftp_username_enc or '', key)
        if org.ftp_schedule_hour is not None:
            schedule_time = f"{org.ftp_schedule_hour:02d}:{org.ftp_schedule_minute or 0:02d}"
    return render_template('bulk_upload_data.html',
                           user_logs=user_logs,
                           org=org,
                           ftp_host_plain=ftp_host_plain,
                           ftp_username_plain=ftp_username_plain,
                           ftp_schedule_time=schedule_time,
                           current_page_name='Bulk Data Upload')


# ****************** Import Bulk Users *******************************
@routes_blueprint.route('/bulk-upload-users', methods=['POST'])
@login_required
def bulk_upload_users():
    is_admin()

    files = request.files.getlist('csvFile')
    files = [f for f in files if f and f.filename]
    if not files:
        flash('No file selected.', 'danger')
        return redirect(url_for('routes.upload_users'))

    for f in files:
        if not f.filename.lower().endswith('.csv'):
            flash(f'Invalid file: {f.filename}. Only .csv files are accepted.', 'danger')
            return redirect(url_for('routes.upload_users'))

    # Process sites.csv before users.csv
    files.sort(key=lambda f: (0 if f.filename.lower() == 'sites.csv' else 1))

    flash_messages = []

    for file in files:
        filename = secure_filename(file.filename)
        is_sites = filename.lower() == 'sites.csv'
        added = updated = total = 0

        try:
            stream = file.stream.read().decode('UTF-8')
            rows = list(csv.DictReader(stream.splitlines()))
            total = len(rows)

            if is_sites:
                added, updated = _process_sites_rows(rows)
                db.session.commit()
                db.session.add(BulkUploadLog(
                    filename=f'[Sites] {filename}',
                    uploaded_by_id=current_user.id,
                    total_records=total,
                    users_added=added,
                    users_updated=updated,
                    status='success'
                ))
                db.session.commit()
                flash_messages.append(f'Sites: {added} added, {updated} updated.')
            else:
                # Build site lookup cache and validate all rows
                csv_emails = set()
                site_cache = {}
                valid_role_ids = {r.id for r in Role.query.with_entities(Role.id).all()}
                for row in rows:
                    if not all([row.get('first_name'), row.get('last_name'), row.get('email'),
                                row.get('role_id'), row.get('site_name'), row.get('rm_num')]):
                        raise ValueError('Some rows in the CSV file are missing required fields.')
                    raw_role_id = int(row['role_id'])
                    if raw_role_id not in valid_role_ids:
                        raise ValueError(f"Row for '{row.get('email')}' has invalid role_id {raw_role_id!r}.")
                    name = row['site_name'].strip()
                    if name not in site_cache:
                        site = Site.query.filter_by(site_name=name).first()
                        if not site:
                            raise ValueError(f"Site '{name}' not found. Please verify the CSV file.")
                        site_cache[name] = site.id
                    csv_emails.add(row['email'].strip().lower())

                # Upsert users
                _bulk_key = current_app.config['SECRET_KEY']
                for row in rows:
                    site_id = site_cache[row['site_name'].strip()]
                    raw_role_id = int(row['role_id'])
                    existing_user = User.query.filter_by(email_hash=hash_email(row['email'].strip(), _bulk_key)).first()
                    if existing_user:
                        existing_user.first_name  = row['first_name']
                        existing_user.middle_name = row.get('middle_name') or None
                        existing_user.last_name   = row['last_name']
                        existing_user.rm_num      = row.get('rm_num') or existing_user.rm_num
                        existing_user.role_id     = raw_role_id
                        existing_user.site_id     = site_id
                        existing_user.status      = row.get('status') or 'Active'
                        updated += 1
                    else:
                        db.session.add(User(
                            first_name=row['first_name'],
                            middle_name=row.get('middle_name') or None,
                            last_name=row['last_name'],
                            email=row['email'].strip(),
                            status=row.get('status') or 'Active',
                            password=generate_password_hash(secrets.token_urlsafe(16)),
                            must_change_password=True,
                            rm_num=row.get('rm_num') or None,
                            role_id=raw_role_id,
                            site_id=site_id
                        ))
                        added += 1

                # Flush pending inserts/updates, then deactivate absent users.
                # Admin accounts (role_id=1) are excluded to prevent accidental lockout.
                db.session.flush()
                csv_email_hashes = {hash_email(e, _bulk_key) for e in csv_emails}
                deactivated = User.query.filter(
                    User.status == 'Active',
                    User.role_id != 1,
                    ~User.email_hash.in_(csv_email_hashes)
                ).update({'status': 'Inactive'}, synchronize_session=False)

                db.session.commit()
                db.session.add(BulkUploadLog(
                    filename=filename,
                    uploaded_by_id=current_user.id,
                    total_records=total,
                    users_added=added,
                    users_updated=updated,
                    status='success'
                ))
                db.session.commit()
                msg = f'Users: {added} added, {updated} updated.'
                if deactivated:
                    msg += f' {deactivated} marked Inactive (not in file).'
                flash_messages.append(msg)

        except ValueError as e:
            db.session.rollback()
            db.session.add(BulkUploadLog(
                filename=f'[Sites] {filename}' if is_sites else filename,
                uploaded_by_id=current_user.id,
                total_records=total,
                users_added=added,
                users_updated=updated,
                status='error',
                error_message=str(e)
            ))
            db.session.commit()
            flash(f'Error processing {filename}: {e}', 'danger')
            return redirect(url_for('routes.upload_users'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Bulk upload failed for {filename}: {e}", exc_info=True)
            db.session.add(BulkUploadLog(
                filename=f'[Sites] {filename}' if is_sites else filename,
                uploaded_by_id=current_user.id,
                total_records=total,
                users_added=added,
                users_updated=updated,
                status='error',
                error_message=str(e)
            ))
            db.session.commit()
            flash(f'An unexpected error occurred while processing {filename}.', 'danger')
            return redirect(url_for('routes.upload_users'))

    if flash_messages:
        flash(' | '.join(flash_messages), 'success')

    return redirect(url_for('routes.upload_users'))


# ****************** FTP Bulk Upload Users *******************************
@routes_blueprint.route('/ftp-settings/save', methods=['POST'])
@login_required
def ftp_save_settings():
    """Save FTP credentials and schedule settings into the Organization record."""
    is_admin()
    org = Organization.query.get_or_404(1)
    key = current_app.config['SECRET_KEY']

    # --- Credentials ---
    raw_host = re.sub(r'^ftps?://', '', request.form.get('ftp_host', '').strip(), flags=re.IGNORECASE)
    username = request.form.get('ftp_username', '').strip()
    password = request.form.get('ftp_password', '').strip()
    if raw_host:
        org.ftp_host_enc = encrypt_mail_password(raw_host, key)
    if username:
        org.ftp_username_enc = encrypt_mail_password(username, key)
    if password:
        org.ftp_password_enc = encrypt_mail_password(password, key)
    org.ftp_port    = int(request.form.get('ftp_port') or 21)
    org.ftp_path    = request.form.get('ftp_path', '').strip() or None
    org.ftp_use_tls = request.form.get('ftp_use_tls') == 'on'

    # --- Schedule ---
    schedule_enabled = request.form.get('ftp_schedule_enabled') == 'on'
    org.ftp_schedule_enabled = schedule_enabled
    if schedule_enabled:
        schedule_time = (request.form.get('ftp_schedule_time') or '00:00').strip()
        try:
            hour, minute = map(int, schedule_time.split(':'))
        except ValueError:
            hour, minute = 0, 0
        days_list = request.form.getlist('ftp_schedule_days')
        all_days  = {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
        org.ftp_schedule_hour   = hour
        org.ftp_schedule_minute = minute
        org.ftp_schedule_days   = '*' if not days_list or set(days_list) >= all_days else ','.join(days_list)

    from datetime import date as _date
    for attr, field in [('ftp_schedule_start_date', 'ftp_schedule_start_date'),
                        ('ftp_schedule_stop_date',  'ftp_schedule_stop_date')]:
        raw = request.form.get(field, '').strip()
        try:
            setattr(org, attr, _date.fromisoformat(raw) if raw else None)
        except ValueError:
            setattr(org, attr, None)

    db.session.add(org)
    db.session.commit()

    # Sync APScheduler job (non-fatal if scheduler unavailable)
    try:
        from application.scheduled_jobs import run_org_ftp_schedule
        if schedule_enabled and org.ftp_schedule_hour is not None:
            scheduler.add_job(
                id='org_ftp_schedule',
                func=run_org_ftp_schedule,
                trigger='cron',
                day_of_week=org.ftp_schedule_days,
                hour=org.ftp_schedule_hour,
                minute=org.ftp_schedule_minute,
                replace_existing=True
            )
        else:
            try:
                scheduler.remove_job('org_ftp_schedule')
            except Exception:
                pass
    except Exception:
        pass

    if schedule_enabled:
        flash('FTP settings and schedule saved.', 'success')
    else:
        flash('FTP settings saved. Schedule disabled.', 'success')

    if not org.ftp_use_tls:
        flash(
            'Warning: FTPS ("Use TLS") is disabled. Plain FTP transmits credentials '
            'and student/staff data in cleartext over the network. Enable "Use TLS" '
            'unless this connection is on a fully trusted private network.',
            'warning'
        )

    return redirect(url_for('routes.upload_users') + '?tab=ftp')


@routes_blueprint.route('/ftp-upload-users', methods=['POST'])
@login_required
def ftp_bulk_upload_users():
    is_admin()

    ftp_host     = re.sub(r'^ftps?://', '', request.form.get('ftp_host', '').strip(), flags=re.IGNORECASE)
    ftp_port     = request.form.get('ftp_port', '21').strip()
    ftp_username = request.form.get('ftp_username', '').strip()
    ftp_path     = request.form.get('ftp_path', '').strip()
    use_tls      = request.form.get('ftp_use_tls') == 'on'
    ftp_password = request.form.get('ftp_password', '').strip()

    # Fall back to saved org credentials (decrypt) if form fields are blank
    org = db.session.get(Organization, 1)
    if org:
        key = current_app.config['SECRET_KEY']
        if not ftp_host and org.ftp_host_enc:
            ftp_host = decrypt_mail_password(org.ftp_host_enc, key)
        if not ftp_username and org.ftp_username_enc:
            ftp_username = decrypt_mail_password(org.ftp_username_enc, key)
        if not ftp_password and org.ftp_password_enc:
            ftp_password = decrypt_mail_password(org.ftp_password_enc, key)
        ftp_path = ftp_path or (org.ftp_path or '')
        ftp_port = ftp_port or str(org.ftp_port or 21)
        use_tls  = use_tls  or bool(org.ftp_use_tls)

    if not all([ftp_host, ftp_username, ftp_path]):
        flash('FTP host, username, and remote directory are required.', 'danger')
        return redirect(url_for('routes.upload_users') + '?tab=ftp')

    try:
        port = int(ftp_port)
    except ValueError:
        flash('FTP port must be a valid number.', 'danger')
        return redirect(url_for('routes.upload_users') + '?tab=ftp')

    # Normalise: if the stored path still has a .csv filename (old format), strip it
    if ftp_path.lower().endswith('.csv'):
        import posixpath as _pp
        ftp_path = _pp.dirname(ftp_path)
    ftp_dir = ftp_path.rstrip('/')
    users_path = f'{ftp_dir}/users.csv'
    sites_path = f'{ftp_dir}/sites.csv'

    users_added = users_updated = total_records = 0
    sites_added = sites_updated = sites_total = 0

    try:
        ftp = ftplib.FTP_TLS() if use_tls else ftplib.FTP()
        ftp.connect(ftp_host, port, timeout=30)
        ftp.login(ftp_username, ftp_password)
        if use_tls:
            ftp.prot_p()

        # --- Download and process sites.csv first ---
        sites_buf = io.BytesIO()
        try:
            ftp.retrbinary(f'RETR {sites_path}', sites_buf.write)
            sites_buf.seek(0)
            site_rows   = list(csv.DictReader(sites_buf.read().decode('utf-8').splitlines()))
            sites_total = len(site_rows)
            sites_added, sites_updated = _process_sites_rows(site_rows)
            db.session.commit()
            db.session.add(BulkUploadLog(
                filename='[FTP Sites] sites.csv',
                uploaded_by_id=current_user.id,
                total_records=sites_total,
                users_added=sites_added,
                users_updated=sites_updated,
                status='success'
            ))
            db.session.commit()
        except ftplib.error_perm:
            pass  # sites.csv not found on server — skip silently

        # --- Download and process users.csv ---
        user_buf = io.BytesIO()
        ftp.retrbinary(f'RETR {users_path}', user_buf.write)
        ftp.quit()

        user_buf.seek(0)
        rows = list(csv.DictReader(user_buf.read().decode('UTF-8').splitlines()))
        total_records = len(rows)

        # First pass: validate all rows and collect emails
        csv_emails = set()
        valid_role_ids = {r.id for r in Role.query.with_entities(Role.id).all()}
        for row in rows:
            if not all([row.get('first_name'), row.get('last_name'), row.get('email'),
                        row.get('role_id'), row.get('site_name'), row.get('rm_num')]):
                raise ValueError('Some rows in the CSV file are missing required fields.')
            raw_role_id = int(row['role_id'])
            if raw_role_id not in valid_role_ids:
                raise ValueError(f"Row for '{row.get('email')}' has invalid role_id {raw_role_id!r}.")
            site = Site.query.filter_by(site_name=row['site_name']).first()
            if not site:
                raise ValueError(f"Site '{row['site_name']}' not found. Please verify the CSV file.")
            csv_emails.add(row['email'].strip().lower())

        # Second pass: upsert users
        for row in rows:
            raw_role_id = int(row['role_id'])
            site = Site.query.filter_by(site_name=row['site_name']).first()
            existing_user = User.query.filter_by(email_hash=hash_email(row['email'].strip(), key)).first()
            if existing_user:
                existing_user.first_name  = row['first_name']
                existing_user.middle_name = row.get('middle_name') or None
                existing_user.last_name   = row['last_name']
                existing_user.rm_num      = row.get('rm_num') or existing_user.rm_num
                existing_user.role_id     = raw_role_id
                existing_user.site_id     = site.id
                existing_user.status      = row.get('status') or 'Active'
                users_updated += 1
            else:
                db.session.add(User(
                    first_name=row['first_name'],
                    middle_name=row.get('middle_name', None),
                    last_name=row['last_name'],
                    email=row['email'].strip(),
                    status=row.get('status', 'Active'),
                    password=generate_password_hash(secrets.token_urlsafe(16)),
                    must_change_password=True,
                    rm_num=row.get('rm_num', None),
                    role_id=raw_role_id,
                    site_id=site.id
                ))
                users_added += 1

        # Third pass: deactivate users absent from the CSV.
        # Admin accounts (role_id=1) are excluded to prevent accidental lockout.
        users_deactivated = 0
        ftp_csv_hashes = {hash_email(e, key) for e in csv_emails}
        for user in User.query.filter(User.status == 'Active', User.role_id != 1).all():
            if user.email_hash not in ftp_csv_hashes:
                user.status = 'Inactive'
                users_deactivated += 1

        db.session.commit()

        db.session.add(BulkUploadLog(
            filename='[FTP] users.csv',
            uploaded_by_id=current_user.id,
            total_records=total_records,
            users_added=users_added,
            users_updated=users_updated,
            status='success'
        ))
        db.session.commit()

        msg = f'FTP import successful: {users_added} users added, {users_updated} updated.'
        if users_deactivated:
            msg += f' {users_deactivated} marked Inactive (not in file).'
        if sites_total:
            msg += f' Sites: {sites_added} added, {sites_updated} updated.'
        flash(msg, 'success')

    except (ftplib.Error, OSError, EOFError, UnicodeDecodeError, ValueError) as e:
        db.session.rollback()
        if isinstance(e, socket.gaierror):
            friendly = f"Cannot reach FTP host '{ftp_host}'. Check that the hostname is correct and the server is reachable."
        elif isinstance(e, ConnectionRefusedError):
            friendly = f"Connection refused by '{ftp_host}:{port}'. Check the port number and that the FTP service is running."
        elif isinstance(e, TimeoutError):
            friendly = f"Connection to '{ftp_host}' timed out. The server may be down or blocked by a firewall."
        elif isinstance(e, ftplib.error_perm):
            msg_lower = str(e)
            if any(code in msg_lower for code in ('530', '331', '332')):
                friendly = 'FTP login failed. Check your username and password.'
            else:
                friendly = f'FTP error: {e}'
        else:
            friendly = str(e)
        try:
            db.session.add(BulkUploadLog(
                filename='[FTP] users.csv',
                uploaded_by_id=current_user.id,
                total_records=total_records,
                users_added=users_added,
                users_updated=users_updated,
                status='error',
                error_message=friendly
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(friendly, 'danger')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'FTP bulk upload unexpected error: {e}', exc_info=True)
        flash('An unexpected error occurred during the FTP import.', 'danger')

    return redirect(url_for('routes.upload_users'))


# ****************** Bulk Upload Sites (CSV) *******************************
@routes_blueprint.route('/bulk-upload-sites', methods=['POST'])
@login_required
def bulk_upload_sites():
    is_admin()

    if 'csvFile' not in request.files:
        flash('No file selected.', 'danger')
        return redirect(url_for('routes.upload_users') + '?tab=sites')

    file = request.files['csvFile']
    if not file or file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('routes.upload_users') + '?tab=sites')

    if not file.filename.lower().endswith('.csv'):
        flash('Invalid file format. Please upload a CSV file.', 'danger')
        return redirect(url_for('routes.upload_users') + '?tab=sites')

    sites_added = sites_updated = total_records = 0
    filename = secure_filename(file.filename)

    try:
        stream = file.read().decode('utf-8')
        rows = list(csv.DictReader(stream.splitlines()))
        total_records = len(rows)
        if total_records == 0:
            flash('The CSV file is empty.', 'warning')
            return redirect(url_for('routes.upload_users') + '?tab=sites')

        sites_added, sites_updated = _process_sites_rows(rows)
        db.session.commit()

        db.session.add(BulkUploadLog(
            filename=f'[Sites] {filename}',
            uploaded_by_id=current_user.id,
            total_records=total_records,
            users_added=sites_added,
            users_updated=sites_updated,
            status='success'
        ))
        db.session.commit()
        flash(f'Sites import successful: {sites_added} added, {sites_updated} updated.', 'success')

    except UnicodeDecodeError as e:
        db.session.rollback()
        current_app.logger.error(f"Sites CSV encoding error for {filename}: {e}", exc_info=True)
        db.session.add(BulkUploadLog(
            filename=f'[Sites] {filename}',
            uploaded_by_id=current_user.id,
            total_records=total_records,
            users_added=sites_added,
            users_updated=sites_updated,
            status='error',
            error_message=str(e)
        ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash('Sites import failed: file encoding not supported. Please save the CSV as UTF-8.', 'danger')

    except ValueError as e:
        db.session.rollback()
        db.session.add(BulkUploadLog(
            filename=f'[Sites] {filename}',
            uploaded_by_id=current_user.id,
            total_records=total_records,
            users_added=sites_added,
            users_updated=sites_updated,
            status='error',
            error_message=str(e)
        ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(f'Sites import failed: {e}', 'danger')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Bulk upload sites unexpected error: {e}', exc_info=True)
        flash('An unexpected error occurred during the sites import.', 'danger')

    return redirect(url_for('routes.upload_users') + '?tab=sites')


# *********************************************************************
# ****************** Role Management Page *******************************
@routes_blueprint.route('/roles')
@login_required
def roles():
        # Mapping paths to page names
    page_names = {'/roles': 'Manage User Roles'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    # Get the page number and per_page from the query parameters, default to 10 for per_page
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    # Query the users
    total = Role.query.count()
    roles = Role.query.order_by(Role.id.asc()).offset(offset).limit(per_page).all()    
    # Set up pagination with Bootstrap 5 styling
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template('roles.html', roles=roles, pagination=pagination, per_page=per_page, total=total, 
        current_path=current_path, 
        current_page_name=current_page_name
    )

# ****************** Add New Role Page *******************************
@routes_blueprint.route('/add_role', methods=['GET', 'POST'])
@login_required
def add_role():
            # Mapping paths to page names
    page_names = {'/add_role': 'New Role'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    form = RoleForm()
    if form.validate_on_submit():
        # Check if a role with the same name already exists
        existing_role = Role.query.filter_by(role_name=form.role_name.data).first()
        if existing_role:
            flash('This role already exists.', 'danger')
            return render_template('add_role.html', form=form)  # Re-render form with the error message
        # Create and add the new role
        new_role = Role(
            role_name=form.role_name.data
        )
        db.session.add(new_role)
        db.session.commit()
        flash('Role added successfully!', 'success')
        return redirect(url_for('routes.roles'))
    return render_template('add_role.html', form=form,
        current_path=current_path, 
        current_page_name=current_page_name)

# ****************** Edit Role Page *******************************
@routes_blueprint.route('/edit_role/<int:role_id>', methods=['GET', 'POST'])
@login_required
def edit_role(role_id):
    is_admin()  # Ensure only admins can access this route
    
    # Restrict editing roles with IDs 1, 2, 3, 4, 5
    if role_id in {1, 2, 3, 4, 5}:
        flash('You are not allowed to edit this role.', 'danger')
        return redirect(url_for('routes.roles'))

    role = Role.query.get_or_404(role_id)
    form = RoleForm(obj=role)
    if form.validate_on_submit():
        # Check for duplicate entries
        existing_role = Role.query.filter(Role.role_name == form.role_name.data, Role.id != role.id).first()
        if existing_role:
            flash('This role already exists.', 'danger')
            return render_template('add_role.html', form=form)  # Re-render form with the error message
        # Check if there are any changes to the form
        if (
            role.role_name == form.role_name.data
        ):
            flash('No changes were made.', 'info')
            return render_template('edit_role.html', form=form, role=role)
        role.role_name = form.role_name.data
        db.session.commit()
        flash('Role updated successfully!', 'success')
        return redirect(url_for('routes.roles'))
    return render_template('edit_role.html', form=form, role=role)


# ****************** Delete Role Page *******************************
@routes_blueprint.route('/delete_role/<int:role_id>', methods=['POST'])
@login_required
def delete_role(role_id):
    is_admin()  # Ensure only admins can access this route

    # Restrict deleting roles with IDs 1, 2, 3, 4, 5
    if role_id in {1, 2, 3, 4, 5}:
        flash('You are not allowed to delete this role.', 'danger')
        return redirect(url_for('routes.roles'))
    
    role = Role.query.get_or_404(role_id)
    db.session.delete(role)
    db.session.commit()
    flash('Role deleted successfully!', 'warning')
    return redirect(url_for('routes.roles'))


# *********************************************************************
# ****************** Site Management Page *******************************
@routes_blueprint.route('/sites', methods=['GET'])
@login_required
def sites():
        # Mapping paths to page names
    page_names = {'/sites': 'Manage Sites'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    # Get the page number and per_page from the query parameters, default to 10 for per_page
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    # Query the users
    total = Site.query.count()
    sites = Site.query.order_by(Site.id.asc()).offset(offset).limit(per_page).all()
    # Set up pagination with Bootstrap 5 styling
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template('sites.html', sites=sites, pagination=pagination, per_page=per_page, total=total, 
        current_path=current_path, 
        current_page_name=current_page_name
    )

# ****************** Add New Site Page *******************************
@routes_blueprint.route('/add_site', methods=['GET', 'POST'])
@login_required
def add_site():
            # Mapping paths to page names
    page_names = {'/add_site': 'New Site'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    form = SiteForm()
    if form.validate_on_submit():
        # Check if a role with the same name already exists
        existing_site = Site.query.filter_by(site_cds=form.site_cds.data).first()
        if existing_site:
            flash('This site already exists.', 'danger')
            return render_template('add_site.html', form=form)  # Re-render form with the error message
        new_site = Site(
            site_name=form.site_name.data,
            site_acronyms=form.site_acronyms.data,
            site_code=form.site_code.data,
            site_cds=form.site_cds.data,
            site_address=form.site_address.data,
            site_type=form.site_type.data 
        )
        db.session.add(new_site)
        db.session.commit()
        flash('Site added successfully!', 'success')
        return redirect(url_for('routes.sites'))
    # Pass None for site to differentiate between add and edit
    return render_template('add_site.html', form=form,
        current_path=current_path, 
        current_page_name=current_page_name
    )


# ****************** Edit Site Page *******************************
@routes_blueprint.route('/edit_site/<int:site_id>', methods=['GET', 'POST'])
@login_required
def edit_site(site_id):
    is_admin()  # Ensure only admins can access this route
    site = Site.query.get_or_404(site_id)
    form = SiteForm(obj=site)
    if form.validate_on_submit():
        # Check if a role with the same name already exists
        existing_site = Site.query.filter(Site.site_cds == form.site_cds.data, Site.id != site.id).first()
        if existing_site:
            flash('This site already exists.', 'danger')
            return render_template('add_site.html', form=form)  # Re-render form with the error message
        # Check if there are any changes to the form
        if (
            site.site_name == form.site_name.data and
            site.site_acronyms == form.site_acronyms.data and
            site.site_code == form.site_code.data and
            site.site_cds == form.site_cds.data and
            site.site_address == form.site_address.data and
            site.site_type == form.site_type.data
        ):
            flash('No changes were made.', 'info')
            return render_template('edit_site.html', form=form, site=site)
        site.site_name = form.site_name.data
        site.site_acronyms = form.site_acronyms.data
        site.site_code = form.site_code.data
        site.site_cds = form.site_cds.data
        site.site_address = form.site_address.data
        site.site_type = form.site_type.data
        db.session.commit()
        flash('Site updated successfully!', 'success')
        return redirect(url_for('routes.sites'))
    return render_template('edit_site.html', form=form, site=site)

# ****************** Delete Site Page *******************************
@routes_blueprint.route('/delete_site/<int:site_id>', methods=['POST'])
@login_required
def delete_site(site_id):
    is_admin()  # Ensure only admins can access this route
    site = Site.query.get_or_404(site_id)
    db.session.delete(site)
    db.session.commit()
    flash('Site deleted successfully!', 'warning')
    return redirect(url_for('routes.sites'))


# *********************************************************************
# ****************** Notification Management Page *********************
@routes_blueprint.route('/notifications', methods=['GET'])
@login_required
def notifications():
        # Mapping paths to page names
    page_names = {'/notifications': 'Manage Notifications'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    # Get the page number and per_page from the query parameters, default to 10 for per_page
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    # Query the users
    total = Notification.query.count()
    notifications = Notification.query.offset(offset).limit(per_page).all()
    # Set up pagination with Bootstrap 5 styling
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template('notifications.html', notifications=notifications, pagination=pagination, per_page=per_page, total=total, 
        current_path=current_path, 
        current_page_name=current_page_name
    )

# ****************** Add New Notification *********************
@routes_blueprint.route('/add_notification', methods=['GET', 'POST'])
@login_required
def add_notification():
    page_names = {'/add_notification': 'New Notification'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    form = NotificationForm()
    if form.validate_on_submit():
        # Check if a notification with the same name already exists
        existing_notification = Notification.query.filter_by(msg_name=form.msg_name.data).first()
        if existing_notification:
            flash('This notification name already exists.', 'danger')
            return render_template('add_notification.html', form=form)  # Re-render form with the error message
        new_notification = Notification(
            msg_name=form.msg_name.data,
            msg_content=form.msg_content.data,
            msg_status="Inactive"
        )
        db.session.add(new_notification)
        db.session.commit()
        flash('Notification added successfully!', 'success')
        return redirect(url_for('routes.notifications'))
    # Pass None for notification to differentiate between add and edit
    return render_template('add_notification.html', form=form,
        current_path=current_path, 
        current_page_name=current_page_name
    )


# ****************** Edit Notification Page *********************
@routes_blueprint.route('/edit_notification/<int:notification_id>', methods=['GET', 'POST'])
@login_required
def edit_notification(notification_id):
    is_admin()  # Ensure only admins can access this route
    notification = Notification.query.get_or_404(notification_id)
    form = NotificationForm(obj=notification)

    if request.method == 'POST':
        # Capture original values before any mutation
        orig_name    = notification.msg_name
        orig_content = notification.msg_content
        orig_status  = notification.msg_status

        # Determine new status from checkbox
        new_status = 'Active' if request.form.get('msg_status') else 'Inactive'

        # Check for duplicate notification name
        existing_notification = Notification.query.filter(
            Notification.msg_name == form.msg_name.data,
            Notification.id != notification.id
        ).first()
        if existing_notification:
            flash('This notification name already exists.', 'danger')
            return render_template('edit_notification.html', form=form, notification=notification)

        # Check if no changes were made
        if (
            orig_name    == form.msg_name.data and
            orig_content == form.msg_content.data and
            orig_status  == new_status
        ):
            flash('No changes were made.', 'info')
            return render_template('edit_notification.html', form=form, notification=notification)

        # Enforce only one active notification
        if new_status == 'Active':
            active_notification = Notification.query.filter_by(msg_status='Active').first()
            if active_notification and active_notification.id != notification.id:
                flash('Only one notification can be active at a time. Please deactivate the current notification before activating a new one. ', 'danger')
                return render_template('edit_notification.html', form=form, notification=notification)

        # Update and save changes
        notification.msg_name    = form.msg_name.data
        notification.msg_content = form.msg_content.data
        notification.msg_status  = new_status
        db.session.commit()
        flash('Notification updated successfully!', 'success')
        return redirect(url_for('routes.notifications'))

    return render_template('edit_notification.html', form=form, notification=notification)



# ****************** Toggle Notification Status *********************
@routes_blueprint.route('/toggle_notification/<int:notification_id>', methods=['POST'])
@login_required
def toggle_notification(notification_id):
    is_admin()
    notification = Notification.query.get_or_404(notification_id)
    if notification.msg_status == 'Active':
        notification.msg_status = 'Inactive'
    else:
        # Deactivate all others first, then activate this one
        Notification.query.filter(Notification.id != notification_id).update({'msg_status': 'Inactive'})
        notification.msg_status = 'Active'
    db.session.commit()
    return redirect(url_for('routes.notifications'))


# ****************** Delete Notification Page *********************
@routes_blueprint.route('/delete_notification/<int:notification_id>', methods=['POST'])
@login_required
def delete_notification(notification_id):
    is_admin()  # Ensure only admins can access this route
    notification = Notification.query.get_or_404(notification_id)
    db.session.delete(notification)
    db.session.commit()
    flash('Notification deleted successfully!', 'warning')
    return redirect(url_for('routes.notifications'))




# *********************************************************************
# ****************** Tickets Management Page *******************************
@routes_blueprint.route('/tickets', methods=['GET'])
@login_required
def tickets():
    # Mapping paths to page names
    page_names = {'/tickets': 'Manage Tickets'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    # Get query parameters
    site_filter = request.args.get('site_filter', '')
    status_filter = request.args.get('status_filter', '').strip()
    assigned_user_filter = request.args.get('assigned_user_filter', '')
    category_filter = request.args.get('category_filter', '')

    # Fetch the current user's role and site information
    current_user_role_id = current_user.role_id  
    current_user_site_id = current_user.site_id  

    # Start the query with explicit joins
    query = Ticket.query.join(User, Ticket.user_id == User.id).join(Site, Site.id == User.site_id)

    # Apply role-specific filtering
    if current_user_role_id == 3:
        query = query.filter(Site.id == current_user_site_id)
    elif current_user_role_id not in [1, 2, 3]:
        query = query.filter(Site.id == current_user_site_id, Ticket.user_id == current_user.id)

    # Apply site filter if provided
    if site_filter:
        try:
            query = query.filter(Site.id == int(site_filter))
        except ValueError:
            pass

    # Apply status filter
    if status_filter:
        query = query.filter(Ticket.tck_status == status_filter)
    
    # Apply assigned user filter
    if assigned_user_filter:
        try:
            query = query.filter(Ticket.assigned_to_id == int(assigned_user_filter))
        except ValueError:
            pass

    # Apply category (ticket title) filter
    if category_filter:
        try:
            query = query.filter(Ticket.title_id == int(category_filter))
        except ValueError:
            pass

    # Pagination setup
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()

    # Sorting logic
    order_by_clause = [
        case(
            # Priority 1: Open and escalated (highest priority)
            ((Ticket.tck_status == "1-pending") & (Ticket.escalated == 1), 1),
            # Priority 2: In progress and escalated
            ((Ticket.tck_status == "2-progress") & (Ticket.escalated == 1), 2),
            # Priority 3: Open and not escalated
            ((Ticket.tck_status == "1-pending") & (Ticket.escalated == 0), 3),
            # Priority 4: In progress and not escalated
            ((Ticket.tck_status == "2-progress") & (Ticket.escalated == 0), 4),
            # Default case
            else_=5
        ),
        Ticket.created_at.desc()  # For tickets with same priority, sort by most recent
    ]

    # Apply ordering
    tickets = query.order_by(*order_by_clause).offset(offset).limit(per_page).all()

    # Fetch sites for the dropdown
    if current_user_role_id in [1, 2]:
        sites = Site.query.order_by(Site.site_name).all()
    else:
        sites = Site.query.filter_by(id=current_user_site_id).order_by(Site.site_name).all()

    # Define status choices
    status_choices = [
        ('1-pending', 'Pending'),
        ('2-progress', 'In Progress'),
        ('3-completed', 'Completed')
    ]

    # Fetch only users with role_id 1 (admin) or 2 (specialist) for assigned user filter - tickets.html
    assigned_users = User.query.filter(User.role_id.in_([1, 2, 3])).order_by(User.first_name).all()

    # Fetch ticket titles for the category filter - tickets.html
    categories = Title.query.order_by(Title.title_name).all()

    # Pagination setup - tickets.html
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template(
        'tickets.html',
        tickets=tickets,
        pagination=pagination,
        per_page=per_page,
        total=total,
        current_path=current_path,
        current_page_name=current_page_name,
        statuses=status_choices,
        sites=sites,
        assigned_users=assigned_users,  # Pass filtered users
        categories=categories
    )







# ****************** Add Ticket Page *******************************
@routes_blueprint.route('/add_ticket', methods=['GET', 'POST'])
@login_required
def add_ticket():
    # Mapping paths to page names
    page_names = {'/add_ticket': 'New Tickets'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    form = TicketForm()
    titles = Title.query.order_by(Title.title_name).all()  # Get all titles sorted by name
    tech_users = User.query.filter(User.role_id.in_([2, 3])).all()
    form.title_id.choices = [(title.id, title.title_name) for title in titles]
    form.assigned_to_id.choices = [(user.id, user.get_full_name()) for user in tech_users]
    
    if form.validate_on_submit():
        # Ensure site_id is set based on the logged-in user's site_id
        site_id = current_user.site_id  # Use current user's site_id directly
        # Find a user with role_id=3 in the same site to auto-assign
        assignee = User.query.filter_by(role_id=3, site_id=site_id).first()
        # Create new ticket
        ticket = Ticket(
            title_id=form.title_id.data,
            tck_status="1-pending",  # Ensure it's always 'Pending'
            assigned_to_id=assignee.id if assignee else None,
            escalated = 0,
            user_id=current_user.id,
            site_id=site_id,  # Assign site_id directly from current_user
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(ticket)
        db.session.flush()
        
    # Handle file upload
        uploaded_file = request.files.get('attachment')
        if uploaded_file and uploaded_file.filename:
            is_valid, error_message = validate_file_upload(uploaded_file)
            if not is_valid:
                flash(error_message, 'error')
                return redirect(request.url)

            file_ext = os.path.splitext(uploaded_file.filename)[1].lower()
            # Generate a unique filename
            new_filename = f"ticket_{ticket.id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}{file_ext}"
            filename = secure_filename(new_filename)
            upload_folder = current_app.config['UPLOAD_ATTACHMENT']
            os.makedirs(upload_folder, exist_ok=True)
            filepath = os.path.join(upload_folder, filename)

            # Save the file to disk
            uploaded_file.save(filepath)

            # Verify the file was saved correctly
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                flash('Failed to save attachment (empty file)', 'error')
                return redirect(request.url)

            # Check if this attachment already exists (prevent duplicates)
            existing_attachment = Ticket_attachment.query.filter_by(
                ticket_id=ticket.id,
                attach_image=filename
            ).first()

            if not existing_attachment:  # Only add if it doesn't exist
                new_attachment = Ticket_attachment(
                    ticket_id=ticket.id,
                    attach_image=filename,
                    uploaded_at=datetime.now(timezone.utc),
                    user_id=current_user.id
                )
                db.session.add(new_attachment)
            else:
                flash('This attachment already exists.', 'warning')

        # Add initial comment (if any)
        initial_comment = request.form.get('initial_comment')
        if initial_comment:
            new_content = Ticket_content(
                ticket_id=ticket.id,
                content=initial_comment,
                cnt_created_at=datetime.now(timezone.utc),
                user_id=current_user.id
            )
            db.session.add(new_content)

        # Commit all changes at once
        db.session.commit()
        send_ticket_notification('created', ticket, initial_comment=initial_comment or '')
        flash('Ticket created successfully!', 'success')
        return redirect(url_for('routes.tickets'))
    
    return render_template('add_ticket.html', form=form, titles=titles, 
        current_path=current_path,
        current_page_name=current_page_name
        )




@routes_blueprint.route('/download_attachment/<int:attachment_id>')
@login_required
def download_attachment(attachment_id):
    attachment = Ticket_attachment.query.get_or_404(attachment_id)
    ticket = Ticket.query.get_or_404(attachment.ticket_id)

    if not can_access_ticket(ticket):
        abort(403)

    filename = attachment.attach_image.split('/')[-1]
    upload_folder = current_app.config['UPLOAD_ATTACHMENT']

    file_path = os.path.join(upload_folder, filename)
    if not os.path.exists(file_path):
        current_app.logger.error(f"Attachment not found at: {file_path}")
        flash('File not found.', 'error')
        return redirect(url_for('routes.tickets'))

    return send_from_directory(upload_folder, filename, as_attachment=True)


# ****************** Delete attachment Page *******************************
@routes_blueprint.route('/delete_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_attachment(attachment_id):
    current_app.logger.info(f"Delete attachment request - Attachment ID: {attachment_id}, User: {current_user.id}")
    
    # Find attachment
    attachment = db.session.get(Ticket_attachment, attachment_id)
    if not attachment:
        flash('Attachment not found.', 'danger')
        return redirect(url_for('routes.index'))

    # Get ticket id for redirect
    ticket_id = attachment.ticket_id

    # Check permissions (site-scoped access, or whoever uploaded the attachment)
    ticket = db.session.get(Ticket, ticket_id)
    if not (can_access_ticket(ticket) or current_user.id == attachment.user_id):
        flash('You do not have permission to delete this attachment.', 'danger')
        return redirect(url_for('routes.edit_ticket', ticket_id=ticket_id))
    
    try:
        # Get filename for file deletion
        if '/' in attachment.attach_image:
            filename = attachment.attach_image.split('/')[-1]
        else:
            filename = attachment.attach_image
        
        # Get filepath
        file_path = os.path.join(current_app.config['UPLOAD_ATTACHMENT'], filename)
        current_app.logger.debug(f"Attempting to delete file: {file_path}")
        
        # Delete physical file if it exists
        if os.path.exists(file_path):
            os.remove(file_path)
            current_app.logger.info(f"File deleted successfully: {file_path}")
        
        # Delete database record
        db.session.delete(attachment)
        ticket.updated_at = datetime.now(timezone.utc)  # Update ticket timestamp
        db.session.commit()
        current_app.logger.info(f"Attachment {attachment_id} deleted from database")
        
        flash('Attachment deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting attachment {attachment_id}: {str(e)}")
        flash('Error deleting attachment.', 'danger')
    
    return redirect(url_for('routes.edit_ticket', ticket_id=ticket_id))




# ****************** edit Ticket Page *******************************
@routes_blueprint.route('/edit_ticket/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
def edit_ticket(ticket_id):
    current_path = request.path
    current_page_name = 'Manage Ticket'

    ticket = Ticket.query.options(db.joinedload(Ticket.contents)).get_or_404(ticket_id)

    # Permission check
    if not can_access_ticket(ticket):
        flash('You do not have permission to edit this ticket.', 'danger')
        return redirect(url_for('routes.tickets'))

    form = TicketForm(obj=ticket)
    titles = Title.query.all()
    tech_users = User.query.filter(User.role_id.in_([2, 3])).all()
    form.title_id.choices = [(title.id, title.title_name) for title in titles]
    form.assigned_to_id.choices = [(user.id, user.get_full_name()) for user in tech_users]
    form.escalate.data = ticket.escalated

    if form.validate_on_submit():
        changes_made = False

        # Capture old values before any changes for email notifications
        old_status = ticket.tck_status
        old_assigned_to_id = ticket.assigned_to_id
        old_escalated = bool(ticket.escalated)

        # Check for changes in ticket fields
        if ticket.title_id != form.title_id.data:
            ticket.title_id = form.title_id.data
            changes_made = True
        if ticket.tck_status != form.tck_status.data:
            ticket.tck_status = form.tck_status.data
            changes_made = True
        if ticket.assigned_to_id != form.assigned_to_id.data:
            ticket.assigned_to_id = form.assigned_to_id.data
            changes_made = True

        # Only Admin, Specialist, and Technician can escalate/de-escalate
        if current_user.role_id in [1, 2, 3]:
            if 'escalate' in request.form:
                escalate_value = request.form.get('escalate') == '1'
            else:
                escalate_value = False

            if ticket.escalated != escalate_value:
                ticket.escalated = escalate_value
                changes_made = True
                flash(f'Ticket {"escalated" if ticket.escalated else "de-escalated"} successfully!', 'success')

            
        # Handle file upload
        uploaded_file = request.files.get('attachment')
        if uploaded_file and uploaded_file.filename != '':
            is_valid, error_message = validate_file_upload(uploaded_file)
            if not is_valid:
                flash(error_message, 'error')
                return redirect(request.url)

            file_ext = os.path.splitext(uploaded_file.filename)[1].lower()
            # Create filename with ticket ID
            new_filename = f"ticket_{ticket.id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}{file_ext}"
            filename = secure_filename(new_filename)
            upload_folder = current_app.config['UPLOAD_ATTACHMENT']
            os.makedirs(upload_folder, exist_ok=True)
            filepath = os.path.join(upload_folder, filename)
            
            try:
                uploaded_file.save(filepath)
                # Verify file was saved
                if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                    raise Exception("File saved as 0 bytes")
                    
                new_attachment = Ticket_attachment(
                    ticket_id=ticket.id,
                    attach_image=filename,
                    uploaded_at=datetime.now(timezone.utc),
                    user_id=current_user.id
                )
                db.session.add(new_attachment)
                changes_made = True
                flash('Attachment added successfully!', 'success')
                
            except Exception as e:
                current_app.logger.error(f"File save failed for ticket {ticket.id}: {e}", exc_info=True)
                flash('Failed to save attachment. Please try again.', 'danger')
                if os.path.exists(filepath):
                    os.remove(filepath)
                return redirect(request.url)

        # Add ticket contents (text-based)
        new_comments = [
            Ticket_content(
                ticket_id=ticket.id,
                content=subform.content.data,
                cnt_created_at=datetime.now(timezone.utc),
                user_id=current_user.id
            ) for subform in form.contents.entries if subform.content.data
        ]

        if new_comments:
            db.session.add_all(new_comments)
            changes_made = True

        if changes_made:
            ticket.updated_at = datetime.now(timezone.utc)
            db.session.add(ticket)
            db.session.commit()

            # Send email notifications for each change
            if ticket.tck_status != old_status:
                send_ticket_notification('status', ticket,
                                         old_status=old_status,
                                         new_status=ticket.tck_status)
            if ticket.assigned_to_id != old_assigned_to_id:
                new_assignee = db.session.get(User, ticket.assigned_to_id) if ticket.assigned_to_id else None
                send_ticket_notification('assigned', ticket, new_assignee=new_assignee)
            if bool(ticket.escalated) != old_escalated:
                send_ticket_notification('escalated', ticket, escalated=bool(ticket.escalated))
            if new_comments:
                send_ticket_notification('comment', ticket, commenter=current_user,
                                         comment_text=new_comments[-1].content)

            flash('Ticket updated successfully!', 'success')
        else:
            flash('No changes detected to update.', 'warning')

        return redirect(request.url)  # Stay on the same page after the changes
    return render_template('edit_ticket.html', form=form, ticket=ticket,
        current_path=current_path,
        current_page_name=current_page_name
        )




# ****************** Add Comment (AJAX) *******************************
@routes_blueprint.route('/add_comment/<int:ticket_id>', methods=['POST'])
@limiter.limit("20 per minute", key_func=get_remote_address)
@login_required
def add_comment(ticket_id):
    ticket = Ticket.query.get_or_404(ticket_id)

    if not can_access_ticket(ticket):
        return jsonify({'success': False, 'message': 'Permission denied'}), 403

    content = request.form.get('content', '').strip()
    if not content:
        return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400

    try:
        comment = Ticket_content(
            ticket_id=ticket.id,
            content=content,
            cnt_created_at=datetime.now(timezone.utc),
            user_id=current_user.id
        )
        db.session.add(comment)
        ticket.updated_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"add_comment failed: {e}")
        return jsonify({'success': False, 'message': 'Database error saving comment'}), 500

    send_ticket_notification('comment', ticket, commenter=current_user, comment_text=content)

    return jsonify({
        'success': True,
        'comment': {
            'author': current_user.get_full_name(),
            'date': comment.cnt_created_at.strftime('%m-%d-%Y %H:%M'),
            'content': content
        }
    })


# ****************** Delete Ticket Page *******************************
@routes_blueprint.route('/delete_ticket/<int:ticket_id>', methods=['POST'])
@login_required
def delete_ticket(ticket_id):
    is_admin()  # Ensure only admins can access this route
    ticket = Ticket.query.get_or_404(ticket_id)

    try:
        # Log ticket deletion
        current_app.logger.info(f"Deleting ticket ID: {ticket_id} by user: {current_user.id}")
        
        # Get all attachments for this ticket
        attachments = Ticket_attachment.query.filter_by(ticket_id=ticket_id).all()
        current_app.logger.debug(f"Found {len(attachments)} attachments to delete for ticket {ticket_id}")

        for attachment in attachments:
            if attachment.attach_image:
                # Extract filename safely
                filename = attachment.attach_image.split('/')[-1] if '/' in attachment.attach_image else attachment.attach_image
                
                # Construct full file path
                file_path = os.path.join(current_app.config['UPLOAD_ATTACHMENT'], filename)
                current_app.logger.debug(f"Attempting to delete file: {file_path}")
                
                # Verify and delete file
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        current_app.logger.info(f"File deleted successfully: {file_path}")
                    except OSError as e:
                        current_app.logger.error(f"Error deleting file {file_path}: {str(e)}")
                        raise  # Re-raise to trigger rollback
                else:
                    current_app.logger.warning(f"File not found: {file_path} (may have been deleted already)")
                
                # Delete attachment record
                db.session.delete(attachment)
                current_app.logger.debug(f"Attachment {attachment.id} marked for deletion")

        # Delete the ticket
        db.session.delete(ticket)
        db.session.commit()
        current_app.logger.info(f"Ticket {ticket_id} and attachments deleted successfully")
        
        flash('Ticket and all attachments deleted successfully', 'success')
        return redirect(url_for('routes.tickets'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting ticket {ticket_id}: {str(e)}", exc_info=True)
        flash('An error occurred while deleting the ticket. Please try again.', 'danger')
        return redirect(url_for('routes.tickets'))



# *********************************************************************
# ****************** Title Management Page *******************************
@routes_blueprint.route('/titles')
@login_required
def titles():
        # Mapping paths to page names
    page_names = {'/titles': 'Manage Ticket Titles'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    # Get the page number and per_page from the query parameters, default to 10 for per_page
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    # Query the users
    sort = request.args.get('sort', 'asc')
    order = Title.title_name.desc() if sort == 'desc' else Title.title_name.asc()
    total = Title.query.count()
    titles = Title.query.order_by(order).offset(offset).limit(per_page).all()
    # Set up pagination with Bootstrap 5 styling
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template('titles.html', titles=titles, pagination=pagination, per_page=per_page, total=total,
        sort=sort,
        current_path=current_path,
        current_page_name=current_page_name
    )


# ****************** Add Title Page *******************************
@routes_blueprint.route('/add_title', methods=['GET', 'POST'])
@login_required
def add_title():
        # Mapping paths to page names
    page_names = {'/add_title': 'New Ticket Title'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Ensure only admins can access this route
    form = TitleForm()
    if form.validate_on_submit():
        # Check if a title with the same name already exists
        existing_title = Title.query.filter_by(title_name=form.title_name.data).first()
        if existing_title:
            flash('This title already exists.', 'danger')
            return render_template('add_title.html', form=form)  # Re-render form with the error message
        # Create and add the new title
        new_title = Title(
            title_name=form.title_name.data
        )
        db.session.add(new_title)
        db.session.commit()
        flash('Title added successfully!', 'success')
        return redirect(url_for('routes.titles'))
    return render_template('add_title.html', form=form, 
        current_path=current_path, 
        current_page_name=current_page_name
    )

# ****************** Edit Title Page *******************************
@routes_blueprint.route('/edit_title/<int:title_id>', methods=['GET', 'POST'])
@login_required
def edit_title(title_id):
    is_admin()  # Ensure only admins can access this route
    title = Title.query.get_or_404(title_id)
    form = TitleForm(obj=title)
    if form.validate_on_submit():
        # Check for duplicate entries
        existing_title = Title.query.filter(Title.title_name == form.title_name.data, Title.id != title.id).first()
        if existing_title:
            flash('This title already exists.', 'danger')
            return render_template('add_title.html', form=form)  # Re-render form with the error message
        # Check if there are any changes to the form
        if (
            title.title_name == form.title_name.data
        ):
            flash('No changes were made.', 'info')
            return render_template('edit_title.html', form=form, title=title)
        title.title_name = form.title_name.data
        db.session.commit()
        flash('Title updated successfully!', 'success')
        return redirect(url_for('routes.titles'))
    return render_template('edit_title.html', form=form, title=title)

# ****************** Delete Title Page *******************************
@routes_blueprint.route('/delete_title/<int:title_id>', methods=['POST'])
@login_required
def delete_title(title_id):
    is_admin()  # Ensure only admins can access this route
    title = Title.query.get_or_404(title_id)
    db.session.delete(title)
    db.session.commit()
    flash('Title deleted successfully!', 'warning')
    return redirect(url_for('routes.titles'))


# *********************************************************************
# ****************** Facilities / Rooms (M&O) **************************
# *********************************************************************
# Reference: docs/PROJECT_PLAN.md — Site -> Facility -> Floor -> Room.
# List/detail (GET) is open to any logged-in user, scoped to their own site
# via can_access_site() — the same "district admins see everything,
# site-level users see their site" rule PROJECT_PLAN.md asks for. Mutations
# (POST) are admin-only, matching how Site management already works.
# "Delete" on a Facility/Room is a soft delete (is_active=False) so that
# work orders/assets/inspections added in later phases keep their history.

def _save_attachment(uploaded_file, entity_label, entity_id, upload_config_key, attachment_model, fk_field, extra=None):
    """
    Validate and persist an uploaded photo/document for a Facility, Room or
    Asset, mirroring the existing Ticket_attachment flow (validate_file_upload
    + a per-entity upload folder). `extra` is merged into the attachment row
    (e.g. condition_history_id). Returns an error message string, or None on
    success/no-op (no file selected is not an error).
    """
    if not uploaded_file or not uploaded_file.filename:
        return None

    is_valid, error_message = validate_file_upload(uploaded_file)
    if not is_valid:
        return error_message

    file_ext = os.path.splitext(uploaded_file.filename)[1].lower()
    new_filename = secure_filename(
        f"{entity_label}_{entity_id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}{file_ext}"
    )
    upload_folder = current_app.config[upload_config_key]
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, new_filename)
    uploaded_file.save(filepath)

    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return 'Failed to save attachment (empty file)'

    db.session.add(attachment_model(
        attach_file=new_filename,
        uploaded_at=datetime.now(timezone.utc),
        user_id=current_user.id,
        **{fk_field: entity_id, **(extra or {})}
    ))
    return None


# ****************** Facility List Page *******************************
@routes_blueprint.route('/facilities', methods=['GET'])
@login_required
def facilities():
    page_names = {'/facilities': 'Manage Facilities'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    site_filter = request.args.get('site_filter', '')
    status_filter = request.args.get('status_filter', 'active')

    query = Facility.query
    if current_user.role_id in (1, 2):
        if site_filter:
            try:
                query = query.filter(Facility.site_id == int(site_filter))
            except ValueError:
                pass
        sites = Site.query.order_by(Site.site_name).all()
    else:
        query = query.filter(Facility.site_id == current_user.site_id)
        sites = Site.query.filter_by(id=current_user.site_id).all()

    if status_filter == 'active':
        query = query.filter(Facility.is_active.is_(True))
    elif status_filter == 'inactive':
        query = query.filter(Facility.is_active.is_(False))
    # status_filter == 'all' -> no filter

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    facility_list = query.order_by(Facility.name.asc()).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template(
        'facilities.html',
        facilities=facility_list,
        pagination=pagination,
        per_page=per_page,
        total=total,
        current_path=current_path,
        current_page_name=current_page_name,
        sites=sites,
        status_filter=status_filter,
    )


# ****************** Add Facility Page *******************************
@routes_blueprint.route('/add_facility', methods=['GET', 'POST'])
@login_required
def add_facility():
    page_names = {'/add_facility': 'New Facility'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()  # Structural reference data — admin only, matching Site management

    form = FacilityForm()
    form.site_id.choices = [(s.id, s.site_name) for s in Site.query.order_by(Site.site_name).all()]

    if form.validate_on_submit():
        existing = Facility.query.filter_by(site_id=form.site_id.data, name=form.name.data).first()
        if existing:
            flash('A facility with this name already exists at that site.', 'danger')
            return render_template('add_facility.html', form=form,
                current_path=current_path, current_page_name=current_page_name)

        facility = Facility(
            site_id=form.site_id.data,
            name=form.name.data,
            facility_type=form.facility_type.data,
            building_code=form.building_code.data,
            address=form.address.data,
            year_built=form.year_built.data,
            square_footage=form.square_footage.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(facility)
        db.session.flush()

        error = _save_attachment(request.files.get('attachment'), 'facility', facility.id,
                                  'UPLOAD_FACILITY_ATTACHMENT', FacilityAttachment, 'facility_id')
        if error:
            db.session.rollback()
            flash(error, 'danger')
            return redirect(request.url)

        db.session.commit()
        flash('Facility added successfully!', 'success')
        return redirect(url_for('routes.edit_facility', facility_id=facility.id))

    return render_template('add_facility.html', form=form,
        current_path=current_path,
        current_page_name=current_page_name
    )


# ****************** Edit / Detail Facility Page *******************************
@routes_blueprint.route('/edit_facility/<int:facility_id>', methods=['GET', 'POST'])
@login_required
def edit_facility(facility_id):
    current_page_name = 'Facility Detail'
    facility = Facility.query.get_or_404(facility_id)
    if not can_access_site(facility.site_id):
        abort(403)

    form = FacilityForm(obj=facility)
    form.site_id.choices = [(s.id, s.site_name) for s in Site.query.order_by(Site.site_name).all()]
    floor_form = FloorForm()

    if request.method == 'POST':
        is_admin()  # Editing facility data is admin only, matching Site management
        if form.validate_on_submit():
            existing = Facility.query.filter(
                Facility.site_id == form.site_id.data,
                Facility.name == form.name.data,
                Facility.id != facility.id
            ).first()
            if existing:
                flash('A facility with this name already exists at that site.', 'danger')
                return render_template('edit_facility.html', form=form, floor_form=floor_form,
                    facility=facility, current_page_name=current_page_name)

            facility.site_id = form.site_id.data
            facility.name = form.name.data
            facility.facility_type = form.facility_type.data
            facility.building_code = form.building_code.data
            facility.address = form.address.data
            facility.year_built = form.year_built.data
            facility.square_footage = form.square_footage.data
            facility.notes = form.notes.data
            facility.is_active = form.is_active.data
            facility.updated_at = datetime.now(timezone.utc)

            error = _save_attachment(request.files.get('attachment'), 'facility', facility.id,
                                      'UPLOAD_FACILITY_ATTACHMENT', FacilityAttachment, 'facility_id')
            if error:
                flash(error, 'danger')
                return redirect(request.url)

            db.session.commit()
            flash('Facility updated successfully!', 'success')
            return redirect(url_for('routes.edit_facility', facility_id=facility.id))

    return render_template('edit_facility.html', form=form, floor_form=floor_form,
        facility=facility, current_page_name=current_page_name,
        health=_facility_health_for(facility), health_factors=analytics.HEALTH_FACTORS)


def _facility_health_for(facility, days=90):
    """Health score for one facility's detail page (staff only), over the
    last `days` for the period-dependent factors."""
    if current_user.role_id not in (1, 2, 3):
        return None
    today = datetime.now(timezone.utc).date()
    f = {'start': today - timedelta(days=days), 'end': today, 'facility_id': facility.id}
    groups = analytics.detect_recurring_issues(analytics.recurring_rows(f))
    rows = analytics.facility_health_scores(f, groups, facility_ids=[facility.id], today=today)
    return rows[0]['health'] if rows else None


# ****************** Delete (Deactivate) Facility *******************************
@routes_blueprint.route('/delete_facility/<int:facility_id>', methods=['POST'])
@login_required
def delete_facility(facility_id):
    is_admin()
    facility = Facility.query.get_or_404(facility_id)
    facility.is_active = False
    facility.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Facility deactivated successfully!', 'warning')
    return redirect(url_for('routes.facilities'))


# ****************** Floors (managed inline from the Facility detail page) ****
@routes_blueprint.route('/add_floor/<int:facility_id>', methods=['POST'])
@login_required
def add_floor(facility_id):
    is_admin()
    facility = Facility.query.get_or_404(facility_id)
    form = FloorForm()
    if form.validate_on_submit():
        existing = Floor.query.filter_by(facility_id=facility.id, name=form.name.data).first()
        if existing:
            flash('This floor already exists on this facility.', 'danger')
        else:
            db.session.add(Floor(facility_id=facility.id, name=form.name.data, sort_order=form.sort_order.data or 0))
            db.session.commit()
            flash('Floor added successfully!', 'success')
    else:
        flash('Floor name is required.', 'danger')
    return redirect(url_for('routes.edit_facility', facility_id=facility.id))


@routes_blueprint.route('/delete_floor/<int:floor_id>', methods=['POST'])
@login_required
def delete_floor(floor_id):
    is_admin()
    floor = Floor.query.get_or_404(floor_id)
    facility_id = floor.facility_id
    if Room.query.filter_by(floor_id=floor.id).count() > 0:
        flash('Cannot delete a floor that still has rooms assigned to it. Reassign or remove those rooms first.', 'danger')
        return redirect(url_for('routes.edit_facility', facility_id=facility_id))
    db.session.delete(floor)
    db.session.commit()
    flash('Floor deleted successfully!', 'warning')
    return redirect(url_for('routes.edit_facility', facility_id=facility_id))


# ****************** Facility Attachments *******************************
@routes_blueprint.route('/download_facility_attachment/<int:attachment_id>')
@login_required
def download_facility_attachment(attachment_id):
    attachment = FacilityAttachment.query.get_or_404(attachment_id)
    facility = Facility.query.get_or_404(attachment.facility_id)
    if not can_access_site(facility.site_id):
        abort(403)

    upload_folder = current_app.config['UPLOAD_FACILITY_ATTACHMENT']
    file_path = os.path.join(upload_folder, attachment.attach_file)
    if not os.path.exists(file_path):
        flash('File not found.', 'error')
        return redirect(url_for('routes.edit_facility', facility_id=facility.id))
    return send_from_directory(upload_folder, attachment.attach_file, as_attachment=True)


@routes_blueprint.route('/delete_facility_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_facility_attachment(attachment_id):
    is_admin()
    attachment = FacilityAttachment.query.get_or_404(attachment_id)
    facility_id = attachment.facility_id
    file_path = os.path.join(current_app.config['UPLOAD_FACILITY_ATTACHMENT'], attachment.attach_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted successfully.', 'success')
    return redirect(url_for('routes.edit_facility', facility_id=facility_id))


# ****************** Room List Page *******************************
@routes_blueprint.route('/rooms', methods=['GET'])
@login_required
def rooms():
    page_names = {'/rooms': 'Manage Rooms'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    site_filter = request.args.get('site_filter', '')
    facility_filter = request.args.get('facility_filter', '')
    status_filter = request.args.get('status_filter', 'active')

    query = Room.query
    if current_user.role_id in (1, 2):
        if site_filter:
            try:
                query = query.filter(Room.site_id == int(site_filter))
            except ValueError:
                pass
        sites = Site.query.order_by(Site.site_name).all()
        facilities_qs = Facility.query.order_by(Facility.name).all()
    else:
        query = query.filter(Room.site_id == current_user.site_id)
        sites = Site.query.filter_by(id=current_user.site_id).all()
        facilities_qs = Facility.query.filter_by(site_id=current_user.site_id).order_by(Facility.name).all()

    if facility_filter:
        try:
            query = query.filter(Room.facility_id == int(facility_filter))
        except ValueError:
            pass

    if status_filter == 'active':
        query = query.filter(Room.is_active.is_(True))
    elif status_filter == 'inactive':
        query = query.filter(Room.is_active.is_(False))

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    room_list = query.order_by(Room.room_number.asc()).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template(
        'rooms.html',
        rooms=room_list,
        pagination=pagination,
        per_page=per_page,
        total=total,
        current_path=current_path,
        current_page_name=current_page_name,
        sites=sites,
        facilities=facilities_qs,
        status_filter=status_filter,
    )


def _facility_and_floor_choices(current_facility=None):
    """Shared (facility_id, floor_id) SelectField choices for add/edit Room forms."""
    facility_qs = Facility.query.filter_by(is_active=True).order_by(Facility.name).all()
    if current_facility and not current_facility.is_active and current_facility not in facility_qs:
        facility_qs = facility_qs + [current_facility]
    facility_choices = [(f.id, f"{f.name} ({f.site.site_name})") for f in facility_qs]
    floor_choices = [(0, '-- No floor --')] + [
        (fl.id, f"{f.name} - {fl.name}") for f in facility_qs for fl in f.floors
    ]
    return facility_choices, floor_choices


# ****************** Add Room Page *******************************
@routes_blueprint.route('/add_room', methods=['GET', 'POST'])
@login_required
def add_room():
    page_names = {'/add_room': 'New Room'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()

    form = RoomForm()
    form.facility_id.choices, form.floor_id.choices = _facility_and_floor_choices()

    if form.validate_on_submit():
        facility = Facility.query.get_or_404(form.facility_id.data)
        existing = Room.query.filter_by(facility_id=facility.id, room_number=form.room_number.data).first()
        if existing:
            flash('A room with this number already exists in this facility.', 'danger')
            return render_template('add_room.html', form=form,
                current_path=current_path, current_page_name=current_page_name)

        floor_id = form.floor_id.data or None
        if floor_id and Floor.query.filter_by(id=floor_id, facility_id=facility.id).first() is None:
            flash('Selected floor does not belong to the selected facility.', 'danger')
            return render_template('add_room.html', form=form,
                current_path=current_path, current_page_name=current_page_name)

        room = Room(
            site_id=facility.site_id,
            facility_id=facility.id,
            floor_id=floor_id,
            room_number=form.room_number.data,
            room_name=form.room_name.data,
            room_type=form.room_type.data,
            capacity=form.capacity.data,
            square_footage=form.square_footage.data,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(room)
        db.session.flush()

        error = _save_attachment(request.files.get('attachment'), 'room', room.id,
                                  'UPLOAD_ROOM_ATTACHMENT', RoomAttachment, 'room_id')
        if error:
            db.session.rollback()
            flash(error, 'danger')
            return redirect(request.url)

        db.session.commit()
        flash('Room added successfully!', 'success')
        return redirect(url_for('routes.edit_room', room_id=room.id))

    return render_template('add_room.html', form=form,
        current_path=current_path,
        current_page_name=current_page_name
    )


# ****************** Edit / Detail Room Page *******************************
@routes_blueprint.route('/edit_room/<int:room_id>', methods=['GET', 'POST'])
@login_required
def edit_room(room_id):
    current_page_name = 'Room Detail'
    room = Room.query.get_or_404(room_id)
    if not can_access_site(room.site_id):
        abort(403)

    form = RoomForm(obj=room)
    form.facility_id.choices, form.floor_id.choices = _facility_and_floor_choices(room.facility)
    if request.method == 'GET':
        form.floor_id.data = room.floor_id or 0

    if request.method == 'POST':
        is_admin()
        if form.validate_on_submit():
            facility = Facility.query.get_or_404(form.facility_id.data)
            existing = Room.query.filter(
                Room.facility_id == facility.id,
                Room.room_number == form.room_number.data,
                Room.id != room.id
            ).first()
            if existing:
                flash('A room with this number already exists in this facility.', 'danger')
                return render_template('edit_room.html', form=form, room=room, current_page_name=current_page_name)

            floor_id = form.floor_id.data or None
            if floor_id and Floor.query.filter_by(id=floor_id, facility_id=facility.id).first() is None:
                flash('Selected floor does not belong to the selected facility.', 'danger')
                return render_template('edit_room.html', form=form, room=room, current_page_name=current_page_name)

            room.site_id = facility.site_id
            room.facility_id = facility.id
            room.floor_id = floor_id
            room.room_number = form.room_number.data
            room.room_name = form.room_name.data
            room.room_type = form.room_type.data
            room.capacity = form.capacity.data
            room.square_footage = form.square_footage.data
            room.notes = form.notes.data
            room.is_active = form.is_active.data
            room.updated_at = datetime.now(timezone.utc)

            error = _save_attachment(request.files.get('attachment'), 'room', room.id,
                                      'UPLOAD_ROOM_ATTACHMENT', RoomAttachment, 'room_id')
            if error:
                flash(error, 'danger')
                return redirect(request.url)

            db.session.commit()
            flash('Room updated successfully!', 'success')
            return redirect(url_for('routes.edit_room', room_id=room.id))

    return render_template('edit_room.html', form=form, room=room, current_page_name=current_page_name)


# ****************** Delete (Deactivate) Room *******************************
@routes_blueprint.route('/delete_room/<int:room_id>', methods=['POST'])
@login_required
def delete_room(room_id):
    is_admin()
    room = Room.query.get_or_404(room_id)
    room.is_active = False
    room.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Room deactivated successfully!', 'warning')
    return redirect(url_for('routes.rooms'))


# ****************** Room Attachments *******************************
@routes_blueprint.route('/download_room_attachment/<int:attachment_id>')
@login_required
def download_room_attachment(attachment_id):
    attachment = RoomAttachment.query.get_or_404(attachment_id)
    room = Room.query.get_or_404(attachment.room_id)
    if not can_access_site(room.site_id):
        abort(403)

    upload_folder = current_app.config['UPLOAD_ROOM_ATTACHMENT']
    file_path = os.path.join(upload_folder, attachment.attach_file)
    if not os.path.exists(file_path):
        flash('File not found.', 'error')
        return redirect(url_for('routes.edit_room', room_id=room.id))
    return send_from_directory(upload_folder, attachment.attach_file, as_attachment=True)


@routes_blueprint.route('/delete_room_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_room_attachment(attachment_id):
    is_admin()
    attachment = RoomAttachment.query.get_or_404(attachment_id)
    room_id = attachment.room_id
    file_path = os.path.join(current_app.config['UPLOAD_ROOM_ATTACHMENT'], attachment.attach_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted successfully.', 'success')
    return redirect(url_for('routes.edit_room', room_id=room_id))


# *********************************************************************
# ****************** Assets & Asset Condition (M&O) ********************
# *********************************************************************
# Same access model as Facilities/Rooms: GET is site-scoped via
# can_access_site(), Asset/AssetType mutations are admin-only. The one
# exception is recording a condition assessment, which Admins, Specialists
# and Technicians (role_id 1-3) can do for assets at sites they can access —
# technicians are the people actually inspecting equipment.
# AssetConditionHistory is append-only, enforced by ORM listeners in models.py.

# ****************** Asset Types (admin reference data) ****************
@routes_blueprint.route('/asset_types', methods=['GET'])
@login_required
def asset_types():
    page_names = {'/asset_types': 'Manage Asset Types'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()
    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = AssetType.query.count()
    types = AssetType.query.order_by(AssetType.name.asc()).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')
    return render_template('asset_types.html', asset_types=types, pagination=pagination,
        per_page=per_page, total=total,
        current_path=current_path,
        current_page_name=current_page_name
    )


@routes_blueprint.route('/add_asset_type', methods=['GET', 'POST'])
@login_required
def add_asset_type():
    page_names = {'/add_asset_type': 'New Asset Type'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()
    form = AssetTypeForm()
    if form.validate_on_submit():
        if AssetType.query.filter_by(name=form.name.data).first():
            flash('An asset type with this name already exists.', 'danger')
            return render_template('add_asset_type.html', form=form,
                current_path=current_path, current_page_name=current_page_name)
        db.session.add(AssetType(
            name=form.name.data,
            category=form.category.data,
            expected_life_years=form.expected_life_years.data,
            description=form.description.data,
        ))
        db.session.commit()
        flash('Asset type added successfully!', 'success')
        return redirect(url_for('routes.asset_types'))
    return render_template('add_asset_type.html', form=form,
        current_path=current_path,
        current_page_name=current_page_name
    )


@routes_blueprint.route('/edit_asset_type/<int:asset_type_id>', methods=['GET', 'POST'])
@login_required
def edit_asset_type(asset_type_id):
    is_admin()
    asset_type = AssetType.query.get_or_404(asset_type_id)
    form = AssetTypeForm(obj=asset_type)
    if form.validate_on_submit():
        existing = AssetType.query.filter(AssetType.name == form.name.data, AssetType.id != asset_type.id).first()
        if existing:
            flash('An asset type with this name already exists.', 'danger')
            return render_template('edit_asset_type.html', form=form, asset_type=asset_type)
        asset_type.name = form.name.data
        asset_type.category = form.category.data
        asset_type.expected_life_years = form.expected_life_years.data
        asset_type.description = form.description.data
        asset_type.is_active = form.is_active.data
        db.session.commit()
        flash('Asset type updated successfully!', 'success')
        return redirect(url_for('routes.asset_types'))
    return render_template('edit_asset_type.html', form=form, asset_type=asset_type)


@routes_blueprint.route('/delete_asset_type/<int:asset_type_id>', methods=['POST'])
@login_required
def delete_asset_type(asset_type_id):
    is_admin()
    asset_type = AssetType.query.get_or_404(asset_type_id)
    if Asset.query.filter_by(asset_type_id=asset_type.id).count() > 0:
        flash('Cannot delete an asset type that is still assigned to assets. Deactivate it instead.', 'danger')
        return redirect(url_for('routes.asset_types'))
    db.session.delete(asset_type)
    db.session.commit()
    flash('Asset type deleted successfully!', 'warning')
    return redirect(url_for('routes.asset_types'))


# ****************** Assets *******************************
def _asset_form_choices(current_asset=None):
    """Shared (facility, room, asset type) SelectField choices for add/edit Asset forms."""
    facility_qs = Facility.query.filter_by(is_active=True).order_by(Facility.name).all()
    if current_asset and current_asset.facility and not current_asset.facility.is_active:
        facility_qs = facility_qs + [current_asset.facility]
    facility_choices = [(f.id, f"{f.name} ({f.site.site_name})") for f in facility_qs]

    facility_ids = [f.id for f in facility_qs]
    room_qs = Room.query.filter(Room.facility_id.in_(facility_ids), Room.is_active.is_(True)) \
                        .order_by(Room.facility_id, Room.room_number).all() if facility_ids else []
    if current_asset and current_asset.room and current_asset.room not in room_qs:
        room_qs = room_qs + [current_asset.room]
    room_choices = [(0, '-- No room --')] + [
        (r.id, f"{r.facility.name} - {r.room_number}{' ' + r.room_name if r.room_name else ''}") for r in room_qs
    ]

    type_qs = AssetType.query.filter_by(is_active=True).order_by(AssetType.name).all()
    if current_asset and current_asset.asset_type and current_asset.asset_type not in type_qs:
        type_qs = type_qs + [current_asset.asset_type]
    type_choices = [(t.id, t.name) for t in type_qs]
    return facility_choices, room_choices, type_choices


def _project_choices(current_project=None):
    project_qs = Project.query.filter(Project.status.notin_(('Completed', 'Cancelled'))).order_by(Project.name).all()
    if current_project and current_project not in project_qs:
        project_qs = project_qs + [current_project]
    return [(0, '-- None --')] + [(p.id, p.name) for p in project_qs]


@routes_blueprint.route('/assets', methods=['GET'])
@login_required
def assets():
    page_names = {'/assets': 'Manage Assets'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    site_filter = request.args.get('site_filter', '')
    facility_filter = request.args.get('facility_filter', '')
    type_filter = request.args.get('type_filter', '')
    condition_filter = request.args.get('condition_filter', '')
    status_filter = request.args.get('status_filter', 'active')
    search = request.args.get('search', '').strip()

    query = Asset.query
    if current_user.role_id in (1, 2):
        if site_filter:
            try:
                query = query.filter(Asset.site_id == int(site_filter))
            except ValueError:
                pass
        sites = Site.query.order_by(Site.site_name).all()
        facilities_qs = Facility.query.order_by(Facility.name).all()
    else:
        query = query.filter(Asset.site_id == current_user.site_id)
        sites = Site.query.filter_by(id=current_user.site_id).all()
        facilities_qs = Facility.query.filter_by(site_id=current_user.site_id).order_by(Facility.name).all()

    for raw, column in ((facility_filter, Asset.facility_id), (type_filter, Asset.asset_type_id)):
        if raw:
            try:
                query = query.filter(column == int(raw))
            except ValueError:
                pass

    if condition_filter:
        query = query.filter(Asset.condition_label == condition_filter)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Asset.asset_tag.ilike(like), Asset.serial_number.ilike(like), Asset.name.ilike(like)))

    if status_filter == 'active':
        query = query.filter(Asset.is_active.is_(True))
    elif status_filter == 'inactive':
        query = query.filter(Asset.is_active.is_(False))

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    asset_list = query.options(
        db.joinedload(Asset.facility), db.joinedload(Asset.room), db.joinedload(Asset.asset_type)
    ).order_by(Asset.asset_tag.asc()).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template(
        'assets.html',
        assets=asset_list,
        pagination=pagination,
        per_page=per_page,
        total=total,
        current_path=current_path,
        current_page_name=current_page_name,
        sites=sites,
        facilities=facilities_qs,
        asset_types=AssetType.query.order_by(AssetType.name).all(),
        condition_labels=[label for _, label in CONDITION_SCALE],
        status_filter=status_filter,
        search=search,
    )


def _validate_asset_location(form, exclude_asset_id=None):
    """Return (facility, room_id, error) for a submitted AssetForm; error is None when valid."""
    facility = Facility.query.get_or_404(form.facility_id.data)
    tag_query = Asset.query.filter(Asset.asset_tag == form.asset_tag.data)
    if exclude_asset_id:
        tag_query = tag_query.filter(Asset.id != exclude_asset_id)
    if tag_query.first():
        return facility, None, 'An asset with this tag already exists.'
    room_id = form.room_id.data or None
    if room_id and Room.query.filter_by(id=room_id, facility_id=facility.id).first() is None:
        return facility, None, 'Selected room does not belong to the selected facility.'
    return facility, room_id, None


@routes_blueprint.route('/add_asset', methods=['GET', 'POST'])
@login_required
def add_asset():
    page_names = {'/add_asset': 'New Asset'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')
    is_admin()

    form = AssetForm()
    form.facility_id.choices, form.room_id.choices, form.asset_type_id.choices = _asset_form_choices()
    form.project_id.choices = _project_choices()

    if form.validate_on_submit():
        facility, room_id, error = _validate_asset_location(form)
        if error:
            flash(error, 'danger')
            return render_template('add_asset.html', form=form,
                current_path=current_path, current_page_name=current_page_name)

        asset = Asset(
            site_id=facility.site_id,
            facility_id=facility.id,
            room_id=room_id,
            asset_type_id=form.asset_type_id.data,
            asset_tag=form.asset_tag.data,
            name=form.name.data,
            manufacturer=form.manufacturer.data,
            model_number=form.model_number.data,
            serial_number=form.serial_number.data or None,
            install_date=form.install_date.data,
            purchase_date=form.purchase_date.data,
            purchase_cost=form.purchase_cost.data,
            warranty_expiration=form.warranty_expiration.data,
            expected_life_years=form.expected_life_years.data,
            project_id=form.project_id.data or None,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(asset)
        db.session.flush()

        error = _save_attachment(request.files.get('attachment'), 'asset', asset.id,
                                  'UPLOAD_ASSET_ATTACHMENT', AssetAttachment, 'asset_id')
        if error:
            db.session.rollback()
            flash(error, 'danger')
            return redirect(request.url)

        db.session.commit()
        flash('Asset added successfully!', 'success')
        return redirect(url_for('routes.edit_asset', asset_id=asset.id))

    return render_template('add_asset.html', form=form,
        current_path=current_path,
        current_page_name=current_page_name
    )


@routes_blueprint.route('/edit_asset/<int:asset_id>', methods=['GET', 'POST'])
@login_required
def edit_asset(asset_id):
    current_page_name = 'Asset Detail'
    asset = Asset.query.get_or_404(asset_id)
    if not can_access_site(asset.site_id):
        abort(403)

    form = AssetForm(obj=asset)
    form.facility_id.choices, form.room_id.choices, form.asset_type_id.choices = _asset_form_choices(asset)
    form.project_id.choices = _project_choices(asset.project)
    condition_form = AssetConditionForm()
    risk_form = AssetRiskFieldsForm()
    if request.method == 'GET':
        form.room_id.data = asset.room_id or 0
        form.project_id.data = asset.project_id or 0
        condition_form.assessed_at.data = datetime.now(timezone.utc).date()
        risk_form.safety_impact.data = asset.safety_impact if asset.safety_impact is not None else -1
        risk_form.operational_importance.data = asset.operational_importance if asset.operational_importance is not None else -1

    if request.method == 'POST':
        is_admin()
        if form.validate_on_submit():
            facility, room_id, error = _validate_asset_location(form, exclude_asset_id=asset.id)
            if error:
                flash(error, 'danger')
                return render_template('edit_asset.html', form=form, condition_form=condition_form,
                    risk_form=risk_form, risk=asset_risk_score(asset), asset=asset, current_page_name=current_page_name)

            asset.site_id = facility.site_id
            asset.facility_id = facility.id
            asset.room_id = room_id
            asset.asset_type_id = form.asset_type_id.data
            asset.asset_tag = form.asset_tag.data
            asset.name = form.name.data
            asset.manufacturer = form.manufacturer.data
            asset.model_number = form.model_number.data
            asset.serial_number = form.serial_number.data or None
            asset.install_date = form.install_date.data
            asset.purchase_date = form.purchase_date.data
            asset.purchase_cost = form.purchase_cost.data
            asset.warranty_expiration = form.warranty_expiration.data
            asset.expected_life_years = form.expected_life_years.data
            asset.project_id = form.project_id.data or None
            asset.notes = form.notes.data
            asset.is_active = form.is_active.data
            asset.updated_at = datetime.now(timezone.utc)

            error = _save_attachment(request.files.get('attachment'), 'asset', asset.id,
                                      'UPLOAD_ASSET_ATTACHMENT', AssetAttachment, 'asset_id')
            if error:
                flash(error, 'danger')
                return redirect(request.url)

            db.session.commit()
            flash('Asset updated successfully!', 'success')
            return redirect(url_for('routes.edit_asset', asset_id=asset.id))

    return render_template('edit_asset.html', form=form, condition_form=condition_form,
        risk_form=risk_form, risk=asset_risk_score(asset), asset=asset, current_page_name=current_page_name)


def asset_risk_score(asset):
    return risk.calculate_risk(asset)


@routes_blueprint.route('/update_asset_risk_fields/<int:asset_id>', methods=['POST'])
@login_required
def update_asset_risk_fields(asset_id):
    is_admin()
    asset = Asset.query.get_or_404(asset_id)
    form = AssetRiskFieldsForm()
    if not form.validate_on_submit():
        flash('Invalid risk factor selection.', 'danger')
        return redirect(url_for('routes.edit_asset', asset_id=asset.id))
    asset.safety_impact = form.safety_impact.data if form.safety_impact.data != -1 else None
    asset.operational_importance = form.operational_importance.data if form.operational_importance.data != -1 else None
    db.session.commit()
    flash('Risk factors saved.', 'success')
    return redirect(url_for('routes.edit_asset', asset_id=asset.id))


@routes_blueprint.route('/delete_asset/<int:asset_id>', methods=['POST'])
@login_required
def delete_asset(asset_id):
    is_admin()
    asset = Asset.query.get_or_404(asset_id)
    asset.is_active = False
    asset.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Asset deactivated successfully!', 'warning')
    return redirect(url_for('routes.assets'))


# ****************** Condition assessments (append-only) *******************
@routes_blueprint.route('/add_asset_condition/<int:asset_id>', methods=['POST'])
@login_required
def add_asset_condition(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    if current_user.role_id not in (1, 2, 3) or not can_access_site(asset.site_id):
        abort(403)

    form = AssetConditionForm()
    if not form.validate_on_submit():
        flash('Assessment date and a score between 0 and 100 are required.', 'danger')
        return redirect(url_for('routes.edit_asset', asset_id=asset.id))

    entry = AssetConditionHistory(
        asset_id=asset.id,
        assessed_at=form.assessed_at.data,
        score=form.score.data,
        condition=condition_label_for_score(form.score.data),
        inspector_id=current_user.id,
        reason=form.reason.data,
        notes=form.notes.data,
        recommended_action=form.recommended_action.data,
    )
    db.session.add(entry)
    db.session.flush()

    # Only refresh the cached "current" condition if this is the newest assessment;
    # back-dated entries are still recorded but don't overwrite a later one.
    latest = asset.condition_history[0] if asset.condition_history else None
    if latest is None or latest.id == entry.id:
        asset.apply_condition(entry)

    error = _save_attachment(request.files.get('photo'), 'asset', asset.id,
                              'UPLOAD_ASSET_ATTACHMENT', AssetAttachment, 'asset_id',
                              extra={'condition_history_id': entry.id})
    if error:
        db.session.rollback()
        flash(error, 'danger')
        return redirect(url_for('routes.edit_asset', asset_id=asset.id))

    db.session.commit()
    flash(f'Condition recorded: {entry.condition} ({entry.score}).', 'success')
    return redirect(url_for('routes.edit_asset', asset_id=asset.id))


# ****************** QR code *******************************
def asset_qr_url(asset):
    """The URL an asset's QR code encodes — its detail page."""
    return url_for('routes.edit_asset', asset_id=asset.id, _external=True)


@routes_blueprint.route('/asset_qr/<int:asset_id>.png')
@login_required
def asset_qr(asset_id):
    asset = Asset.query.get_or_404(asset_id)
    if not can_access_site(asset.site_id):
        abort(403)
    img = qrcode.make(asset_qr_url(asset))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', download_name=f'asset_{asset.asset_tag}_qr.png')


# ****************** Asset Attachments *******************************
@routes_blueprint.route('/download_asset_attachment/<int:attachment_id>')
@login_required
def download_asset_attachment(attachment_id):
    attachment = AssetAttachment.query.get_or_404(attachment_id)
    asset = Asset.query.get_or_404(attachment.asset_id)
    if not can_access_site(asset.site_id):
        abort(403)

    upload_folder = current_app.config['UPLOAD_ASSET_ATTACHMENT']
    file_path = os.path.join(upload_folder, attachment.attach_file)
    if not os.path.exists(file_path):
        flash('File not found.', 'error')
        return redirect(url_for('routes.edit_asset', asset_id=asset.id))
    return send_from_directory(upload_folder, attachment.attach_file, as_attachment=True)


@routes_blueprint.route('/delete_asset_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_asset_attachment(attachment_id):
    is_admin()
    attachment = AssetAttachment.query.get_or_404(attachment_id)
    asset_id = attachment.asset_id
    file_path = os.path.join(current_app.config['UPLOAD_ASSET_ATTACHMENT'], attachment.attach_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted successfully.', 'success')
    return redirect(url_for('routes.edit_asset', asset_id=asset_id))


# *********************************************************************
# ****************** Work Orders (M&O) *********************************
# *********************************************************************
# Access tiers mirror tickets: Admin/Specialist (1/2) see and manage every
# work order; Technicians (3) see and manage their own site's; everyone
# else sees only work orders they requested or are assigned to, and can
# comment but not edit. Status changes go through workflow.apply_transition().
# There is no delete route — a work order is Cancelled, never removed.

def can_access_work_order(wo):
    if current_user.role_id in (1, 2):
        return True
    if current_user.role_id == 3:
        return wo.site_id == current_user.site_id
    return current_user.id in (wo.requester_id, wo.assigned_to_id)


def can_manage_work_order(wo):
    if current_user.role_id in (1, 2):
        return True
    return current_user.role_id == 3 and wo.site_id == current_user.site_id


def is_staff():
    """Admin, Specialist or Technician — the roles that create Manual work orders."""
    if current_user.role_id not in (1, 2, 3):
        abort(403)


def _visible_site_ids():
    """Site ids the current user may pick from; None means every site."""
    return None if current_user.role_id in (1, 2) else [current_user.site_id]


def _location_choices(site_ids=None, current=None, with_none=True):
    """
    (facility, room, asset) SelectField choices, optionally limited to site_ids.
    `current` is a WorkOrder whose (possibly inactive) links must stay selectable.
    """
    fq = Facility.query.filter_by(is_active=True)
    if site_ids:
        fq = fq.filter(Facility.site_id.in_(site_ids))
    facilities = fq.order_by(Facility.name).all()
    if current and current.facility and current.facility not in facilities:
        facilities.append(current.facility)
    fids = [f.id for f in facilities]

    rooms = Room.query.filter(Room.facility_id.in_(fids), Room.is_active.is_(True)) \
                      .order_by(Room.facility_id, Room.room_number).all() if fids else []
    if current and current.room and current.room not in rooms:
        rooms.append(current.room)
    assets = Asset.query.filter(Asset.facility_id.in_(fids), Asset.is_active.is_(True)) \
                        .order_by(Asset.asset_tag).all() if fids else []
    if current and current.asset and current.asset not in assets:
        assets.append(current.asset)

    none = [(0, '-- None --')] if with_none else []
    return (
        none + [(f.id, f"{f.name} ({f.site.site_name})") for f in facilities],
        [(0, '-- No room --')] + [(r.id, f"{r.facility.name} - {r.room_number}{' ' + r.room_name if r.room_name else ''}") for r in rooms],
        [(0, '-- No asset --')] + [(a.id, f"{a.asset_tag} {a.name}") for a in assets],
    )


def _classification_choices(current=None):
    cats = Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()
    if current and current.category and current.category not in cats:
        cats.append(current.category)
    subs = Subcategory.query.filter(Subcategory.category_id.in_([c.id for c in cats]), Subcategory.is_active.is_(True)) \
                            .order_by(Subcategory.name).all() if cats else []
    if current and current.subcategory and current.subcategory not in subs:
        subs.append(current.subcategory)
    pris = Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()
    if current and current.priority and current.priority not in pris:
        pris.append(current.priority)
    return (
        [(c.id, c.name) for c in cats],
        [(0, '-- None --')] + [(s.id, f"{s.category.name} - {s.name}") for s in subs],
        [(p.id, p.name) for p in pris],
    )


def _assignee_choices(site_ids=None):
    q = User.query.filter(User.role_id.in_([2, 3]), User.status == 'Active')
    if site_ids:
        q = q.filter(User.site_id.in_(site_ids))
    return [(0, '-- Unassigned --')] + [(u.id, u.get_full_name()) for u in q.order_by(User.first_name).all()]


def _validate_work_order_links(facility_id, room_id, asset_id, category_id, subcategory_id, site_id=None):
    """
    Cross-check the chosen facility/room/asset/subcategory belong together.
    Returns (site_id, error). Site comes from the facility when one is chosen.
    """
    facility = db.session.get(Facility, facility_id) if facility_id else None
    if facility_id and facility is None:
        return None, 'Selected facility not found.'
    if facility:
        site_id = facility.site_id
    if room_id:
        room = db.session.get(Room, room_id)
        if room is None or not facility or room.facility_id != facility.id:
            return None, 'Selected room does not belong to the selected facility.'
    if asset_id:
        asset = db.session.get(Asset, asset_id)
        if asset is None or not facility or asset.facility_id != facility.id:
            return None, 'Selected asset does not belong to the selected facility.'
    if subcategory_id:
        sub = db.session.get(Subcategory, subcategory_id)
        if sub is None or sub.category_id != category_id:
            return None, 'Selected subcategory does not belong to the selected category.'
    if not site_id:
        return None, 'A site is required.'
    return site_id, None


# ****************** Priorities (admin reference data) *****************
@routes_blueprint.route('/priorities')
@login_required
def priorities():
    current_page_name = 'Manage Priorities'
    is_admin()
    items = Priority.query.order_by(Priority.sort_order, Priority.name).all()
    return render_template('priorities.html', priorities=items,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/add_priority', methods=['GET', 'POST'])
@login_required
def add_priority():
    current_page_name = 'New Priority'
    is_admin()
    form = PriorityForm()
    if form.validate_on_submit():
        if Priority.query.filter_by(name=form.name.data).first():
            flash('A priority with this name already exists.', 'danger')
            return render_template('add_priority.html', form=form, current_path=request.path, current_page_name=current_page_name)
        db.session.add(Priority(name=form.name.data, description=form.description.data,
                                sort_order=form.sort_order.data or 0, color=form.color.data))
        db.session.commit()
        flash('Priority added successfully!', 'success')
        return redirect(url_for('routes.priorities'))
    return render_template('add_priority.html', form=form, current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_priority/<int:priority_id>', methods=['GET', 'POST'])
@login_required
def edit_priority(priority_id):
    is_admin()
    priority = Priority.query.get_or_404(priority_id)
    form = PriorityForm(obj=priority)
    if form.validate_on_submit():
        if Priority.query.filter(Priority.name == form.name.data, Priority.id != priority.id).first():
            flash('A priority with this name already exists.', 'danger')
            return render_template('edit_priority.html', form=form, priority=priority)
        priority.name = form.name.data
        priority.description = form.description.data
        priority.sort_order = form.sort_order.data or 0
        priority.color = form.color.data
        priority.is_active = form.is_active.data
        db.session.commit()
        flash('Priority updated successfully!', 'success')
        return redirect(url_for('routes.priorities'))
    return render_template('edit_priority.html', form=form, priority=priority)


@routes_blueprint.route('/delete_priority/<int:priority_id>', methods=['POST'])
@login_required
def delete_priority(priority_id):
    is_admin()
    priority = Priority.query.get_or_404(priority_id)
    if WorkOrder.query.filter_by(priority_id=priority.id).count() > 0:
        flash('Cannot delete a priority that work orders still use. Deactivate it instead.', 'danger')
        return redirect(url_for('routes.priorities'))
    db.session.delete(priority)
    db.session.commit()
    flash('Priority deleted successfully!', 'warning')
    return redirect(url_for('routes.priorities'))


# ****************** Categories / Subcategories (admin reference data) ***
@routes_blueprint.route('/categories')
@login_required
def categories():
    current_page_name = 'Manage Categories'
    is_admin()
    items = Category.query.order_by(Category.sort_order, Category.name).all()
    return render_template('categories.html', categories=items,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/add_category', methods=['GET', 'POST'])
@login_required
def add_category():
    current_page_name = 'New Category'
    is_admin()
    form = CategoryForm()
    if form.validate_on_submit():
        if Category.query.filter_by(name=form.name.data).first():
            flash('A category with this name already exists.', 'danger')
            return render_template('add_category.html', form=form, current_path=request.path, current_page_name=current_page_name)
        category = Category(name=form.name.data, sort_order=form.sort_order.data or 0)
        db.session.add(category)
        db.session.commit()
        flash('Category added successfully!', 'success')
        return redirect(url_for('routes.edit_category', category_id=category.id))
    return render_template('add_category.html', form=form, current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_category/<int:category_id>', methods=['GET', 'POST'])
@login_required
def edit_category(category_id):
    is_admin()
    category = Category.query.get_or_404(category_id)
    form = CategoryForm(obj=category)
    sub_form = SubcategoryForm()
    if form.validate_on_submit():
        if Category.query.filter(Category.name == form.name.data, Category.id != category.id).first():
            flash('A category with this name already exists.', 'danger')
            return render_template('edit_category.html', form=form, sub_form=sub_form, category=category)
        category.name = form.name.data
        category.sort_order = form.sort_order.data or 0
        category.is_active = form.is_active.data
        db.session.commit()
        flash('Category updated successfully!', 'success')
        return redirect(url_for('routes.categories'))
    return render_template('edit_category.html', form=form, sub_form=sub_form, category=category)


@routes_blueprint.route('/delete_category/<int:category_id>', methods=['POST'])
@login_required
def delete_category(category_id):
    is_admin()
    category = Category.query.get_or_404(category_id)
    if WorkOrder.query.filter_by(category_id=category.id).count() > 0:
        flash('Cannot delete a category that work orders still use. Deactivate it instead.', 'danger')
        return redirect(url_for('routes.categories'))
    db.session.delete(category)
    db.session.commit()
    flash('Category deleted successfully!', 'warning')
    return redirect(url_for('routes.categories'))


@routes_blueprint.route('/add_subcategory/<int:category_id>', methods=['POST'])
@login_required
def add_subcategory(category_id):
    is_admin()
    category = Category.query.get_or_404(category_id)
    form = SubcategoryForm()
    if form.validate_on_submit():
        if Subcategory.query.filter_by(category_id=category.id, name=form.name.data).first():
            flash('This subcategory already exists in this category.', 'danger')
        else:
            db.session.add(Subcategory(category_id=category.id, name=form.name.data))
            db.session.commit()
            flash('Subcategory added successfully!', 'success')
    else:
        flash('Subcategory name is required.', 'danger')
    return redirect(url_for('routes.edit_category', category_id=category.id))


@routes_blueprint.route('/delete_subcategory/<int:subcategory_id>', methods=['POST'])
@login_required
def delete_subcategory(subcategory_id):
    is_admin()
    sub = Subcategory.query.get_or_404(subcategory_id)
    category_id = sub.category_id
    if WorkOrder.query.filter_by(subcategory_id=sub.id).count() > 0:
        sub.is_active = False
        db.session.commit()
        flash('Subcategory is in use by work orders, so it was deactivated instead of deleted.', 'warning')
    else:
        db.session.delete(sub)
        db.session.commit()
        flash('Subcategory deleted successfully!', 'warning')
    return redirect(url_for('routes.edit_category', category_id=category_id))


# ****************** Work Order list *******************************
@routes_blueprint.route('/work_orders', methods=['GET'])
@login_required
def work_orders():
    page_names = {'/work_orders': 'Work Orders'}
    current_path = request.path
    current_page_name = page_names.get(current_path, 'Unknown Page')

    status_filter = request.args.get('status_filter', 'open')
    priority_filter = request.args.get('priority_filter', '')
    category_filter = request.args.get('category_filter', '')
    site_filter = request.args.get('site_filter', '')
    assigned_filter = request.args.get('assigned_filter', '')
    source_filter = request.args.get('source_filter', '')
    search = request.args.get('search', '').strip()

    query = WorkOrder.query.join(Priority, WorkOrder.priority_id == Priority.id)
    if current_user.role_id in (1, 2):
        if site_filter:
            try:
                query = query.filter(WorkOrder.site_id == int(site_filter))
            except ValueError:
                pass
        sites = Site.query.order_by(Site.site_name).all()
    elif current_user.role_id == 3:
        query = query.filter(WorkOrder.site_id == current_user.site_id)
        sites = Site.query.filter_by(id=current_user.site_id).all()
    else:
        query = query.filter(db.or_(WorkOrder.requester_id == current_user.id,
                                    WorkOrder.assigned_to_id == current_user.id))
        sites = []

    if status_filter == 'open':
        query = query.filter(WorkOrder.status.in_(workflow.OPEN_STATUSES))
    elif status_filter and status_filter != 'all':
        query = query.filter(WorkOrder.status == status_filter)

    for raw, column in ((priority_filter, WorkOrder.priority_id), (category_filter, WorkOrder.category_id)):
        if raw:
            try:
                query = query.filter(column == int(raw))
            except ValueError:
                pass
    if assigned_filter == 'me':
        query = query.filter(WorkOrder.assigned_to_id == current_user.id)
    elif assigned_filter == 'unassigned':
        query = query.filter(WorkOrder.assigned_to_id.is_(None))
    elif assigned_filter:
        try:
            query = query.filter(WorkOrder.assigned_to_id == int(assigned_filter))
        except ValueError:
            pass
    if source_filter in workflow.SOURCES:
        query = query.filter(WorkOrder.source == source_filter)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(WorkOrder.wo_number.ilike(like), WorkOrder.title.ilike(like)))

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    items = query.options(
        db.joinedload(WorkOrder.facility), db.joinedload(WorkOrder.room), db.joinedload(WorkOrder.site),
        db.joinedload(WorkOrder.assigned_to), db.joinedload(WorkOrder.requester), db.joinedload(WorkOrder.category),
    ).order_by(Priority.sort_order.asc(), WorkOrder.created_at.desc()).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template(
        'work_orders.html',
        work_orders=items, pagination=pagination, per_page=per_page, total=total,
        current_path=current_path, current_page_name=current_page_name,
        sites=sites,
        priorities=Priority.query.order_by(Priority.sort_order).all(),
        categories=Category.query.order_by(Category.sort_order, Category.name).all(),
        assignees=_assignee_choices(_visible_site_ids())[1:] if current_user.role_id in (1, 2, 3) else [],
        statuses=workflow.ALL_STATUSES, sources=workflow.SOURCES, status_badge=workflow.STATUS_BADGE,
        status_filter=status_filter, search=search,
    )


# ****************** Requester submission (the simple mobile flow) ********
@routes_blueprint.route('/request_work_order', methods=['GET', 'POST'])
@login_required
def request_work_order():
    current_page_name = 'New Request'
    form = WorkOrderRequestForm()
    facility_choices, room_choices, _ = _location_choices(_visible_site_ids(), with_none=False)
    form.facility_id.choices = facility_choices
    form.room_id.choices = room_choices
    form.category_id.choices, _, form.priority_id.choices = _classification_choices()

    if form.validate_on_submit():
        site_id, error = _validate_work_order_links(
            form.facility_id.data, form.room_id.data or None, None, form.category_id.data, None)
        if error:
            flash(error, 'danger')
            return render_template('request_work_order.html', form=form, current_page_name=current_page_name)
        if not can_access_site(site_id):
            abort(403)

        wo = WorkOrder(
            site_id=site_id,
            facility_id=form.facility_id.data,
            room_id=form.room_id.data or None,
            title=form.title.data,
            description=form.description.data,
            source=workflow.SOURCE_REQUEST,
            status=workflow.NEW,
            priority_id=form.priority_id.data,
            category_id=form.category_id.data,
            requester_id=current_user.id,
            created_by_id=current_user.id,
        )
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        workflow.record_initial_status(wo, current_user)

        error = _save_attachment(request.files.get('attachment'), 'wo', wo.id,
                                  'UPLOAD_WORK_ORDER_ATTACHMENT', WorkOrderAttachment, 'work_order_id')
        if error:
            db.session.rollback()
            flash(error, 'danger')
            return redirect(request.url)
        db.session.commit()
        flash(f'Request {wo.wo_number} submitted. The M&O team will review it.', 'success')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    return render_template('request_work_order.html', form=form, current_page_name=current_page_name)


def _fill_staff_form_choices(form, current=None):
    site_ids = _visible_site_ids()
    if site_ids:
        form.site_id.choices = [(s.id, s.site_name) for s in Site.query.filter(Site.id.in_(site_ids)).all()]
    else:
        form.site_id.choices = [(s.id, s.site_name) for s in Site.query.order_by(Site.site_name).all()]
    form.facility_id.choices, form.room_id.choices, form.asset_id.choices = _location_choices(site_ids, current)
    form.category_id.choices, form.subcategory_id.choices, form.priority_id.choices = _classification_choices(current)
    form.assigned_to_id.choices = _assignee_choices(site_ids)
    vendor_qs = Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()
    if current and current.vendor and current.vendor not in vendor_qs:
        vendor_qs.append(current.vendor)
    form.vendor_id.choices = [(0, '-- No vendor --')] + [(v.id, v.name) for v in vendor_qs]
    form.project_id.choices = _project_choices(current.project if current else None)


def _apply_staff_form(form, wo):
    """Copy WorkOrderForm fields onto a WorkOrder. Returns an error string or None."""
    site_id, error = _validate_work_order_links(
        form.facility_id.data or None, form.room_id.data or None, form.asset_id.data or None,
        form.category_id.data, form.subcategory_id.data or None, site_id=form.site_id.data)
    if error:
        return error
    if not can_access_site(site_id):
        return 'You cannot create work orders for that site.'
    wo.site_id = site_id
    wo.facility_id = form.facility_id.data or None
    wo.room_id = form.room_id.data or None
    wo.asset_id = form.asset_id.data or None
    wo.title = form.title.data
    wo.description = form.description.data
    wo.category_id = form.category_id.data
    wo.subcategory_id = form.subcategory_id.data or None
    wo.priority_id = form.priority_id.data
    wo.assigned_to_id = form.assigned_to_id.data or None
    wo.assigned_team = form.assigned_team.data or None
    wo.vendor_id = form.vendor_id.data or None
    wo.project_id = form.project_id.data or None
    wo.scheduled_date = form.scheduled_date.data
    wo.due_date = form.due_date.data
    wo.estimated_cost = form.estimated_cost.data
    wo.actual_cost = form.actual_cost.data
    wo.resolution = form.resolution.data or None
    return None


# ****************** Staff: create a Manual work order ********************
@routes_blueprint.route('/add_work_order', methods=['GET', 'POST'])
@login_required
def add_work_order():
    current_page_name = 'New Work Order'
    is_staff()
    form = WorkOrderForm()
    _fill_staff_form_choices(form)
    if request.method == 'GET':
        form.site_id.data = current_user.site_id

    if form.validate_on_submit():
        wo = WorkOrder(source=workflow.SOURCE_MANUAL, status=workflow.NEW, created_by_id=current_user.id)
        error = _apply_staff_form(form, wo)
        if error:
            flash(error, 'danger')
            return render_template('add_work_order.html', form=form, current_page_name=current_page_name)
        db.session.add(wo)
        db.session.flush()
        wo.assign_number()
        workflow.record_initial_status(wo, current_user)
        if wo.assigned_to_id:
            workflow.apply_transition(wo, workflow.ASSIGNED, current_user, note='Assigned at creation')

        error = _save_attachment(request.files.get('attachment'), 'wo', wo.id,
                                  'UPLOAD_WORK_ORDER_ATTACHMENT', WorkOrderAttachment, 'work_order_id')
        if error:
            db.session.rollback()
            flash(error, 'danger')
            return redirect(request.url)
        db.session.commit()
        if wo.assigned_to_id:
            send_work_order_notification('created', wo)
        flash(f'Work order {wo.wo_number} created.', 'success')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    return render_template('add_work_order.html', form=form, current_page_name=current_page_name)


# ****************** Work Order detail / edit *******************************
@routes_blueprint.route('/edit_work_order/<int:work_order_id>', methods=['GET', 'POST'])
@login_required
def edit_work_order(work_order_id):
    current_page_name = 'Work Order'
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_access_work_order(wo):
        abort(403)
    manage = can_manage_work_order(wo)

    form = WorkOrderForm(obj=wo)
    _fill_staff_form_choices(form, wo)
    status_form = WorkOrderStatusForm()
    status_form.new_status.choices = [(s, s) for s in workflow.allowed_transitions(wo.status)]
    comment_form = WorkOrderCommentForm()
    labor_form = WorkOrderLaborForm()
    labor_form.technician_id.choices = _assignee_choices(_visible_site_ids())[1:]  # drop "Unassigned"
    material_form = WorkOrderMaterialForm()
    material_form.vendor_id.choices = [(0, '-- No vendor --')] + [
        (v.id, v.name) for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()]
    cost_form = CostRecordForm(obj=wo.cost_record)
    if request.method == 'GET':
        for field, value in (('facility_id', wo.facility_id), ('room_id', wo.room_id), ('asset_id', wo.asset_id),
                             ('subcategory_id', wo.subcategory_id), ('assigned_to_id', wo.assigned_to_id),
                             ('vendor_id', wo.vendor_id), ('project_id', wo.project_id)):
            getattr(form, field).data = value or 0
        status_form.completed_at.data = datetime.now(timezone.utc).date()

    if request.method == 'POST':
        if not manage:
            abort(403)
        if form.validate_on_submit():
            old_assignee_id = wo.assigned_to_id
            error = _apply_staff_form(form, wo)
            if error:
                flash(error, 'danger')
                return render_template('edit_work_order.html', form=form, status_form=status_form,
                    comment_form=comment_form, labor_form=labor_form, material_form=material_form,
                    cost_form=cost_form, wo=wo, manage=manage, current_page_name=current_page_name,
                    status_badge=workflow.STATUS_BADGE)
            assignee_changed = wo.assigned_to_id != old_assignee_id
            if assignee_changed and wo.assigned_to_id and wo.status == workflow.NEW:
                workflow.apply_transition(wo, workflow.ASSIGNED, current_user, note='Assigned')
            wo.updated_at = datetime.now(timezone.utc)

            error = _save_attachment(request.files.get('attachment'), 'wo', wo.id,
                                      'UPLOAD_WORK_ORDER_ATTACHMENT', WorkOrderAttachment, 'work_order_id')
            if error:
                flash(error, 'danger')
                return redirect(request.url)
            db.session.commit()
            if assignee_changed and wo.assigned_to_id:
                send_work_order_notification('assigned', wo, new_assignee=wo.assigned_to)
            flash('Work order updated successfully!', 'success')
            return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    return render_template('edit_work_order.html', form=form, status_form=status_form,
        comment_form=comment_form, labor_form=labor_form, material_form=material_form,
        cost_form=cost_form, wo=wo, manage=manage, current_page_name=current_page_name,
        status_badge=workflow.STATUS_BADGE, recurring_groups=analytics.related_recurring_groups(wo))


# ****************** Labor / Materials / Cost Record (Phase 7) *************
@routes_blueprint.route('/add_work_order_labor/<int:work_order_id>', methods=['POST'])
@login_required
def add_work_order_labor(work_order_id):
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_manage_work_order(wo):
        abort(403)
    form = WorkOrderLaborForm()
    form.technician_id.choices = _assignee_choices(_visible_site_ids())[1:]
    if not form.validate_on_submit():
        flash('A technician and either labor hours or a start/end time are required.', 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    hours = form.labor_hours.data
    if form.start_time.data and form.end_time.data:
        if form.end_time.data <= form.start_time.data:
            flash('End time must be after start time.', 'danger')
            return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))
        hours = round((form.end_time.data - form.start_time.data).total_seconds() / 3600, 2)
    if not hours:
        flash('Enter labor hours, or both a start and end time.', 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    db.session.add(WorkOrderLabor(
        work_order_id=wo.id, technician_id=form.technician_id.data,
        start_time=form.start_time.data, end_time=form.end_time.data,
        labor_hours=hours, labor_type=form.labor_type.data, hourly_rate=form.hourly_rate.data,
        notes=form.notes.data, created_by_id=current_user.id,
    ))
    db.session.flush()
    costs_module.refresh_cost_record(wo)
    db.session.commit()
    flash('Labor entry added.', 'success')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


@routes_blueprint.route('/delete_work_order_labor/<int:labor_id>', methods=['POST'])
@login_required
def delete_work_order_labor(labor_id):
    entry = WorkOrderLabor.query.get_or_404(labor_id)
    wo = entry.work_order
    if not can_manage_work_order(wo):
        abort(403)
    db.session.delete(entry)
    db.session.flush()
    costs_module.refresh_cost_record(wo)
    db.session.commit()
    flash('Labor entry removed.', 'warning')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


@routes_blueprint.route('/add_work_order_material/<int:work_order_id>', methods=['POST'])
@login_required
def add_work_order_material(work_order_id):
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_manage_work_order(wo):
        abort(403)
    form = WorkOrderMaterialForm()
    form.vendor_id.choices = [(0, '-- No vendor --')] + [
        (v.id, v.name) for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()]
    if not form.validate_on_submit():
        flash('A description, quantity, and unit cost are required.', 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    db.session.add(WorkOrderMaterial(
        work_order_id=wo.id, description=form.description.data, quantity=form.quantity.data,
        unit_cost=form.unit_cost.data, vendor_id=form.vendor_id.data or None,
        notes=form.notes.data, created_by_id=current_user.id,
    ))
    db.session.flush()
    costs_module.refresh_cost_record(wo)
    db.session.commit()
    flash('Material added.', 'success')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


@routes_blueprint.route('/delete_work_order_material/<int:material_id>', methods=['POST'])
@login_required
def delete_work_order_material(material_id):
    entry = WorkOrderMaterial.query.get_or_404(material_id)
    wo = entry.work_order
    if not can_manage_work_order(wo):
        abort(403)
    db.session.delete(entry)
    db.session.flush()
    costs_module.refresh_cost_record(wo)
    db.session.commit()
    flash('Material removed.', 'warning')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


@routes_blueprint.route('/update_work_order_cost/<int:work_order_id>', methods=['POST'])
@login_required
def update_work_order_cost(work_order_id):
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_manage_work_order(wo):
        abort(403)
    form = CostRecordForm()
    if not form.validate_on_submit():
        flash('Vendor cost and other expenses must be numbers.', 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    # refresh_cost_record() is the single place that gets-or-creates the
    # CostRecord for a WorkOrder — don't duplicate that here. Creating one
    # via a second, separate get-or-create in this route (bypassing the
    # relationship) left `wo.cost_record`'s cached value stale, so
    # refresh_cost_record's own lookup didn't see it and tried to insert a
    # second row with the same work_order_id, tripping the unique constraint.
    record = costs_module.refresh_cost_record(wo)
    record.vendor_cost = form.vendor_cost.data or 0
    record.other_cost = form.other_cost.data or 0
    record.total_cost = record.labor_cost + record.material_cost + record.vendor_cost + record.other_cost
    db.session.commit()
    flash('Cost adjustments saved.', 'success')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


# ****************** Cost Rollups *******************************
@routes_blueprint.route('/cost_rollups')
@login_required
def cost_rollups():
    current_page_name = 'Cost Rollups'
    is_staff()
    dimension = request.args.get('dimension', 'facility')
    if dimension not in costs_module.ROLLUP_DIMENSIONS:
        dimension = 'facility'

    query = WorkOrder.query.options(
        db.joinedload(WorkOrder.facility), db.joinedload(WorkOrder.site), db.joinedload(WorkOrder.asset),
        db.joinedload(WorkOrder.category), db.joinedload(WorkOrder.vendor), db.joinedload(WorkOrder.assigned_to),
        db.joinedload(WorkOrder.cost_record),
    )
    site_ids = _visible_site_ids()
    if site_ids:
        query = query.filter(WorkOrder.site_id.in_(site_ids))

    rows = costs_module.cost_rollup(query.all(), dimension)
    return render_template('cost_rollups.html', rows=rows, dimension=dimension,
        dimensions=costs_module.ROLLUP_DIMENSIONS, dimension_labels=costs_module.ROLLUP_LABELS,
        current_path=request.path, current_page_name=current_page_name)


# ****************** Technician Workload *******************************
@routes_blueprint.route('/technician_workload')
@login_required
def technician_workload():
    current_page_name = 'Technician Workload'
    is_staff()
    site_ids = _visible_site_ids()

    tech_query = User.query.filter_by(role_id=3, status='Active')
    if site_ids:
        tech_query = tech_query.filter(User.site_id.in_(site_ids))
    technicians = tech_query.order_by(User.first_name).all()

    wo_query = WorkOrder.query.filter(WorkOrder.assigned_to_id.isnot(None))
    if site_ids:
        wo_query = wo_query.filter(WorkOrder.site_id.in_(site_ids))
    work_orders_by_tech = {}
    for wo in wo_query.all():
        work_orders_by_tech.setdefault(wo.assigned_to_id, []).append(wo)

    rows = costs_module.technician_workload(technicians, work_orders_by_tech)
    return render_template('technician_workload.html', rows=rows,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/change_work_order_status/<int:work_order_id>', methods=['POST'])
@login_required
def change_work_order_status(work_order_id):
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_manage_work_order(wo):
        abort(403)
    form = WorkOrderStatusForm()
    form.new_status.choices = [(s, s) for s in workflow.allowed_transitions(wo.status)]
    if not form.validate_on_submit():
        flash('Please choose a valid status.', 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))

    old_status = wo.status
    completed_at = datetime.combine(form.completed_at.data, datetime.min.time()) if form.completed_at.data else None
    try:
        workflow.apply_transition(wo, form.new_status.data, current_user, note=form.note.data or None,
                                  completed_at=completed_at, resolution=form.resolution.data or None)
    except WorkflowError as e:
        db.session.rollback()
        flash(str(e), 'danger')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))
    db.session.commit()
    send_work_order_notification('status', wo, old_status=old_status, new_status=wo.status)
    flash(f'Status changed to {wo.status}.', 'success')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


@routes_blueprint.route('/add_work_order_comment/<int:work_order_id>', methods=['POST'])
@limiter.limit("20 per minute", key_func=get_remote_address)
@login_required
def add_work_order_comment(work_order_id):
    wo = WorkOrder.query.get_or_404(work_order_id)
    if not can_access_work_order(wo):
        abort(403)
    form = WorkOrderCommentForm()
    if form.validate_on_submit():
        db.session.add(WorkOrderComment(work_order_id=wo.id, content=form.content.data, user_id=current_user.id))
        wo.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        send_work_order_notification('comment', wo, commenter=current_user, comment_text=form.content.data)
        flash('Comment added.', 'success')
    else:
        flash('Comment cannot be empty.', 'danger')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


# ****************** Work Order Attachments *******************************
@routes_blueprint.route('/download_work_order_attachment/<int:attachment_id>')
@login_required
def download_work_order_attachment(attachment_id):
    attachment = WorkOrderAttachment.query.get_or_404(attachment_id)
    wo = WorkOrder.query.get_or_404(attachment.work_order_id)
    if not can_access_work_order(wo):
        abort(403)
    upload_folder = current_app.config['UPLOAD_WORK_ORDER_ATTACHMENT']
    if not os.path.exists(os.path.join(upload_folder, attachment.attach_file)):
        flash('File not found.', 'error')
        return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))
    return send_from_directory(upload_folder, attachment.attach_file, as_attachment=True)


@routes_blueprint.route('/delete_work_order_attachment/<int:attachment_id>', methods=['POST'])
@login_required
def delete_work_order_attachment(attachment_id):
    attachment = WorkOrderAttachment.query.get_or_404(attachment_id)
    wo = WorkOrder.query.get_or_404(attachment.work_order_id)
    if not (can_manage_work_order(wo) or current_user.id == attachment.user_id):
        abort(403)
    file_path = os.path.join(current_app.config['UPLOAD_WORK_ORDER_ATTACHMENT'], attachment.attach_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(attachment)
    db.session.commit()
    flash('Attachment deleted successfully.', 'success')
    return redirect(url_for('routes.edit_work_order', work_order_id=wo.id))


# *********************************************************************
# ****************** Preventive Maintenance (M&O) ***********************
# *********************************************************************
# MaintenancePlan CRUD is admin-only, matching Priority/Category (Phase 3) —
# it's structural configuration, not an operational record. The PM dashboard
# and "run now" trigger are open to staff (Admin/Specialist/Technician),
# site-scoped like the work order list. Generation logic lives in
# application/pm.py so it's identical whether called from here, the "run
# now" button, or the daily APScheduler job in scheduled_jobs.py.

def _maintenance_plan_choices(current=None):
    assets = Asset.query.filter_by(is_active=True).order_by(Asset.asset_tag).all()
    if current and current.asset and current.asset not in assets:
        assets.append(current.asset)
    types = AssetType.query.filter_by(is_active=True).order_by(AssetType.name).all()
    if current and current.asset_type and current.asset_type not in types:
        types.append(current.asset_type)
    return (
        [(0, '-- None --')] + [(a.id, f"{a.asset_tag} — {a.name}") for a in assets],
        [(0, '-- None --')] + [(t.id, t.name) for t in types],
    )


def _fill_maintenance_plan_choices(form, current=None):
    form.asset_id.choices, form.asset_type_id.choices = _maintenance_plan_choices(current)
    form.frequency.choices = [(f, f) for f in pm.FREQUENCIES]
    form.category_id.choices = [(c.id, c.name) for c in Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()]
    form.priority_id.choices = [(p.id, p.name) for p in Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()]
    form.assigned_to_id.choices = _assignee_choices()


def _validate_maintenance_plan_target(form):
    """Exactly one of asset_id/asset_type_id must be chosen; Custom frequency needs an interval."""
    asset_id = form.asset_id.data or None
    asset_type_id = form.asset_type_id.data or None
    if bool(asset_id) == bool(asset_type_id):
        return None, None, 'Choose exactly one: a specific asset, or an asset type.'
    if form.frequency.data == pm.CUSTOM and not form.custom_interval_days.data:
        return None, None, 'A custom interval (in days) is required for Custom frequency.'
    return asset_id, asset_type_id, None


@routes_blueprint.route('/maintenance_plans')
@login_required
def maintenance_plans():
    current_page_name = 'Manage PM Plans'
    is_admin()
    items = MaintenancePlan.query.order_by(MaintenancePlan.name).all()
    return render_template('maintenance_plans.html', plans=items,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/add_maintenance_plan', methods=['GET', 'POST'])
@login_required
def add_maintenance_plan():
    current_page_name = 'New PM Plan'
    is_admin()
    form = MaintenancePlanForm()
    _fill_maintenance_plan_choices(form)

    if form.validate_on_submit():
        asset_id, asset_type_id, error = _validate_maintenance_plan_target(form)
        if error:
            flash(error, 'danger')
            return render_template('add_maintenance_plan.html', form=form, current_path=request.path, current_page_name=current_page_name)

        plan = MaintenancePlan(
            name=form.name.data,
            asset_id=asset_id,
            asset_type_id=asset_type_id,
            frequency=form.frequency.data,
            custom_interval_days=form.custom_interval_days.data if form.frequency.data == pm.CUSTOM else None,
            start_date=form.start_date.data,
            category_id=form.category_id.data,
            priority_id=form.priority_id.data,
            assigned_to_id=form.assigned_to_id.data or None,
            assigned_team=form.assigned_team.data or None,
            description=form.description.data,
            created_by_id=current_user.id,
        )
        db.session.add(plan)
        db.session.commit()
        flash(f'Maintenance plan "{plan.name}" created.', 'success')
        return redirect(url_for('routes.maintenance_plans'))

    return render_template('add_maintenance_plan.html', form=form,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_maintenance_plan/<int:plan_id>', methods=['GET', 'POST'])
@login_required
def edit_maintenance_plan(plan_id):
    is_admin()
    plan = MaintenancePlan.query.get_or_404(plan_id)
    form = MaintenancePlanForm(obj=plan)
    _fill_maintenance_plan_choices(form, plan)
    if request.method == 'GET':
        form.asset_id.data = plan.asset_id or 0
        form.asset_type_id.data = plan.asset_type_id or 0
        form.assigned_to_id.data = plan.assigned_to_id or 0

    if form.validate_on_submit():
        asset_id, asset_type_id, error = _validate_maintenance_plan_target(form)
        if error:
            flash(error, 'danger')
            return render_template('edit_maintenance_plan.html', form=form, plan=plan)

        plan.name = form.name.data
        plan.asset_id = asset_id
        plan.asset_type_id = asset_type_id
        plan.frequency = form.frequency.data
        plan.custom_interval_days = form.custom_interval_days.data if form.frequency.data == pm.CUSTOM else None
        plan.start_date = form.start_date.data
        plan.category_id = form.category_id.data
        plan.priority_id = form.priority_id.data
        plan.assigned_to_id = form.assigned_to_id.data or None
        plan.assigned_team = form.assigned_team.data or None
        plan.description = form.description.data
        plan.is_active = form.is_active.data
        plan.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('Maintenance plan updated successfully!', 'success')
        return redirect(url_for('routes.maintenance_plans'))

    return render_template('edit_maintenance_plan.html', form=form, plan=plan)


@routes_blueprint.route('/delete_maintenance_plan/<int:plan_id>', methods=['POST'])
@login_required
def delete_maintenance_plan(plan_id):
    is_admin()
    plan = MaintenancePlan.query.get_or_404(plan_id)
    plan.is_active = False
    plan.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Maintenance plan deactivated successfully!', 'warning')
    return redirect(url_for('routes.maintenance_plans'))


# ****************** PM Dashboard *******************************
@routes_blueprint.route('/pm_dashboard')
@login_required
def pm_dashboard():
    current_page_name = 'Preventive Maintenance'
    is_staff()

    query = MaintenanceSchedule.query.join(MaintenancePlan).filter(MaintenancePlan.is_active.is_(True)) \
                                     .options(db.joinedload(MaintenanceSchedule.asset), db.joinedload(MaintenanceSchedule.plan))
    if current_user.role_id == 3:
        query = query.join(Asset, MaintenanceSchedule.asset_id == Asset.id).filter(Asset.site_id == current_user.site_id)
    schedules = [s for s in query.all() if s.asset.is_active]

    buckets = pm.dashboard_buckets(schedules)
    return render_template('pm_dashboard.html', buckets=buckets,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/run_pm_generation', methods=['POST'])
@login_required
def run_pm_generation():
    is_admin()
    created = pm.generate_due_work_orders(user=current_user)
    if created:
        flash(f'Generated {len(created)} preventive-maintenance work order(s): '
              f'{", ".join(wo.wo_number for wo in created)}.', 'success')
    else:
        flash('No preventive-maintenance work orders were due.', 'info')
    return redirect(url_for('routes.pm_dashboard'))


# *********************************************************************
# ****************** Inspections (M&O) **********************************
# *********************************************************************
# InspectionTemplate/Item CRUD is admin-only, matching Priority/Category/
# MaintenancePlan. Scheduling and performing inspections, and the due/
# overdue dashboard, are open to staff (Admin/Specialist/Technician),
# site-scoped for Technicians like work orders and the PM dashboard.
# An Inspection is dual-purpose (see the models.py docstring): it starts
# "Scheduled" with a due_date and no results, and becomes "Completed" in
# place the moment record_inspection_results() writes its InspectionResult
# rows. There is no separate schedule table, mirroring how Phase 4 needed
# one (MaintenanceSchedule) only because a plan can target many assets —
# an Inspection already names its one concrete target directly.

def can_access_inspection(inspection):
    if current_user.role_id in (1, 2):
        return True
    return current_user.role_id == 3 and inspection.site_id == current_user.site_id


# ****************** Inspection Templates *******************************
@routes_blueprint.route('/inspection_templates')
@login_required
def inspection_templates():
    current_page_name = 'Manage Inspection Templates'
    is_admin()
    items = InspectionTemplate.query.order_by(InspectionTemplate.name).all()
    return render_template('inspection_templates.html', templates=items,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/add_inspection_template', methods=['GET', 'POST'])
@login_required
def add_inspection_template():
    current_page_name = 'New Inspection Template'
    is_admin()
    form = InspectionTemplateForm()
    form.category_id.choices = [(c.id, c.name) for c in Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()]
    form.priority_id.choices = [(p.id, p.name) for p in Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()]

    if form.validate_on_submit():
        if InspectionTemplate.query.filter_by(name=form.name.data).first():
            flash('A template with this name already exists.', 'danger')
            return render_template('add_inspection_template.html', form=form, current_path=request.path, current_page_name=current_page_name)
        template = InspectionTemplate(
            name=form.name.data, description=form.description.data,
            category_id=form.category_id.data, priority_id=form.priority_id.data,
            created_by_id=current_user.id,
        )
        db.session.add(template)
        db.session.commit()
        flash(f'Template "{template.name}" created. Add checklist items below.', 'success')
        return redirect(url_for('routes.edit_inspection_template', template_id=template.id))

    return render_template('add_inspection_template.html', form=form,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_inspection_template/<int:template_id>', methods=['GET', 'POST'])
@login_required
def edit_inspection_template(template_id):
    is_admin()
    template = InspectionTemplate.query.get_or_404(template_id)
    form = InspectionTemplateForm(obj=template)
    form.category_id.choices = [(c.id, c.name) for c in Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()]
    form.priority_id.choices = [(p.id, p.name) for p in Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()]
    item_form = InspectionItemForm()

    if form.validate_on_submit():
        if InspectionTemplate.query.filter(InspectionTemplate.name == form.name.data, InspectionTemplate.id != template.id).first():
            flash('A template with this name already exists.', 'danger')
            return render_template('edit_inspection_template.html', form=form, item_form=item_form, template=template)
        template.name = form.name.data
        template.description = form.description.data
        template.category_id = form.category_id.data
        template.priority_id = form.priority_id.data
        template.is_active = form.is_active.data
        db.session.commit()
        flash('Template updated successfully!', 'success')
        return redirect(url_for('routes.inspection_templates'))

    return render_template('edit_inspection_template.html', form=form, item_form=item_form, template=template)


@routes_blueprint.route('/delete_inspection_template/<int:template_id>', methods=['POST'])
@login_required
def delete_inspection_template(template_id):
    is_admin()
    template = InspectionTemplate.query.get_or_404(template_id)
    if Inspection.query.filter_by(template_id=template.id).count() > 0:
        flash('Cannot delete a template that has inspections against it. Deactivate it instead.', 'danger')
        return redirect(url_for('routes.inspection_templates'))
    db.session.delete(template)
    db.session.commit()
    flash('Template deleted successfully!', 'warning')
    return redirect(url_for('routes.inspection_templates'))


@routes_blueprint.route('/add_inspection_item/<int:template_id>', methods=['POST'])
@login_required
def add_inspection_item(template_id):
    is_admin()
    template = InspectionTemplate.query.get_or_404(template_id)
    form = InspectionItemForm()
    if form.validate_on_submit():
        db.session.add(InspectionItem(template_id=template.id, question=form.question.data,
                                      sort_order=form.sort_order.data or 0))
        db.session.commit()
        flash('Checklist item added.', 'success')
    else:
        flash('A question is required.', 'danger')
    return redirect(url_for('routes.edit_inspection_template', template_id=template.id))


@routes_blueprint.route('/delete_inspection_item/<int:item_id>', methods=['POST'])
@login_required
def delete_inspection_item(item_id):
    is_admin()
    item = InspectionItem.query.get_or_404(item_id)
    template_id = item.template_id
    if InspectionResult.query.filter_by(inspection_item_id=item.id).count() > 0:
        item.is_active = False
        db.session.commit()
        flash('Item is referenced by past inspection results, so it was deactivated instead of deleted.', 'warning')
    else:
        db.session.delete(item)
        db.session.commit()
        flash('Checklist item removed.', 'warning')
    return redirect(url_for('routes.edit_inspection_template', template_id=template_id))


# ****************** Scheduling & performing inspections ******************
def _validate_inspection_target(form):
    """Exactly one of facility_id/room_id/asset_id must be chosen. Returns (site_id, error)."""
    chosen = [v for v in (form.facility_id.data or None, form.room_id.data or None, form.asset_id.data or None) if v]
    if len(chosen) != 1:
        return None, 'Choose exactly one target: a facility, a room, or an asset.'
    if form.facility_id.data:
        facility = db.session.get(Facility, form.facility_id.data)
        return (facility.site_id if facility else None), (None if facility else 'Selected facility not found.')
    if form.room_id.data:
        room = db.session.get(Room, form.room_id.data)
        return (room.site_id if room else None), (None if room else 'Selected room not found.')
    asset = db.session.get(Asset, form.asset_id.data)
    return (asset.site_id if asset else None), (None if asset else 'Selected asset not found.')


@routes_blueprint.route('/inspections')
@login_required
def inspections():
    current_page_name = 'Inspections'
    is_staff()
    status_filter = request.args.get('status_filter', 'Scheduled')
    query = Inspection.query
    if current_user.role_id == 3:
        query = query.filter(Inspection.site_id == current_user.site_id)
    if status_filter in ('Scheduled', 'Completed'):
        query = query.filter(Inspection.status == status_filter)
    items = query.options(db.joinedload(Inspection.template)).order_by(Inspection.due_date.asc()).all()
    return render_template('inspections.html', inspections=items,
        current_path=request.path, current_page_name=current_page_name, status_filter=status_filter)


@routes_blueprint.route('/add_inspection', methods=['GET', 'POST'])
@login_required
def add_inspection():
    current_page_name = 'Schedule Inspection'
    is_staff()
    form = InspectionForm()
    site_ids = _visible_site_ids()
    form.template_id.choices = [(t.id, t.name) for t in InspectionTemplate.query.filter_by(is_active=True).order_by(InspectionTemplate.name).all()]
    form.facility_id.choices, form.room_id.choices, form.asset_id.choices = _location_choices(site_ids, with_none=True)
    form.inspector_id.choices = _assignee_choices(site_ids)

    if form.validate_on_submit():
        site_id, error = _validate_inspection_target(form)
        if error:
            flash(error, 'danger')
            return render_template('add_inspection.html', form=form, current_path=request.path, current_page_name=current_page_name)
        if not can_access_site(site_id):
            flash('You cannot schedule inspections for that site.', 'danger')
            return render_template('add_inspection.html', form=form, current_path=request.path, current_page_name=current_page_name)

        inspection = Inspection(
            template_id=form.template_id.data,
            site_id=site_id,
            facility_id=form.facility_id.data or None,
            room_id=form.room_id.data or None,
            asset_id=form.asset_id.data or None,
            due_date=form.due_date.data,
            inspector_id=form.inspector_id.data or None,
            notes=form.notes.data,
            created_by_id=current_user.id,
        )
        db.session.add(inspection)
        db.session.commit()
        flash('Inspection scheduled.', 'success')
        return redirect(url_for('routes.edit_inspection', inspection_id=inspection.id))

    return render_template('add_inspection.html', form=form,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_inspection/<int:inspection_id>')
@login_required
def edit_inspection(inspection_id):
    current_page_name = 'Inspection'
    inspection = Inspection.query.get_or_404(inspection_id)
    if not can_access_inspection(inspection):
        abort(403)

    results_form = InspectionResultsForm()
    active_items = [i for i in inspection.template.items if i.is_active]
    if inspection.status == 'Scheduled':
        for item in active_items:
            results_form.items.append_entry()
    results_by_item = {r.inspection_item_id: r for r in inspection.results}

    return render_template('edit_inspection.html', inspection=inspection, results_form=results_form,
        active_items=active_items, results_by_item=results_by_item, current_page_name=current_page_name)


@routes_blueprint.route('/record_inspection_results/<int:inspection_id>', methods=['POST'])
@login_required
def record_inspection_results(inspection_id):
    inspection = Inspection.query.get_or_404(inspection_id)
    if not can_access_inspection(inspection):
        abort(403)
    if inspection.status == 'Completed':
        flash('This inspection has already been completed.', 'danger')
        return redirect(url_for('routes.edit_inspection', inspection_id=inspection.id))

    active_items = [i for i in inspection.template.items if i.is_active]
    results_form = InspectionResultsForm()
    if len(results_form.items.entries) != len(active_items) or not results_form.validate_on_submit():
        flash('Every checklist item requires a result.', 'danger')
        return redirect(url_for('routes.edit_inspection', inspection_id=inspection.id))

    for item, entry in zip(active_items, results_form.items.entries):
        db.session.add(InspectionResult(
            inspection_id=inspection.id, inspection_item_id=item.id,
            result=entry.form.result.data, notes=entry.form.notes.data or None,
        ))
    inspection.status = 'Completed'
    inspection.completed_at = datetime.now(timezone.utc)
    db.session.flush()

    wo = None
    if results_form.generate_work_order.data:
        wo = inspections_module.generate_work_order_for_failures(inspection, current_user)
    db.session.commit()

    if inspection.failed_item_count:
        notifications_module.notify_inspection_failed(inspection)
        db.session.commit()

    if wo:
        flash(f'Inspection completed. Work order {wo.wo_number} generated for the failed item(s).', 'success')
    elif inspection.failed_item_count:
        flash('Inspection completed with failed items (no work order generated, as requested).', 'warning')
    else:
        flash('Inspection completed — all items passed.', 'success')
    return redirect(url_for('routes.edit_inspection', inspection_id=inspection.id))


@routes_blueprint.route('/delete_inspection/<int:inspection_id>', methods=['POST'])
@login_required
def delete_inspection(inspection_id):
    is_admin()
    inspection = Inspection.query.get_or_404(inspection_id)
    if inspection.status == 'Completed':
        flash('A completed inspection cannot be deleted — its results are historical record.', 'danger')
        return redirect(url_for('routes.inspections'))
    db.session.delete(inspection)
    db.session.commit()
    flash('Scheduled inspection removed.', 'warning')
    return redirect(url_for('routes.inspections'))


# ****************** Inspection Dashboard *******************************
@routes_blueprint.route('/inspection_dashboard')
@login_required
def inspection_dashboard():
    current_page_name = 'Inspections Due'
    is_staff()
    query = Inspection.query.filter_by(status='Scheduled').options(db.joinedload(Inspection.template))
    if current_user.role_id == 3:
        query = query.filter(Inspection.site_id == current_user.site_id)
    buckets = inspections_module.dashboard_buckets(query.all())
    return render_template('inspection_dashboard.html', buckets=buckets,
        current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** Vendors (M&O) **************************************
# *********************************************************************
# Vendor CRUD is admin-only, matching every other M&O reference-data list
# (Priority/Category/AssetType/MaintenancePlan/InspectionTemplate). The
# performance view is staff-viewable (Admin/Specialist/Technician) —
# technicians already see/enter cost fields on individual work orders via
# the staff WorkOrderForm, so an aggregate view over the same data is no
# more sensitive. "Delete" is a soft deactivate: WorkOrder.vendor_id is
# nullable, so nothing blocks deactivating a vendor with historical work
# orders — those work orders simply keep pointing at the (now inactive)
# vendor record, same as Facility/Room/Asset's soft-delete precedent.

@routes_blueprint.route('/vendors')
@login_required
def vendors():
    current_page_name = 'Manage Vendors'
    is_admin()
    items = Vendor.query.order_by(Vendor.name).all()
    return render_template('vendors.html', vendors=items,
        current_path=request.path, current_page_name=current_page_name,
        expiration_status=vendors_module.expiration_status)


@routes_blueprint.route('/add_vendor', methods=['GET', 'POST'])
@login_required
def add_vendor():
    current_page_name = 'New Vendor'
    is_admin()
    form = VendorForm()
    if form.validate_on_submit():
        if Vendor.query.filter_by(name=form.name.data).first():
            flash('A vendor with this name already exists.', 'danger')
            return render_template('add_vendor.html', form=form, current_path=request.path, current_page_name=current_page_name)
        vendor = Vendor(
            name=form.name.data, contact_name=form.contact_name.data, phone=form.phone.data,
            email=form.email.data, address=form.address.data,
            contract_start_date=form.contract_start_date.data, contract_end_date=form.contract_end_date.data,
            insurance_expiration=form.insurance_expiration.data, license_expiration=form.license_expiration.data,
            notes=form.notes.data, created_by_id=current_user.id,
        )
        db.session.add(vendor)
        db.session.commit()
        flash(f'Vendor "{vendor.name}" added.', 'success')
        return redirect(url_for('routes.vendors'))
    return render_template('add_vendor.html', form=form,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_vendor/<int:vendor_id>', methods=['GET', 'POST'])
@login_required
def edit_vendor(vendor_id):
    is_admin()
    vendor = Vendor.query.get_or_404(vendor_id)
    form = VendorForm(obj=vendor)
    if form.validate_on_submit():
        if Vendor.query.filter(Vendor.name == form.name.data, Vendor.id != vendor.id).first():
            flash('A vendor with this name already exists.', 'danger')
            return render_template('edit_vendor.html', form=form, vendor=vendor)
        vendor.name = form.name.data
        vendor.contact_name = form.contact_name.data
        vendor.phone = form.phone.data
        vendor.email = form.email.data
        vendor.address = form.address.data
        vendor.contract_start_date = form.contract_start_date.data
        vendor.contract_end_date = form.contract_end_date.data
        vendor.insurance_expiration = form.insurance_expiration.data
        vendor.license_expiration = form.license_expiration.data
        vendor.notes = form.notes.data
        vendor.is_active = form.is_active.data
        db.session.commit()
        flash('Vendor updated successfully!', 'success')
        return redirect(url_for('routes.vendors'))
    return render_template('edit_vendor.html', form=form, vendor=vendor)


@routes_blueprint.route('/delete_vendor/<int:vendor_id>', methods=['POST'])
@login_required
def delete_vendor(vendor_id):
    is_admin()
    vendor = Vendor.query.get_or_404(vendor_id)
    vendor.is_active = False
    db.session.commit()
    flash('Vendor deactivated successfully!', 'warning')
    return redirect(url_for('routes.vendors'))


@routes_blueprint.route('/vendor_performance')
@login_required
def vendor_performance():
    current_page_name = 'Vendor Performance'
    is_staff()
    site_ids = _visible_site_ids()

    rows = []
    for vendor in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all():
        query = WorkOrder.query.filter_by(vendor_id=vendor.id)
        if site_ids:
            query = query.filter(WorkOrder.site_id.in_(site_ids))
        stats = vendors_module.compute_vendor_stats(query.all())
        rows.append({
            'vendor': vendor,
            'stats': stats,
            'contract_status': vendors_module.expiration_status(vendor.contract_end_date),
            'insurance_status': vendors_module.expiration_status(vendor.insurance_expiration),
            'license_status': vendors_module.expiration_status(vendor.license_expiration),
        })

    return render_template('vendor_performance.html', rows=rows,
        current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** Projects & Capital Planning (M&O) ******************
# *********************************************************************
# List/detail (GET) is staff-viewable, site-scoped for Technicians, like
# work orders and the PM/Inspection dashboards. Mutations (create/edit/
# delete, and every sub-resource: tasks, costs, documents, vendor links) are
# admin-only, matching Priority/Category/MaintenancePlan/InspectionTemplate
# — capital planning is budget-facing structural data, not an operational
# record every technician edits day to day. "Delete" never removes a
# project — it moves status to Cancelled, since work orders/assets/costs
# may already point at it.

@routes_blueprint.route('/projects')
@login_required
def projects():
    current_page_name = 'Capital Projects'
    is_staff()
    status_filter = request.args.get('status_filter', 'active')
    query = Project.query
    site_ids = _visible_site_ids()
    if site_ids:
        query = query.filter(Project.site_id.in_(site_ids))
    if status_filter == 'active':
        query = query.filter(Project.status.notin_(('Completed', 'Cancelled')))
    elif status_filter in PROJECT_STATUSES:
        query = query.filter(Project.status == status_filter)
    items = query.order_by(Project.name).all()
    return render_template('projects.html', projects=items, statuses=PROJECT_STATUSES,
        status_filter=status_filter, current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/add_project', methods=['GET', 'POST'])
@login_required
def add_project():
    current_page_name = 'New Capital Project'
    is_admin()  # only Admins reach this route, so _visible_site_ids() is always unrestricted here
    form = ProjectForm()
    form.status.choices = [(s, s) for s in PROJECT_STATUSES]
    form.site_id.choices = [(s.id, s.site_name) for s in Site.query.order_by(Site.site_name).all()]

    if form.validate_on_submit():
        project = Project(
            site_id=form.site_id.data,
            name=form.name.data, description=form.description.data, status=form.status.data,
            budget=form.budget.data, start_date=form.start_date.data,
            target_end_date=form.target_end_date.data, actual_end_date=form.actual_end_date.data,
            created_by_id=current_user.id,
        )
        db.session.add(project)
        db.session.commit()
        flash(f'Project "{project.name}" created.', 'success')
        return redirect(url_for('routes.edit_project', project_id=project.id))

    return render_template('add_project.html', form=form,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_project/<int:project_id>', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    current_page_name = 'Capital Project'
    project = Project.query.get_or_404(project_id)
    if not can_access_site(project.site_id):
        abort(403)

    form = ProjectForm(obj=project)
    form.status.choices = [(s, s) for s in PROJECT_STATUSES]
    form.site_id.choices = [(s.id, s.site_name) for s in Site.query.order_by(Site.site_name).all()]
    task_form = ProjectTaskForm()
    task_form.assigned_to_id.choices = _assignee_choices(_visible_site_ids())
    cost_form = ProjectCostForm()
    cost_form.vendor_id.choices = [(0, '-- No vendor --')] + [
        (v.id, v.name) for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()]
    vendor_form = ProjectVendorForm()
    vendor_form.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.filter_by(is_active=True)
                                     .order_by(Vendor.name).all() if v not in project.vendors]

    if request.method == 'POST':
        is_admin()
        if form.validate_on_submit():
            project.site_id = form.site_id.data
            project.name = form.name.data
            project.description = form.description.data
            project.status = form.status.data
            project.budget = form.budget.data
            project.start_date = form.start_date.data
            project.target_end_date = form.target_end_date.data
            project.actual_end_date = form.actual_end_date.data
            project.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            flash('Project updated successfully!', 'success')
            return redirect(url_for('routes.edit_project', project_id=project.id))

    rollup = projects_module.project_cost_rollup(project)
    return render_template('edit_project.html', form=form, task_form=task_form, cost_form=cost_form,
        vendor_form=vendor_form, project=project, rollup=rollup, status_badge=workflow.STATUS_BADGE,
        current_page_name=current_page_name)


@routes_blueprint.route('/delete_project/<int:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    project.status = 'Cancelled'
    project.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash('Project cancelled.', 'warning')
    return redirect(url_for('routes.projects'))


@routes_blueprint.route('/add_project_task/<int:project_id>', methods=['POST'])
@login_required
def add_project_task(project_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    form = ProjectTaskForm()
    form.assigned_to_id.choices = _assignee_choices(_visible_site_ids())
    if form.validate_on_submit():
        db.session.add(ProjectTask(
            project_id=project.id, name=form.name.data, description=form.description.data,
            assigned_to_id=form.assigned_to_id.data or None, due_date=form.due_date.data,
            sort_order=form.sort_order.data or 0,
        ))
        db.session.commit()
        flash('Task added.', 'success')
    else:
        flash('A task name is required.', 'danger')
    return redirect(url_for('routes.edit_project', project_id=project.id))


@routes_blueprint.route('/toggle_project_task/<int:task_id>', methods=['POST'])
@login_required
def toggle_project_task(task_id):
    is_admin()
    task = ProjectTask.query.get_or_404(task_id)
    project_id = task.project_id
    if task.status == 'Completed':
        task.status = 'Not Started'
        task.completed_at = None
    else:
        task.status = 'Completed'
        task.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for('routes.edit_project', project_id=project_id))


@routes_blueprint.route('/delete_project_task/<int:task_id>', methods=['POST'])
@login_required
def delete_project_task(task_id):
    is_admin()
    task = ProjectTask.query.get_or_404(task_id)
    project_id = task.project_id
    db.session.delete(task)
    db.session.commit()
    flash('Task removed.', 'warning')
    return redirect(url_for('routes.edit_project', project_id=project_id))


@routes_blueprint.route('/add_project_cost/<int:project_id>', methods=['POST'])
@login_required
def add_project_cost(project_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    form = ProjectCostForm()
    form.vendor_id.choices = [(0, '-- No vendor --')] + [
        (v.id, v.name) for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()]
    if form.validate_on_submit():
        db.session.add(ProjectCost(
            project_id=project.id, description=form.description.data, cost_type=form.cost_type.data,
            amount=form.amount.data, vendor_id=form.vendor_id.data or None,
            incurred_date=form.incurred_date.data, notes=form.notes.data, created_by_id=current_user.id,
        ))
        db.session.commit()
        flash('Cost added.', 'success')
    else:
        flash('A description and amount are required.', 'danger')
    return redirect(url_for('routes.edit_project', project_id=project.id))


@routes_blueprint.route('/delete_project_cost/<int:cost_id>', methods=['POST'])
@login_required
def delete_project_cost(cost_id):
    is_admin()
    cost = ProjectCost.query.get_or_404(cost_id)
    project_id = cost.project_id
    db.session.delete(cost)
    db.session.commit()
    flash('Cost entry removed.', 'warning')
    return redirect(url_for('routes.edit_project', project_id=project_id))


@routes_blueprint.route('/add_project_vendor/<int:project_id>', methods=['POST'])
@login_required
def add_project_vendor(project_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    form = ProjectVendorForm()
    form.vendor_id.choices = [(v.id, v.name) for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()]
    if form.validate_on_submit():
        vendor = Vendor.query.get_or_404(form.vendor_id.data)
        if vendor not in project.vendors:
            project.vendors.append(vendor)
            db.session.commit()
            flash(f'{vendor.name} added to project.', 'success')
    return redirect(url_for('routes.edit_project', project_id=project.id))


@routes_blueprint.route('/remove_project_vendor/<int:project_id>/<int:vendor_id>', methods=['POST'])
@login_required
def remove_project_vendor(project_id, vendor_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    vendor = Vendor.query.get_or_404(vendor_id)
    if vendor in project.vendors:
        project.vendors.remove(vendor)
        db.session.commit()
        flash(f'{vendor.name} removed from project.', 'warning')
    return redirect(url_for('routes.edit_project', project_id=project.id))


@routes_blueprint.route('/download_project_document/<int:document_id>')
@login_required
def download_project_document(document_id):
    document = ProjectDocument.query.get_or_404(document_id)
    project = Project.query.get_or_404(document.project_id)
    if not can_access_site(project.site_id):
        abort(403)
    upload_folder = current_app.config['UPLOAD_PROJECT_DOCUMENT']
    if not os.path.exists(os.path.join(upload_folder, document.attach_file)):
        flash('File not found.', 'error')
        return redirect(url_for('routes.edit_project', project_id=project.id))
    return send_from_directory(upload_folder, document.attach_file, as_attachment=True)


@routes_blueprint.route('/upload_project_document/<int:project_id>', methods=['POST'])
@login_required
def upload_project_document(project_id):
    is_admin()
    project = Project.query.get_or_404(project_id)
    error = _save_attachment(request.files.get('document'), 'project', project.id,
                              'UPLOAD_PROJECT_DOCUMENT', ProjectDocument, 'project_id')
    if error:
        flash(error, 'danger')
    else:
        flash('Document uploaded.', 'success')
    return redirect(url_for('routes.edit_project', project_id=project.id))


@routes_blueprint.route('/delete_project_document/<int:document_id>', methods=['POST'])
@login_required
def delete_project_document(document_id):
    is_admin()
    document = ProjectDocument.query.get_or_404(document_id)
    project_id = document.project_id
    file_path = os.path.join(current_app.config['UPLOAD_PROJECT_DOCUMENT'], document.attach_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(document)
    db.session.commit()
    flash('Document removed.', 'warning')
    return redirect(url_for('routes.edit_project', project_id=project_id))


# ****************** Capital Replacement (Asset Risk Score) ***************
@routes_blueprint.route('/capital_replacement')
@login_required
def capital_replacement():
    current_page_name = 'Capital Replacement'
    is_staff()
    query = Asset.query.filter_by(is_active=True).options(
        db.joinedload(Asset.asset_type), db.joinedload(Asset.facility), db.joinedload(Asset.work_orders))
    site_ids = _visible_site_ids()
    if site_ids:
        query = query.filter(Asset.site_id.in_(site_ids))

    rows = []
    for asset in query.all():
        result = risk.calculate_risk(asset)
        rows.append({'asset': asset, 'risk': result})
    rows.sort(key=lambda r: (r['risk']['score'] is None, -(r['risk']['score'] or 0)))

    return render_template('capital_replacement.html', rows=rows,
        current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** M&O Dashboard (Phase 9) ****************************
# *********************************************************************
# One route, four role views (application/analytics.py). Admins and
# Specialists may switch between Executive and M&O Manager; Technicians get
# the Technician view pinned to their own work and site; everyone else gets
# the School Staff view pinned to their own requests and site. The legacy
# ticket dashboard at "/" is untouched.
@routes_blueprint.route('/dashboard')
@login_required
def mo_dashboard():
    current_page_name = 'M&O Dashboard'
    allowed = analytics.allowed_views(current_user)
    view = request.args.get('view') or analytics.default_view(current_user)
    if view not in allowed:
        view = analytics.default_view(current_user)

    site_ids = _visible_site_ids()
    filters = analytics.parse_filters(request.args, site_ids)
    data = analytics.build_dashboard(view, filters, current_user)

    options = {}
    if view in ('executive', 'manager'):
        fac_query = Facility.query.filter_by(is_active=True)
        if filters['site_ids']:
            fac_query = fac_query.filter(Facility.site_id.in_(filters['site_ids']))
        options['sites'] = Site.query.order_by(Site.site_name).all() if site_ids is None else []
        options['facilities'] = fac_query.order_by(Facility.name).all()
        options['technicians'] = User.query.filter(User.role_id.in_((2, 3)), User.status == 'Active') \
                                           .order_by(User.first_name, User.last_name).all()
        options['teams'] = [t for (t,) in db.session.query(WorkOrder.assigned_team).distinct()
                                                    .filter(WorkOrder.assigned_team.isnot(None)).order_by(WorkOrder.assigned_team).all()]
    options['categories'] = Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()
    options['priorities'] = Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()
    options['statuses'] = workflow.ALL_STATUSES

    return render_template('mo_dashboard.html', data=data, view=view, allowed_views=allowed,
        view_labels=analytics.VIEW_LABELS, filters=filters, presets=analytics.PRESETS,
        preset_labels=analytics.PRESET_LABELS, options=options, health_factors=analytics.HEALTH_FACTORS,
        status_badge=workflow.STATUS_BADGE, current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** Reports & Exports (Phase 10) ************************
# *********************************************************************
# Every report reuses an existing module's already-defined metric (risk.py,
# costs.py, vendors.py, analytics.py) — this section only adds a tabular,
# CSV-exportable view over them. Staff-viewable (Admin/Specialist/
# Technician), site-scoped for Technicians, matching every other M&O
# reporting/dashboard view. "?format=csv" on the same URL downloads the
# same rows the page just rendered (minus the on-screen DISPLAY_ROWS cap).
@routes_blueprint.route('/reports')
@login_required
def reports_index():
    current_page_name = 'Reports'
    is_staff()
    groups = {}
    for key in reports_module.REPORT_ORDER:
        entry = reports_module.REPORTS[key]
        groups.setdefault(entry['group'], []).append((key, entry['title']))
    return render_template('reports.html', groups=groups, current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/reports/<report_key>')
@login_required
def view_report(report_key):
    is_staff()
    entry = reports_module.REPORTS.get(report_key)
    if entry is None:
        abort(404)
    current_page_name = entry['title']

    site_ids = _visible_site_ids()
    filters = reports_module.parse_filters(request.args, site_ids, default_preset=entry['default_preset'])
    headers, rows, truncated = entry['rows'](filters)

    if request.args.get('format') == 'csv':
        csv_body = reports_module.to_csv(headers, rows)
        return current_app.response_class(
            csv_body, mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{report_key}.csv"'})

    options = {'facilities': [], 'technicians': [], 'vendors': [], 'assets': []}
    needed = entry['filters']
    if 'facility' in needed:
        fac_query = Facility.query.filter_by(is_active=True)
        if filters['site_ids']:
            fac_query = fac_query.filter(Facility.site_id.in_(filters['site_ids']))
        options['facilities'] = fac_query.order_by(Facility.name).all()
    if 'technician' in needed:
        options['technicians'] = User.query.filter(User.role_id.in_((2, 3)), User.status == 'Active') \
                                           .order_by(User.first_name, User.last_name).all()
    if 'vendor' in needed:
        options['vendors'] = Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()
    if 'asset' in needed:
        asset_query = Asset.query.filter_by(is_active=True)
        if filters['site_ids']:
            asset_query = asset_query.filter(Asset.site_id.in_(filters['site_ids']))
        if filters['facility_id']:
            asset_query = asset_query.filter(Asset.facility_id == filters['facility_id'])
        options['assets'] = asset_query.order_by(Asset.asset_tag).all()
    options['sites'] = Site.query.order_by(Site.site_name).all() if site_ids is None else []
    options['categories'] = Category.query.filter_by(is_active=True).order_by(Category.sort_order, Category.name).all()
    options['priorities'] = Priority.query.filter_by(is_active=True).order_by(Priority.sort_order).all()
    options['dimensions'] = costs_module.ROLLUP_DIMENSIONS
    options['dimension_labels'] = costs_module.ROLLUP_LABELS

    export_args = request.args.to_dict()
    export_args['format'] = 'csv'

    return render_template('report_view.html', report_key=report_key, entry=entry, headers=headers,
        rows=rows[:reports_module.DISPLAY_ROWS], total_rows=len(rows), truncated=truncated,
        display_cap=reports_module.DISPLAY_ROWS, filters=filters, presets=reports_module.PRESETS,
        preset_labels=reports_module.PRESET_LABELS, options=options, export_args=export_args,
        current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** SLA Rules (Phase 11) ********************************
# *********************************************************************
# One rule per Priority (get-or-create on edit — there's no separate "add"
# flow since the full set of priorities is small and fixed). Admin-only,
# matching every other reference-data CRUD (Priority/Category/Vendor/
# MaintenancePlan/InspectionTemplate).
@routes_blueprint.route('/sla_rules')
@login_required
def sla_rules():
    current_page_name = 'SLA Rules'
    is_admin()
    priorities = Priority.query.order_by(Priority.sort_order, Priority.name).all()
    return render_template('sla_rules.html', priorities=priorities,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/edit_sla_rule/<int:priority_id>', methods=['GET', 'POST'])
@login_required
def edit_sla_rule(priority_id):
    current_page_name = 'SLA Rule'
    is_admin()
    priority = Priority.query.get_or_404(priority_id)
    rule = SLARule.query.filter_by(priority_id=priority_id).first()
    form = SLARuleForm(obj=rule)
    if form.validate_on_submit():
        if rule is None:
            rule = SLARule(priority_id=priority_id)
            db.session.add(rule)
        rule.response_hours = form.response_hours.data
        rule.resolution_hours = form.resolution_hours.data
        rule.is_active = form.is_active.data
        db.session.commit()
        flash(f'SLA rule for "{priority.name}" saved.', 'success')
        return redirect(url_for('routes.sla_rules'))
    return render_template('edit_sla_rule.html', form=form, priority=priority, rule=rule,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/delete_sla_rule/<int:rule_id>', methods=['POST'])
@login_required
def delete_sla_rule(rule_id):
    is_admin()
    rule = SLARule.query.get_or_404(rule_id)
    db.session.delete(rule)
    db.session.commit()
    flash('SLA rule removed.', 'warning')
    return redirect(url_for('routes.sla_rules'))


# *********************************************************************
# ****************** Notification Preferences (Phase 11) *****************
# *********************************************************************
# Any authenticated user manages their own preferences — there's nothing
# admin-only about which alerts you personally receive.
@routes_blueprint.route('/notification_preferences', methods=['GET', 'POST'])
@login_required
def notification_preferences():
    current_page_name = 'Notification Preferences'
    pref = current_user.notification_preference
    form = NotificationPreferenceForm(obj=pref) if pref else NotificationPreferenceForm(
        **{event: True for event in NOTIFICATION_EVENTS})
    if form.validate_on_submit():
        if pref is None:
            pref = NotificationPreference(user_id=current_user.id)
            db.session.add(pref)
        for event in NOTIFICATION_EVENTS:
            setattr(pref, event, getattr(form, event).data)
        db.session.commit()
        flash('Notification preferences saved.', 'success')
        return redirect(url_for('routes.notification_preferences'))
    return render_template('notification_preferences.html', form=form, event_labels=NOTIFICATION_EVENT_LABELS,
        events=NOTIFICATION_EVENTS, current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** Global Search (Phase 11) *****************************
# *********************************************************************
@routes_blueprint.route('/search')
@login_required
@limiter.limit("60 per minute", key_func=get_remote_address)
def search():
    current_page_name = 'Search'
    query_text = request.args.get('q', '').strip()
    site_ids = _visible_site_ids()
    include_users = is_admin_bool()
    results = search_module.global_search(query_text, site_ids, include_users) if query_text else []
    return render_template('search.html', query_text=query_text, results=results,
        too_short=bool(query_text) and len(query_text) < search_module.MIN_QUERY_LENGTH,
        current_path=request.path, current_page_name=current_page_name)


def is_admin_bool():
    """Non-aborting admin check for places (like search scope) that need a
    plain True/False rather than is_admin()'s 403-on-failure behavior."""
    return current_user.is_authenticated and current_user.role_id == 1


# *********************************************************************
# ****************** CSV Import (Phase 11) ********************************
# *********************************************************************
# A separate, self-contained tool from the legacy /bulk-data-upload flow
# (Users+Sites, FTP-schedulable) — this one covers Facilities/Rooms/Assets/
# Vendors/Users with a downloadable per-type template, full pre-commit
# validation, and a row-by-row success/duplicate/error report. Admin-only,
# like every other data-management tool in this project.
@routes_blueprint.route('/csv_import')
@login_required
def csv_import_index():
    current_page_name = 'CSV Import'
    is_admin()
    recent = CsvImportLog.query.order_by(CsvImportLog.uploaded_at.desc()).limit(20).all()
    return render_template('csv_import.html', entities=csv_import_module.ENTITY_SPECS,
        entity_order=csv_import_module.ENTITY_ORDER, recent=recent,
        current_path=request.path, current_page_name=current_page_name)


@routes_blueprint.route('/csv_import/<entity_type>/template')
@login_required
def csv_import_template(entity_type):
    is_admin()
    if entity_type not in csv_import_module.ENTITY_SPECS:
        abort(404)
    text = csv_import_module.template_csv(entity_type)
    return current_app.response_class(text, mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{entity_type}_import_template.csv"'})


@routes_blueprint.route('/csv_import/<entity_type>', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per minute", key_func=get_remote_address)
def csv_import_upload(entity_type):
    current_page_name = 'CSV Import'
    is_admin()
    if entity_type not in csv_import_module.ENTITY_SPECS:
        abort(404)
    spec = csv_import_module.ENTITY_SPECS[entity_type]
    form = CsvImportUploadForm()

    if form.validate_on_submit():
        file = form.csv_file.data
        try:
            rows = list(csv.DictReader(file.stream.read().decode('utf-8-sig').splitlines()))
        except (UnicodeDecodeError, csv.Error):
            flash('Could not read that file as CSV.', 'danger')
            return redirect(url_for('routes.csv_import_upload', entity_type=entity_type))

        report, valid_data = csv_import_module.validate_rows(entity_type, rows)
        imported, commit_error = csv_import_module.commit_rows(entity_type, valid_data)
        duplicate_count = sum(1 for r in report if r['status'] == 'duplicate')
        error_count = sum(1 for r in report if r['status'] == 'error') + (len(valid_data) if commit_error else 0)

        db.session.add(CsvImportLog(
            entity_type=entity_type, filename=secure_filename(file.filename), uploaded_by_id=current_user.id,
            total_rows=len(rows), success_count=imported, duplicate_count=duplicate_count,
            error_count=error_count, status='error' if commit_error else 'success', error_message=commit_error,
        ))
        db.session.commit()

        if commit_error:
            flash(f'Import failed while saving — no rows were written: {commit_error}', 'danger')
        else:
            flash(f'Imported {imported} of {len(rows)} row(s). {duplicate_count} duplicate(s), '
                 f'{error_count} error(s) skipped.', 'success' if imported else 'warning')
        return render_template('csv_import_report.html', entity_type=entity_type, spec=spec, report=report,
            total_rows=len(rows), imported=imported, duplicate_count=duplicate_count, error_count=error_count,
            commit_error=commit_error, current_path=request.path, current_page_name=current_page_name)

    return render_template('csv_import_upload.html', entity_type=entity_type, spec=spec, form=form,
        current_path=request.path, current_page_name=current_page_name)


# *********************************************************************
# ****************** Audit Log (Phase 12) *********************************
# *********************************************************************
# Read-only. Rows are written automatically by application/audit.py's
# Session flush listener — no route in this file writes AuditLog by hand.
@routes_blueprint.route('/audit_log')
@login_required
def audit_log():
    current_page_name = 'Audit Log'
    is_admin()
    entity_type = request.args.get('entity_type') or None
    entity_id = request.args.get('entity_id', type=int)
    user_id = request.args.get('user_id', type=int)

    query = AuditLog.query
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.filter(AuditLog.entity_id == entity_id)
    if user_id is not None:
        query = query.filter(AuditLog.user_id.is_(None) if user_id == 0 else AuditLog.user_id == user_id)

    page, per_page, offset = get_page_args(page_parameter="page", per_page_parameter="per_page")
    total = query.count()
    entries = query.options(db.joinedload(AuditLog.user)).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()) \
                   .offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    from . import audit as audit_module
    return render_template('audit_log.html', entries=entries, pagination=pagination, per_page=per_page, total=total,
        entity_types=audit_module.TRACKED, entity_type=entity_type, entity_id=entity_id, user_id=user_id,
        users=User.query.order_by(User.first_name, User.last_name).all(),
        current_path=request.path, current_page_name=current_page_name)
