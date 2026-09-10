from flask import current_app
from flask_mail import Message
from main import mail, db

def _is_mail_configured():
    """Return True only if the Organization has SMTP credentials saved."""
    from application.models import Organization
    org = db.session.get(Organization, 1)
    return bool(org and org.mail_server and org.mail_username)


def send_work_order_notification(event, work_order, **kwargs):
    """
    Email notifications for work order events.

    Events:
        'created'  - notifies the assignee if one was set at creation
        'assigned' - (kwargs: new_assignee) notifies the new assignee
        'status'   - (kwargs: old_status, new_status) notifies the requester
        'comment'  - (kwargs: commenter, comment_text) notifies requester + assignee, not the commenter
    Work orders with no requester (PM / Inspection / Manual) simply have fewer recipients.
    """
    if not _is_mail_configured():
        return
    try:
        requester = work_order.requester
        assignee = work_order.assigned_to
        label = f"{work_order.wo_number} – {work_order.title}"
        location = work_order.location_label
        footer = "\n\nPlease log in to Maintaink12 to view the work order.\n\n— Maintaink12"

        messages = []  # (recipient email, first name, subject, body)
        if event in ('created', 'assigned'):
            target = kwargs.get('new_assignee') if event == 'assigned' else assignee
            if target and target.email:
                messages.append((target.email, target.first_name,
                    f"Work Order Assigned to You: {work_order.wo_number}",
                    f"A work order has been assigned to you.\n\nWork Order: {label}\nLocation: {location}\n"
                    f"Priority: {work_order.priority.name}\nStatus: {work_order.status}"))
        elif event == 'status':
            if requester and requester.email:
                messages.append((requester.email, requester.first_name,
                    f"Your Work Order {work_order.wo_number} Status Changed",
                    f"The status of your request has been updated.\n\nWork Order: {label}\n"
                    f"Status: {kwargs.get('old_status')} → {kwargs.get('new_status')}"))
        elif event == 'comment':
            commenter = kwargs.get('commenter')
            commenter_name = commenter.get_full_name() if commenter else 'Someone'
            text = (kwargs.get('comment_text') or '').strip()
            for person in (requester, assignee):
                if person and person.email and (not commenter or person.id != commenter.id) \
                        and person.email not in [m[0] for m in messages]:
                    messages.append((person.email, person.first_name,
                        f"New Comment on Work Order {work_order.wo_number}",
                        f"{commenter_name} added a comment to {label}.\n\nComment:\n{text}"))

        for email, first_name, subject, body in messages:
            mail.send(Message(subject=subject, recipients=[email], body=f"Hi {first_name},\n\n{body}{footer}"))
            current_app.logger.info(f"Work order notification '{event}' sent to {email}")
    except Exception as e:
        current_app.logger.error(f"Failed to send work order notification (event={event}): {type(e).__name__}: {e}", exc_info=True)


def send_temp_password_email(user, temp_password):
    """Send a temporary password to a user and instruct them to change it on first login."""
    if not _is_mail_configured():
        current_app.logger.info("Temp password email skipped — no SMTP configuration saved.")
        return
    try:
        msg = Message(
            subject="Your Temporary Password — AssistITK12",
            recipients=[user.email],
            body=(
                f"Hi {user.first_name},\n\n"
                f"An administrator has reset your password. Use the temporary password below to log in.\n\n"
                f"Temporary Password: {temp_password}\n\n"
                f"You will be required to change your password immediately after logging in.\n\n"
                f"— AssistITK12 System"
            )
        )
        mail.send(msg)
        current_app.logger.info(f"Temporary password email sent to {user.email}")
    except Exception as e:
        current_app.logger.error(f"Failed to send temp password email to {user.email}: {type(e).__name__}: {e}", exc_info=True)
        raise


def send_password_updated_email(user):
    """Notify a user that their password was manually updated by an administrator."""
    if not _is_mail_configured():
        return
    try:
        msg = Message(
            subject="Your Password Has Been Updated — AssistITK12",
            recipients=[user.email],
            body=(
                f"Hi {user.first_name},\n\n"
                f"This is a confirmation that your password has been updated by an administrator.\n\n"
                f"If you did not expect this change, please contact your system administrator immediately.\n\n"
                f"— AssistITK12 System"
            )
        )
        mail.send(msg)
        current_app.logger.info(f"Password updated notification sent to {user.email}")
    except Exception as e:
        current_app.logger.error(f"Failed to send password updated email to {user.email}: {type(e).__name__}: {e}", exc_info=True)


def send_generic_notification(user, subject, body):
    """
    One reusable sender for application/notifications.py's eight alert
    categories (PM/inspection/vendor/asset/SLA) — mirrors the gate/log/catch
    shape of every function above rather than duplicating it eight times.
    """
    if not _is_mail_configured():
        return
    try:
        mail.send(Message(subject=subject, recipients=[user.email],
                          body=f"Hi {user.first_name},\n\n{body}\n\n— Maintaink12"))
        current_app.logger.info(f"Notification email sent to {user.email}: {subject}")
    except Exception as e:
        current_app.logger.error(f"Failed to send notification email to {user.email}: {type(e).__name__}: {e}", exc_info=True)
