"""Account persistence, credential checks, and country, city, and place-type lookups."""

import uuid
from typing import Any

from sqlmodel import Session, select
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status
from app.core.security import get_hashed_password, verify_password
from app.models import User, UserCreate, UserUpdate, City, Country, PlaceType

def normalize_email(email: str) -> str:
  """Trim and lowercase an email address for consistent account matching."""
  return email.strip().lower()

def create_user(*, session: Session, user_create: UserCreate):
  """Create an account with a hashed password and reject duplicate emails."""
  db_obj = User.model_validate(user_create, update={"email": normalize_email(user_create.email), "hashed_password": get_hashed_password(user_create.password)})
  session.add(db_obj)
  try:
    session.commit()
  except IntegrityError:
    session.rollback()
    if get_user_by_email(session=session, email=user_create.email):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists with this email!")
    raise
  session.refresh(db_obj)
  return db_obj

def get_user_by_email(*, session: Session, email: str) -> User | None:
  """Find an account by its normalized, case-insensitive email address."""
  user = session.exec(select(User).where(func.lower(User.email) == normalize_email(email))).first()
  return user

DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"

def authenticate(*, session: Session, email: str, password: str) -> User | None:
  """Verify account credentials and persist any upgraded password hash."""
  db_user = get_user_by_email(session=session, email=email)
  if not db_user:
    verify_password(password, DUMMY_HASH)
    return None
  
  verified, updated_password_hash = verify_password(password, db_user.hashed_password)
  if not verified:
    return None 
  
  if updated_password_hash:
    db_user.hashed_password = updated_password_hash
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
  return db_user

def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
  """Update supplied account fields and revoke tokens after a password change."""
  user_data = user_in.model_dump(exclude_unset=True)
  extra_data = {}
  password = user_data.pop("password", None)
  if password is not None:
    hashed_password = get_hashed_password(password)
    extra_data["hashed_password"] = hashed_password
    extra_data["token_version"] = User.token_version + 1
  if user_data.get("email"):
    user_data["email"] = normalize_email(user_data["email"])
  
  db_user.sqlmodel_update(user_data, update=extra_data)
  session.add(db_user)
  
  session.commit()
  session.refresh(db_user)
  return db_user

def reset_password(*, session: Session, user_id: uuid.UUID, token_version: int, new_password: str) -> bool:
  """Atomically reset an active account's password if its token version matches."""
  result = session.execute(update(User).where(User.id == user_id, User.token_version == token_version, User.is_active == True).values(hashed_password=get_hashed_password(new_password), token_version=User.token_version + 1).execution_options(synchronize_session=False))
  if result.rowcount != 1:
    session.rollback()
    return False
  session.commit()
  return True

def retrieve_country(session: Session, country_id: uuid.UUID):
  """Return the country with the supplied identifier, if present."""
  country = session.exec(select(Country).where(Country.id == country_id)).first()
  return country

def retrieve_city(session: Session, city_id: uuid.UUID):
  """Return the city with the supplied identifier, if present."""
  city = session.exec(select(City).where(City.id == city_id)).first()
  return city

def retrieve_place_type(session: Session, place_t_id: uuid.UUID):
  """Return the place type with the supplied identifier, if present."""
  place_type = session.exec(select(PlaceType).where(PlaceType.id == place_t_id)).first()
  return place_type
