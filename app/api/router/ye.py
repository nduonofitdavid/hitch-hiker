import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import select

from app.api.deps import SessionDep, get_current_user
from app.models import Stops, City, Country, StopsOut
from app.core.rlimiter import limiter

router = APIRouter(prefix='/ye', tags=["ye"])

@router.get("/stops", dependencies=[Depends(get_current_user),])
@limiter.limit("5/minute")
def get_stops(request: Request, session: SessionDep, stop_id: uuid.UUID | None=None, country: uuid.UUID|None=None, city: uuid.UUID|None=None, offset: int=0, limit: int = Query(default=100, le=100)):
  """Get the stops in the database"""
  query = select(Stops).where(Stops.is_active == True)
  if stop_id:
    query = query.where(Stops.id == stop_id)
  if country:
    query = query.where(Stops.country == country)
  if city:
    query = query.where(Stops.city == city)
  stops = session.exec(query.offset(offset).limit(limit)).all()
  return stops  

@router.get("/city", dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_city(request: Request, session: SessionDep, name: str|None=None,  city_id: uuid.UUID | None=None, country_id: uuid.UUID | None=None, offset: int=0, limit: int =Query(default=100, le=100)):
  """Get the cities in the database"""
  query = select(City)
  if city_id:
    query = query.where(City.id == city_id)
  if country_id:
    query = query.where(City.country == country_id)
  if name:
    query = query.where(City.name.ilike(name))
  results = session.exec(query.offset(offset).limit(limit)).all()
  return results

@router.get("/country", dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_country(request: Request, session: SessionDep, country_id: uuid.UUID | None=None, name: str | None=None, offset: int=0, limit: int=Query(default=100, le=100)):
  """Get the countries in the database"""
  query = select(Country)
  if country_id:
    query = query.where(Country.id == country_id)
  if name:
    query = query.where(Country.name.ilike(name))
  results = session.exec(query.offset(offset).limit(limit)).all()
  return results
