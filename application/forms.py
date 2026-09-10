from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, TextAreaField, FieldList, FormField, BooleanField, RadioField, DateTimeField, IntegerField, DateField, DecimalField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange
from flask_wtf.file import FileField, FileRequired, FileAllowed
from datetime import datetime

class LoginForm(FlaskForm):
    email = StringField('Email:', validators=[DataRequired(), Email()])
    password = PasswordField('Password:', validators=[DataRequired()])
    submit = SubmitField('Login')


class UserForm(FlaskForm):
    first_name = StringField('First Name:', validators=[DataRequired()])
    middle_name = StringField('Middle Name:', validators=[Optional()])
    last_name = StringField('Last Name:', validators=[DataRequired()])
    email = StringField('Email:', validators=[DataRequired(), Email()])
    role_id = SelectField('Role:', coerce=int, choices=[], validators=[DataRequired()])
    site_id = SelectField('Site:', coerce=int, choices=[], validators=[DataRequired()])
    rm_num = StringField('Room:', validators=[Optional()])
    status = SelectField('Status:',
        choices=[('Active', 'Active'), ('Inactive', 'Inactive')],
        validators=[DataRequired()]    )
    password = PasswordField('New Password:', validators=[Optional(), Length(min=12)])
    submit = SubmitField('Save User')


class RoleForm(FlaskForm):
    role_name = StringField('Role Name:', validators=[DataRequired()])
    submit = SubmitField('Save Role')


class SiteForm(FlaskForm):
    site_name = StringField('Site Name:', validators=[DataRequired()])
    site_acronyms = StringField('Site Acronym:', validators=[DataRequired()])
    site_code = StringField('Site Code:', validators=[DataRequired()])
    site_cds = StringField('CDS Code:', validators=[DataRequired()])
    site_address = StringField('Site Address:', validators=[DataRequired()])
    site_city = StringField('City:', validators=[Optional(), Length(max=100)])
    site_state = StringField('State:', validators=[Optional(), Length(max=50)])
    site_zip = StringField('ZIP Code:', validators=[Optional(), Length(max=20)])
    site_type = StringField('Site Type:', validators=[DataRequired()])
    principal_first_name = StringField('Principal First Name:', validators=[Optional(), Length(max=100)])
    principal_last_name = StringField('Principal Last Name:', validators=[Optional(), Length(max=100)])
    principal_email = StringField('Principal Email:', validators=[Optional(), Email(), Length(max=255)])
    principal_phone = StringField('Principal Phone:', validators=[Optional(), Length(max=30)])
    submit = SubmitField('Save Site')


class NotificationForm(FlaskForm):
    msg_name = StringField('Message Name:', validators=[DataRequired()])
    msg_content = TextAreaField('Message:', validators=[DataRequired()])
    msg_status = RadioField('Status', choices=[('active', 'Active'), ('inactive', 'Inactive')], default='inactive', validators=[DataRequired()])
    submit = SubmitField('Save Notification Message')


class OrganizationForm(FlaskForm):
    organization_name = StringField('Organization Name', validators=[DataRequired()])
    site_version = StringField('Site Version', validators=[DataRequired()])
    submit = SubmitField('Save Settings')


class EmailConfigForm(FlaskForm):
    mail_server = StringField('SMTP Server', validators=[Optional()])
    mail_port = IntegerField('SMTP Port', validators=[Optional()])
    mail_use_tls = BooleanField('Use TLS (STARTTLS)')
    mail_use_ssl = BooleanField('Use SSL')
    mail_username = StringField('Username / Email', validators=[Optional()])
    mail_password = PasswordField('Password', validators=[Optional()])
    mail_default_sender = StringField('Default Sender Email', validators=[Optional(), Email()])
    submit_email = SubmitField('Save Email Settings')


class FacilityForm(FlaskForm):
    site_id = SelectField('Site:', coerce=int, choices=[], validators=[DataRequired()])
    name = StringField('Facility Name:', validators=[DataRequired(), Length(max=150)])
    facility_type = StringField('Facility Type:', validators=[Optional(), Length(max=100)])
    building_code = StringField('Building Code:', validators=[Optional(), Length(max=50)])
    address = StringField('Address:', validators=[Optional(), Length(max=255)])
    year_built = IntegerField('Year Built:', validators=[Optional()])
    square_footage = IntegerField('Square Footage:', validators=[Optional()])
    notes = TextAreaField('Notes:', validators=[Optional()])
    attachment = FileField('Attach Photo/Document')
    # Only rendered on edit_facility.html — add_facility.html leaves new
    # facilities at the column default (Active) by never touching this field.
    is_active = BooleanField('Active')
    submit = SubmitField('Save Facility')


