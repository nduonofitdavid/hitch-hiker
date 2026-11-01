"""Hash passwords and create and validate signed authentication and password-reset tokens."""

from datetime import datetime, UTC, timedelta
from typing import Any
from uuid import UUID
import jwt
from jwt.exceptions import InvalidTokenError

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from .config import settings

password_hash = PasswordHash((Argon2Hasher(), BcryptHasher(),))

ALGORITHM = 'HS256'

def verify_password(plain_password: str, hashed_password: str) -> tuple[bool, str|None]:
  """Verify a password and return a replacement hash when an upgrade is needed."""
  return password_hash.verify_and_update(plain_password, hashed_password)

def get_hashed_password(password: str) -> str:
  """Hash a password using the preferred password hashing algorithm."""
  return password_hash.hash(password)

def create_token(subject: UUID | str, expires_delta: timedelta, *, purpose: str, token_version: int) -> str:
  """Sign an expiring token with its account, purpose, and revocation version."""
  now = datetime.now(UTC)
  to_encode = {"exp": now + expires_delta, "iat": now, "sub": str(UUID(str(subject))), "purpose": purpose, "ver": token_version}
  return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)

def create_access_token(subject: UUID | str, expires_delta: timedelta, *, token_version: int = 0) -> str:
  """Create a signed access token for the account's current token version."""
  return create_token(subject, expires_delta, purpose="access", token_version=token_version)

def decode_token(token: str, *, purpose: str) -> dict[str, Any]:
  """Validate a signed token's required claims and return its UUID subject."""
  try:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["exp", "iat", "sub", "purpose", "ver"]})
  except (TypeError, ValueError, OverflowError) as exc:
    raise InvalidTokenError("Invalid token claims") from exc
  if payload["purpose"] != purpose or type(payload["ver"]) is not int or payload["ver"] < 0:
    raise InvalidTokenError("Invalid token claims")
  try:
    payload["sub"] = UUID(payload["sub"])
  except (ValueError, TypeError, AttributeError) as exc:
    raise InvalidTokenError("Invalid token subject") from exc
  return payload
