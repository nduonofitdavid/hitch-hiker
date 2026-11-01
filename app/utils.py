"""Render notification emails, handle delivery, and create and verify password-reset tokens."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import emails
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jwt.exceptions import InvalidTokenError

from app.core import security
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_email_environment = Environment(loader=FileSystemLoader(Path(__file__).parent / "email-templates"), autoescape=select_autoescape(["html"]))

@dataclass
class EmailData:
  html_content: str
  subject: str

def render_email_template(*, template_name: str, context: dict[str, Any]) -> str:
  """Render an HTML email template with automatic escaping of context values."""
  return _email_environment.get_template(template_name).render(context)

def send_email(*, email_to: str, subject: str="", html_content: str="") -> None:
  """Send one configured SMTP message and raise if delivery is rejected."""
  if not settings.emails_enabled:
    raise RuntimeError("Email delivery is not configured")
  message = emails.message.Message(subject=subject, html=html_content, mail_from=(settings.EMAILS_FROM_NAME, settings.EMAILS_FROM_EMAIL))
  smtp_options = {"host": settings.SMTP_HOST, "port": settings.SMTP_PORT, "timeout": 10}
  if settings.SMTP_TLS:
    smtp_options["tls"] = True  
  elif settings.SMTP_SSL:
    smtp_options["ssl"] = True
  if settings.SMTP_USER:
    smtp_options["user"] = settings.SMTP_USER
  if settings.SMTP_PASSWORD:
    smtp_options["password"] = settings.SMTP_PASSWORD
    
  response = message.send(to=email_to, smtp=smtp_options)
  if response.status_code != 250:
    raise RuntimeError("Email delivery was rejected")

def send_email_safely(*, email_to: str, subject: str="", html_content: str="") -> bool:
  """Attempt email delivery with one connection retry and suppress delivery errors."""
  if not settings.emails_enabled:
    return False
  for attempt in range(2):
    try:
      send_email(email_to=email_to, subject=subject, html_content=html_content)
      return True
    except (OSError, TimeoutError) as exc:
      logger.warning("Email delivery attempt failed (%s)", type(exc).__name__)
      if attempt:
        return False
    except Exception as exc:
      logger.warning("Email delivery failed (%s)", type(exc).__name__)
      return False
  return False

def notify_by_email(*, email_to: str, prepare: Callable[[], EmailData]) -> bool:
  """Prepare and deliver a configured notification without failing its caller."""
  if not settings.emails_enabled:
    return False
  try:
    email_data = prepare()
  except Exception as exc:
    logger.warning("Email preparation failed (%s)", type(exc).__name__)
    return False
  return send_email_safely(email_to=email_to, subject=email_data.subject, html_content=email_data.html_content)

def generate_test_email(email_to: str) -> EmailData:
  """Build a test email for checking configured message delivery."""
  project_name = settings.PROJECT_NAME
  subject = f"{project_name} - Test email"
  html_context = render_email_template(template_name="test_email.html", context={"project_name": settings.PROJECT_NAME, "email": email_to})
  return EmailData(html_content=html_context, subject=subject)

def generate_reset_password_email(email_to: str, email: str, token: str) -> EmailData:
  """Build a password recovery email containing the supplied reset token."""
  project_name = settings.PROJECT_NAME
  subject = f"{project_name} - Password recovery for user {email}"
  link = f"{settings.FRONTEND_HOST}/reset-password?token={token}"
  html_content = render_email_template(template_name="reset_password.html", context={"project_name": settings.PROJECT_NAME, "username": email, "email": email_to, "valid_hours": settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS, "link": link})
  return EmailData(html_content=html_content, subject=subject)

def generate_new_account_email(email_to: str, username: str) -> EmailData:
  """Build a welcome email with the account name and application link."""
  project_name = settings.PROJECT_NAME
  subject = f"{project_name} - New account for user {username}"
  html_content = render_email_template(template_name="new_account.html", context={"project_name": settings.PROJECT_NAME, "username": username, "email": email_to, "link": settings.FRONTEND_HOST})
  return EmailData(html_content=html_content, subject=subject)

def generate_new_contributor_email(email_to: str, username: str) -> EmailData:
  """Build a registration email for a newly created contributor profile."""
  project_name = settings.PROJECT_NAME
  subject = f"{project_name} - New contributor account for user {username}"
  html_content = render_email_template(template_name="reg_contributor.html", context={"project_name": settings.PROJECT_NAME, "link": settings.FRONTEND_HOST})
  return EmailData(html_content=html_content, subject=subject)

def generate_contributor_approved_email() -> EmailData:
  """Build the notification that a contributor profile has been enabled."""
  html_content = render_email_template(template_name="approved_contributor.html", context={"project_name": settings.PROJECT_NAME, "link": settings.FRONTEND_HOST})
  return EmailData(html_content=html_content, subject=f"{settings.PROJECT_NAME} - Contributor approved")

def generate_password_reset_token(user_id: UUID, token_version: int) -> str:
  """Create an expiring reset token bound to the account's token version."""
  delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
  return security.create_token(user_id, delta, purpose="password_reset", token_version=token_version)

def verify_password_reset_token(token: str) -> dict[str, Any] | None:
  """Return validated password reset claims, or None when the token is invalid."""
  try:
    return security.decode_token(token, purpose="password_reset")
  except InvalidTokenError:
    return None