class FloorForm(FlaskForm):
    name = StringField('Floor Name:', validators=[DataRequired(), Length(max=100)])
    sort_order = IntegerField('Sort Order:', validators=[Optional()], default=0)
    submit = SubmitField('Add Floor')


class RoomForm(FlaskForm):
    facility_id = SelectField('Facility:', coerce=int, choices=[], validators=[DataRequired()])
    floor_id = SelectField('Floor:', coerce=int, choices=[], validators=[Optional()])
    room_number = StringField('Room Number:', validators=[DataRequired(), Length(max=50)])
    room_name = StringField('Room Name:', validators=[Optional(), Length(max=150)])
    room_type = StringField('Room Type:', validators=[Optional(), Length(max=100)])
    capacity = IntegerField('Capacity:', validators=[Optional()])
    square_footage = IntegerField('Square Footage:', validators=[Optional()])
    notes = TextAreaField('Notes:', validators=[Optional()])
    attachment = FileField('Attach Photo/Document')
    # Only rendered on edit_room.html — see FacilityForm.is_active.
    is_active = BooleanField('Active')
    submit = SubmitField('Save Room')


class AssetTypeForm(FlaskForm):
    name = StringField('Type Name:', validators=[DataRequired(), Length(max=100)])
    category = StringField('Category:', validators=[Optional(), Length(max=100)])
    expected_life_years = IntegerField('Expected Life (years):', validators=[Optional(), NumberRange(min=0)])
    description = TextAreaField('Description:', validators=[Optional()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save Asset Type')


class AssetForm(FlaskForm):
    facility_id = SelectField('Facility:', coerce=int, choices=[], validators=[DataRequired()])
    room_id = SelectField('Room:', coerce=int, choices=[], validators=[Optional()])
    asset_type_id = SelectField('Asset Type:', coerce=int, choices=[], validators=[DataRequired()])
    asset_tag = StringField('Asset Tag:', validators=[DataRequired(), Length(max=50)])
    name = StringField('Asset Name:', validators=[DataRequired(), Length(max=150)])
    manufacturer = StringField('Manufacturer:', validators=[Optional(), Length(max=100)])
    model_number = StringField('Model Number:', validators=[Optional(), Length(max=100)])
    serial_number = StringField('Serial Number:', validators=[Optional(), Length(max=100)])
    install_date = DateField('Install Date:', validators=[Optional()])
    purchase_date = DateField('Purchase Date:', validators=[Optional()])
    purchase_cost = DecimalField('Purchase Cost:', places=2, validators=[Optional(), NumberRange(min=0)])
    warranty_expiration = DateField('Warranty Expiration:', validators=[Optional()])
    expected_life_years = IntegerField('Expected Life (years):', validators=[Optional(), NumberRange(min=0)])
    project_id = SelectField('Capital Project (optional):', coerce=int, choices=[], validators=[Optional()])
    notes = TextAreaField('Notes:', validators=[Optional()])
    attachment = FileField('Attach Photo/Document')
    # Only rendered on edit_asset.html — see FacilityForm.is_active.
    is_active = BooleanField('Active')
    submit = SubmitField('Save Asset')


class PriorityForm(FlaskForm):
    name = StringField('Priority Name:', validators=[DataRequired(), Length(max=50)])
    description = StringField('Description:', validators=[Optional(), Length(max=255)])
    sort_order = IntegerField('Sort Order (1 = most urgent):', validators=[Optional(), NumberRange(min=0)], default=0)
    color = SelectField('Badge Color:', choices=[
        ('danger', 'Red'), ('warning', 'Amber'), ('info', 'Blue'), ('primary', 'Navy'), ('secondary', 'Gray')
    ], default='secondary')
    is_active = BooleanField('Active')
    submit = SubmitField('Save Priority')


class CategoryForm(FlaskForm):
    name = StringField('Category Name:', validators=[DataRequired(), Length(max=100)])
    sort_order = IntegerField('Sort Order:', validators=[Optional(), NumberRange(min=0)], default=0)
    is_active = BooleanField('Active')
    submit = SubmitField('Save Category')


class SubcategoryForm(FlaskForm):
    name = StringField('Subcategory Name:', validators=[DataRequired(), Length(max=100)])
    submit = SubmitField('Add Subcategory')


class WorkOrderRequestForm(FlaskForm):
    """The requester-facing 'what / where / type / urgency / description / photo' flow."""
    title = StringField('What needs attention?', validators=[DataRequired(), Length(max=200)])
    facility_id = SelectField('Where? (building)', coerce=int, choices=[], validators=[DataRequired()])
    room_id = SelectField('Room', coerce=int, choices=[], validators=[Optional()])
    category_id = SelectField('Type of issue', coerce=int, choices=[], validators=[DataRequired()])
    priority_id = SelectField('Urgency', coerce=int, choices=[], validators=[DataRequired()])
    description = TextAreaField('Describe the problem', validators=[Optional()])
    attachment = FileField('Photo')
    submit = SubmitField('Submit Request')


class WorkOrderForm(FlaskForm):
    """Staff-facing full work order form (create Manual work orders, edit any)."""
    title = StringField('Title:', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description:', validators=[Optional()])
    site_id = SelectField('Site:', coerce=int, choices=[], validators=[DataRequired()])
    facility_id = SelectField('Facility:', coerce=int, choices=[], validators=[Optional()])
    room_id = SelectField('Room:', coerce=int, choices=[], validators=[Optional()])
    asset_id = SelectField('Asset:', coerce=int, choices=[], validators=[Optional()])
    category_id = SelectField('Category:', coerce=int, choices=[], validators=[DataRequired()])
    subcategory_id = SelectField('Subcategory:', coerce=int, choices=[], validators=[Optional()])
    priority_id = SelectField('Priority:', coerce=int, choices=[], validators=[DataRequired()])
    assigned_to_id = SelectField('Assign To:', coerce=int, choices=[], validators=[Optional()])
    assigned_team = StringField('Team:', validators=[Optional(), Length(max=100)])
    vendor_id = SelectField('Vendor:', coerce=int, choices=[], validators=[Optional()])
    project_id = SelectField('Capital Project (optional):', coerce=int, choices=[], validators=[Optional()])
    scheduled_date = DateField('Scheduled Date:', validators=[Optional()])
    due_date = DateField('Due Date:', validators=[Optional()])
    estimated_cost = DecimalField('Estimated Cost:', places=2, validators=[Optional(), NumberRange(min=0)])
    actual_cost = DecimalField('Actual Cost:', places=2, validators=[Optional(), NumberRange(min=0)])
    resolution = TextAreaField('Resolution:', validators=[Optional()])
    attachment = FileField('Attach Photo/Document')
    submit = SubmitField('Save Work Order')


class WorkOrderStatusForm(FlaskForm):
    new_status = SelectField('New Status:', choices=[], validators=[DataRequired()])
    note = StringField('Note:', validators=[Optional(), Length(max=255)])
    completed_at = DateField('Completion Date:', validators=[Optional()])
    resolution = TextAreaField('Resolution:', validators=[Optional()])
    submit = SubmitField('Update Status')


class WorkOrderCommentForm(FlaskForm):
    content = TextAreaField('Comment', validators=[DataRequired()])
    submit = SubmitField('Post Comment')


class MaintenancePlanForm(FlaskForm):
    name = StringField('Plan Name:', validators=[DataRequired(), Length(max=150)])
    asset_id = SelectField('Specific Asset:', coerce=int, choices=[], validators=[Optional()])
    asset_type_id = SelectField('Or: Every Asset of Type:', coerce=int, choices=[], validators=[Optional()])
    frequency = SelectField('Frequency:', choices=[], validators=[DataRequired()])
    custom_interval_days = IntegerField('Custom Interval (days):', validators=[Optional(), NumberRange(min=1)])
    start_date = DateField('First Due Date (optional):', validators=[Optional()])
    category_id = SelectField('Category:', coerce=int, choices=[], validators=[DataRequired()])
    priority_id = SelectField('Priority:', coerce=int, choices=[], validators=[DataRequired()])
    assigned_to_id = SelectField('Assign To:', coerce=int, choices=[], validators=[Optional()])
    assigned_team = StringField('Team:', validators=[Optional(), Length(max=100)])
    description = TextAreaField('Instructions:', validators=[Optional()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save Plan')


class InspectionTemplateForm(FlaskForm):
    name = StringField('Template Name:', validators=[DataRequired(), Length(max=150)])
    description = TextAreaField('Description:', validators=[Optional()])
    category_id = SelectField('Category (for generated work orders):', coerce=int, choices=[], validators=[DataRequired()])
    priority_id = SelectField('Priority (for generated work orders):', coerce=int, choices=[], validators=[DataRequired()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save Template')


class InspectionItemForm(FlaskForm):
    question = StringField('Checklist Question:', validators=[DataRequired(), Length(max=255)])
    sort_order = IntegerField('Sort Order:', validators=[Optional()], default=0)
    submit = SubmitField('Add Item')


class InspectionForm(FlaskForm):
    template_id = SelectField('Template:', coerce=int, choices=[], validators=[DataRequired()])
    facility_id = SelectField('Facility:', coerce=int, choices=[], validators=[Optional()])
    room_id = SelectField('Room:', coerce=int, choices=[], validators=[Optional()])
    asset_id = SelectField('Asset:', coerce=int, choices=[], validators=[Optional()])
    due_date = DateField('Due Date:', validators=[DataRequired()])
    inspector_id = SelectField('Inspector:', coerce=int, choices=[], validators=[Optional()])
    notes = TextAreaField('Notes:', validators=[Optional()])
    submit = SubmitField('Schedule Inspection')


class InspectionResultItemForm(FlaskForm):
    """One row of the results checklist — subform, not posted standalone."""
    result = RadioField('Result', choices=[(r, r) for r in ('Pass', 'Fail', 'Needs Attention')], validators=[DataRequired()])
    notes = StringField('Notes', validators=[Optional(), Length(max=255)])


class InspectionResultsForm(FlaskForm):
    items = FieldList(FormField(InspectionResultItemForm))
    generate_work_order = BooleanField('Generate a work order for any failed items', default=True)
    submit = SubmitField('Submit Inspection Results')


class ProjectForm(FlaskForm):
    site_id = SelectField('Site:', coerce=int, choices=[], validators=[DataRequired()])
    name = StringField('Project Name:', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description:', validators=[Optional()])
    status = SelectField('Status:', choices=[])
    budget = DecimalField('Budget:', places=2, validators=[Optional(), NumberRange(min=0)])
    start_date = DateField('Start Date:', validators=[Optional()])
    target_end_date = DateField('Target End Date:', validators=[Optional()])
    actual_end_date = DateField('Actual End Date:', validators=[Optional()])
    submit = SubmitField('Save Project')


class ProjectTaskForm(FlaskForm):
    name = StringField('Task Name:', validators=[DataRequired(), Length(max=200)])
    description = StringField('Description:', validators=[Optional(), Length(max=255)])
    assigned_to_id = SelectField('Assign To:', coerce=int, choices=[], validators=[Optional()])
    due_date = DateField('Due Date:', validators=[Optional()])
    sort_order = IntegerField('Sort Order:', validators=[Optional()], default=0)
    submit = SubmitField('Add Task')


class ProjectCostForm(FlaskForm):
    description = StringField('Description:', validators=[DataRequired(), Length(max=200)])
    cost_type = SelectField('Cost Type:', choices=[(t, t) for t in ('Design', 'Permit', 'Labor', 'Material', 'Contingency', 'Other')])
    amount = DecimalField('Amount:', places=2, validators=[DataRequired(), NumberRange(min=0)])
    vendor_id = SelectField('Vendor (optional):', coerce=int, choices=[], validators=[Optional()])
    incurred_date = DateField('Date Incurred:', validators=[Optional()])
    notes = StringField('Notes:', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Add Cost')


class ProjectVendorForm(FlaskForm):
    vendor_id = SelectField('Vendor:', coerce=int, choices=[], validators=[DataRequired()])
    submit = SubmitField('Add Vendor')


class AssetRiskFieldsForm(FlaskForm):
    """
    A small standalone form for setting Asset.safety_impact /
    operational_importance — the two Risk Score factors that have to be
    entered by an admin, not derived. -1 ("Not Assessed") is a sentinel the
    route converts to Python None — distinct from 0 ("None"), which is a
    real assessment ("we looked, this asset has no safety role"). Collapsing
    those two into one choice would make "never assessed" indistinguishable
    from "assessed as zero risk", silently excluding a real 0 from nothing
    and including an unassessed asset as if it were known-safe.
    """
    safety_impact = SelectField('Safety Impact:', coerce=int, choices=[
        (-1, '-- Not Assessed --'), (0, 'None'), (25, 'Low'), (50, 'Medium'), (75, 'High'), (100, 'Critical'),
    ])
    operational_importance = SelectField('Operational Importance:', coerce=int, choices=[
        (-1, '-- Not Assessed --'), (0, 'None'), (25, 'Low'), (50, 'Medium'), (75, 'High'), (100, 'Critical'),
    ])
    submit = SubmitField('Save Risk Factors')


class WorkOrderLaborForm(FlaskForm):
    technician_id = SelectField('Technician:', coerce=int, choices=[], validators=[DataRequired()])
    start_time = DateTimeField('Start Time (optional):', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    end_time = DateTimeField('End Time (optional):', format='%Y-%m-%dT%H:%M', validators=[Optional()])
    labor_hours = DecimalField('Labor Hours (auto-filled from start/end if both given):', places=2,
                               validators=[Optional(), NumberRange(min=0)])
    labor_type = SelectField('Labor Type:', choices=[(t, t) for t in ('Regular', 'Overtime', 'Emergency', 'Contractor')])
    hourly_rate = DecimalField('Hourly Rate:', places=2, validators=[Optional(), NumberRange(min=0)])
    notes = StringField('Notes:', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Add Labor Entry')


class WorkOrderMaterialForm(FlaskForm):
    description = StringField('Material / Part:', validators=[DataRequired(), Length(max=200)])
    quantity = DecimalField('Quantity:', places=2, default=1, validators=[DataRequired(), NumberRange(min=0)])
    unit_cost = DecimalField('Unit Cost:', places=2, default=0, validators=[DataRequired(), NumberRange(min=0)])
    vendor_id = SelectField('Vendor (optional):', coerce=int, choices=[], validators=[Optional()])
    notes = StringField('Notes:', validators=[Optional(), Length(max=255)])
    submit = SubmitField('Add Material')


class CostRecordForm(FlaskForm):
    """vendor_cost/other_cost are the only CostRecord fields entered directly — labor_cost
    and material_cost are always derived from the WorkOrderLabor/WorkOrderMaterial entries."""
    vendor_cost = DecimalField('Vendor Cost:', places=2, validators=[Optional(), NumberRange(min=0)])
    other_cost = DecimalField('Other Expenses:', places=2, validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField('Save Cost Adjustments')


class VendorForm(FlaskForm):
    name = StringField('Vendor Name:', validators=[DataRequired(), Length(max=150)])
    contact_name = StringField('Contact Name:', validators=[Optional(), Length(max=100)])
    phone = StringField('Phone:', validators=[Optional(), Length(max=30)])
    email = StringField('Email:', validators=[Optional(), Email(), Length(max=255)])
    address = StringField('Address:', validators=[Optional(), Length(max=255)])
    contract_start_date = DateField('Contract Start:', validators=[Optional()])
    contract_end_date = DateField('Contract End:', validators=[Optional()])
    insurance_expiration = DateField('Insurance Expiration:', validators=[Optional()])
    license_expiration = DateField('License Expiration:', validators=[Optional()])
    notes = TextAreaField('Notes:', validators=[Optional()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save Vendor')


class AssetConditionForm(FlaskForm):
    assessed_at = DateField('Assessment Date:', validators=[DataRequired()])
    score = IntegerField('Condition Score (0-100):', validators=[DataRequired(), NumberRange(min=0, max=100)])
    reason = StringField('Reason for Assessment:', validators=[Optional(), Length(max=255)])
    recommended_action = StringField('Recommended Action:', validators=[Optional(), Length(max=255)])
    notes = TextAreaField('Notes:', validators=[Optional()])
    photo = FileField('Photo')
    submit = SubmitField('Record Assessment')


class SLARuleForm(FlaskForm):
    response_hours = IntegerField('Response Target (hours):', validators=[DataRequired(), NumberRange(min=1)])
    resolution_hours = IntegerField('Resolution Target (hours):', validators=[DataRequired(), NumberRange(min=1)])
    is_active = BooleanField('Active', default=True)
    submit = SubmitField('Save SLA Rule')


class NotificationPreferenceForm(FlaskForm):
    pm_due = BooleanField('Preventive maintenance due today')
    pm_overdue = BooleanField('Preventive maintenance overdue')
    inspection_due = BooleanField('Inspection due today')
    inspection_failed = BooleanField('Inspection failed an item')
    vendor_contract_expiring = BooleanField('Vendor contract expiring/expired')
    asset_warranty_expiring = BooleanField('Asset warranty expiring/expired')
    sla_warning = BooleanField('Work order approaching its SLA deadline')
    sla_breach = BooleanField('Work order missed its SLA deadline')
    submit = SubmitField('Save Notification Preferences')


class CsvImportUploadForm(FlaskForm):
    csv_file = FileField('CSV File:', validators=[FileRequired(), FileAllowed(['csv'], 'CSV files only.')])
    submit = SubmitField('Validate & Import')