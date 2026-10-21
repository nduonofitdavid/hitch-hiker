from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, status, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core import security
from app.core.rlimiter import limiter
from app.models import Token, UserPublic, Message, UserUpdate, NewPassword, UserCreate
from app.api.deps import SessionDep, CurrentUser, get_current_superuser
from app import crud
from app.utils import generate_password_reset_token, generate_reset_password_email, send_email, verify_password_reset_token, generate_new_account_email

router = APIRouter(prefix="/auth", tags=["auth", "authentication"])

@router.post("/sign-up", response_model=UserPublic)
@limiter.limit("5/minute")
def create_user(request: Request, session: SessionDep, payload: UserCreate):
  """Create account"""
  user = crud.get_user_by_email(session=session, email=payload.email)
  if user:
    raise HTTPException(detail="An account already exists with this email!", status_code=status.HTTP_400_BAD_REQUEST)
  user = crud.create_user(session=session, user_create=payload)
  
  if settings.emails_enabled and payload.email:
    email_data = generate_new_account_email(email_to=payload.email, username=payload.email, password=payload.password)
    send_email(email_to=payload.email, subject=email_data.subject, html_content=email_data.html_content)
    
  return user

@router.post('/login/access-token')
@limiter.limit("5/minute")
def login_user(request: Request, session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Token:
  """Login and receive access token"""
  user = crud.authenticate(session=session, email=form_data.username, password=form_data.password)
  if not user:
    raise HTTPException(status_code=400, detail="Incorrect email or password")
  elif not user.is_active:
    raise HTTPException(status_code=400, detail="Inactive user")
  
  access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRES_MINUTES)
  return Token(access_token=security.create_access_token(user.id, expires_delta=access_token_expires))

@router.post("/login/test-token", response_model=UserPublic)
@limiter.limit("5/minute")
def test_token(request: Request, current_user: CurrentUser) -> Any:
  """Test the bearer token"""
  return current_user

@router.post("/password-recovery/{email}")
@limiter.limit("5/minute")
def recover_password(request: Request, session: SessionDep, email: str) -> Message:
  """Recover lost password"""
  user = crud.get_user_by_email(session=session, email=email)

  if user:
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(email_to=user.email, email=email, token=password_reset_token)
    send_email(email_to=user.email, subject=email_data.subject, html_content=email_data.html_content)

  return Message(message="If you registered with an email, a recovery link has been set")

@router.post("/reset-password/")
@limiter.limit("5/minute")
def reset_password(request: Request, session: SessionDep, body: NewPassword) -> Message:
  """Reset password"""
  email = verify_password_reset_token(token=body.token)
  if not email:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
  
  user = crud.get_user_by_email(session=session, email=email)
  if not user:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
  elif not user.is_active:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")
  
  user_in_update = UserUpdate(password=body.new_password)
  crud.update_user(session=session, db_user=user, user_in=user_in_update)
  return Message(message="Password updated successfully")

@router.post("/password-recovery-html-content/{email}", dependencies=[Depends(get_current_superuser)], response_class=HTMLResponse)
@limiter.limit("5/minute")
def recover_password_html_content(request: Request, session: SessionDep, email: str) -> Any:
  """Password recovery that returns html response directly instead of sending to mail"""
  user = crud.get_user_by_email(session=session, email=email)
  if not user:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with the username provided exists in the system.")

  password_reset_token = generate_password_reset_token(email=email)
  email_data = generate_reset_password_email(email_to=user.email, email=email, token=password_reset_token)
  return HTMLResponse(content=email_data.html_content, headers={"subject:": email_data.subject})