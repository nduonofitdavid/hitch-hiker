import uuid
from typing import Any

from sqlmodel import Session, select
from app.core.security import get_hashed_password, verify_password
from app.models import User, UserCreate, UserUpdate, City, Country, PlaceType

def create_user(*, session: Session, user_create: UserCreate):
  db_obj = User.model_validate(user_create, update={"hashed_password": get_hashed_password(user_create.password)})
  session.add(db_obj)
  session.commit()
  session.refresh(db_obj)
  return db_obj

def get_user_by_email(*, session: Session, email: str) -> User | None:
  user = session.exec(select(User).where(User.email == email)).first()
  return user

DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"

def authenticate(*, session: Session, email: str, password: str) -> User | None:
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
  user_data = user_in.model_dump(exclude_unset=True)
  extra_data = {}
  if "password" in user_data:
    password = user_data["password"]
    hashed_password = get_hashed_password(password)
    extra_data["hashed_password"] = hashed_password
  db_user.sqlmodel_update(user_data, update=extra_data)
  session.add(db_user)
  session.commit()
  session.refresh(db_user)
  return db_user

def retrieve_country(session: Session, country_id: uuid.UUID):
  country = session.exec(select(Country).where(Country.id == country_id)).first()
  return country

def retrieve_city(session: Session, city_id: uuid.UUID):
  city = session.exec(select(City).where(City.id == city_id)).first()
  return city

def retrieve_place_type(session: Session, place_t_id: uuid.UUID):
  place_type = session.exec(select(PlaceType).where(PlaceType.id == place_t_id)).first()
  return place_type