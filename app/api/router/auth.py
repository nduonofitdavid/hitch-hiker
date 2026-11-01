"""Account registration, access-token login, and password recovery endpoints."""

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, status, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core import security
from app.core.rlimiter import limiter
from app.models import Token, UserPublic, Message, NewPassword, UserRegister, UserCreate
from app.api.deps import SessionDep, CurrentUser, get_current_superuser
from app import crud
from app.utils import generate_password_reset_token, generate_reset_password_email, notify_by_email, verify_password_reset_token, generate_new_account_email

router = APIRouter(prefix="/auth", tags=["auth", "authentication"])

@router.post("/sign-up", response_model=UserPublic)
@limiter.limit("5/minute")
def create_user(request: Request, session: SessionDep, payload: UserRegister):
  """Create account"""
  user = crud.get_user_by_email(session=session, email=payload.email)
  if user:
    raise HTTPException(detail="An account already exists with this email!", status_code=status.HTTP_409_CONFLICT)
  user = crud.create_user(session=session, user_create=UserCreate(email=payload.email, full_name=payload.full_name, password=payload.password))
  
  notify_by_email(email_to=user.email, prepare=lambda: generate_new_account_email(email_to=user.email, username=user.email))
    
  return user

@router.post('/login/access-token')
@limiter.limit("5/minute")
def login_user(request: Request, session: SessionDep, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Token:
  """Login and receive access token"""
  user = crud.authenticate(session=session, email=form_data.username, password=form_data.password)
  if not user:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
  elif not user.is_active:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
  
  access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRES_MINUTES)
  return Token(access_token=security.create_access_token(user.id, expires_delta=access_token_expires, token_version=user.token_version))

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

  if user and user.is_active:
    notify_by_email(email_to=user.email, prepare=lambda: generate_reset_password_email(
      email_to=user.email, email=user.email, token=generate_password_reset_token(user_id=user.id, token_version=user.token_version)))

  return Message(message="If an eligible account exists, a recovery link will be sent")

@router.post("/reset-password/")
@limiter.limit("5/minute")
def reset_password(request: Request, session: SessionDep, body: NewPassword) -> Message:
  """Reset password"""
  payload = verify_password_reset_token(token=body.token)
  if not payload:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
  
  if not crud.reset_password(session=session, user_id=payload["sub"], token_version=payload["ver"], new_password=body.new_password):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
  return Message(message="Password updated successfully")

@router.post("/password-recovery-html-content/{email}", dependencies=[Depends(get_current_superuser)], response_class=HTMLResponse)
@limiter.limit("5/minute")
def recover_password_html_content(request: Request, session: SessionDep, email: str) -> Any:
  """Password recovery that returns html response directly instead of sending to mail"""
  user = crud.get_user_by_email(session=session, email=email)
  if not user:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with the username provided exists in the system.")

  password_reset_token = generate_password_reset_token(user_id=user.id, token_version=user.token_version)
  email_data = generate_reset_password_email(email_to=user.email, email=email, token=password_reset_token)
  return HTMLResponse(content=email_data.html_content, headers={"subject": email_data.subject})
