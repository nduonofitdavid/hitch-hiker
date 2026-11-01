"""Authenticated catalogue lookups for stops, cities, countries, transport modes, and place types."""

import uuid
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import select
from sqlalchemy import func

from app.api.deps import SessionDep, get_current_user
from app.models import Stops, City, Country, TransportMode, TransportModeOut, PlaceType
from app.core.rlimiter import limiter

router = APIRouter(prefix='/ye', tags=["ye"])

@router.get("/stops", dependencies=[Depends(get_current_user),])
@limiter.limit("5/minute")
def get_stops(request: Request, session: SessionDep, stop_id: uuid.UUID | None=None, country: uuid.UUID|None=None, city: uuid.UUID|None=None, offset: int=Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
  """List active stops matching the supplied location filters in name order."""
  query = select(Stops).where(Stops.is_active == True)
  if stop_id:
    query = query.where(Stops.id == stop_id)
  if country:
    query = query.where(Stops.country == country)
  if city:
    query = query.where(Stops.city == city)
  stops = session.exec(query.order_by(Stops.name, Stops.id).offset(offset).limit(limit)).all()
  return stops  

@router.get("/city", dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_city(request: Request, session: SessionDep, name: str|None=None,  city_id: uuid.UUID | None=None, country_id: uuid.UUID | None=None, offset: int=Query(default=0, ge=0), limit: int =Query(default=100, ge=1, le=100)):
  """List cities matching a country, identifier, or literal case-insensitive name."""
  query = select(City)
  if city_id:
    query = query.where(City.id == city_id)
  if country_id:
    query = query.where(City.country == country_id)
  if name:
    query = query.where(func.lower(City.name) == name.strip().lower())
  results = session.exec(query.order_by(City.name, City.id).offset(offset).limit(limit)).all()
  return results

@router.get("/country", dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_country(request: Request, session: SessionDep, country_id: uuid.UUID | None=None, name: str | None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List countries matching an identifier or literal case-insensitive name."""
  query = select(Country)
  if country_id:
    query = query.where(Country.id == country_id)
  if name:
    query = query.where(func.lower(Country.name) == name.strip().lower())
  results = session.exec(query.order_by(Country.name, Country.id).offset(offset).limit(limit)).all()
  return results

@router.get("/transport-modes", response_model=list[TransportModeOut], dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_transport_modes(request: Request, session: SessionDep, name: str|None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List transport modes optionally matching a literal case-insensitive name."""
  query = select(TransportMode)
  if name is not None:
    query = query.where(func.lower(TransportMode.name) == name.strip().lower())
  return session.exec(query.order_by(TransportMode.name, TransportMode.id).offset(offset).limit(limit)).all()

@router.get("/place-types", dependencies=[Depends(get_current_user)])
@limiter.limit("5/minute")
def get_place_types(request: Request, session: SessionDep, name: str|None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List place types optionally matching a literal case-insensitive name."""
  query = select(PlaceType)
  if name is not None:
    query = query.where(func.lower(PlaceType.name) == name.strip().lower())
  return session.exec(query.order_by(PlaceType.name, PlaceType.id).offset(offset).limit(limit)).all()
