"""Shared FastAPI dependencies for database sessions and account authorization."""

from sqlmodel import Session
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from collections.abc import Generator
from typing import Annotated

from jwt.exceptions import InvalidTokenError

from app.core.db import engine
from app.core.config import settings
from app.core.security import decode_token

from app.models import User

reuasable_oauth2 = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login/access-token")

def get_db() -> Generator[Session]:
  """Provide a database session scoped to the current request."""
  with Session(engine) as session:
    yield session

SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reuasable_oauth2)]

def get_current_user(session: SessionDep, token: TokenDep):
  """Authenticate an active account using an unrevoked access token."""
  try:
    payload = decode_token(token, purpose="access")
  except InvalidTokenError:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"})

  user = session.get(User, payload["sub"])
  if not user or user.token_version != payload["ver"]:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
  if not user.is_active:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
  return user

CurrentUser = Annotated[User, Depends(get_current_user)]

def get_current_superuser(current_user: CurrentUser) -> User:
  """Require the authenticated account to have administrator privileges."""
  if not current_user.is_superuser:
    raise HTTPException(status_code=403, detail="The user doesn't have enough priviledge")
  return current_user

AdminDep = Annotated[User, Depends(get_current_superuser)]
