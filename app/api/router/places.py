"""Authenticated lookup of active places by identifier, name, or location."""

import uuid
from fastapi import APIRouter, Depends, status, Query, Request
from sqlmodel import select
from sqlalchemy import func

from app.api.deps import SessionDep, get_current_user
from app.models import Place
from app.core.rlimiter import limiter

router = APIRouter(prefix='/places', tags=['places', 'closest'])

@router.get("/", dependencies=[Depends(get_current_user)], status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_places(request: Request, session: SessionDep, place_id: uuid.UUID | None=None, name: str | None=None, country: uuid.UUID | None=None, city: uuid.UUID | None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List active places matching the supplied filters in name order."""
  query = select(Place).where(Place.is_active == True)
  if place_id:
    query = query.where(Place.id == place_id)
  if name:
    query = query.where(func.lower(Place.name) == name.strip().lower())
  if country:
    query = query.where(Place.country == country)
  if city:
    query = query.where(Place.city == city)
  
  places = session.exec(query.order_by(Place.name, Place.id).offset(offset).limit(limit)).all()
  return places
