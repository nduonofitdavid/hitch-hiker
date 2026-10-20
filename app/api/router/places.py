import uuid
from fastapi import APIRouter, Depends, status, Query, Request
from sqlmodel import select

from app.api.deps import SessionDep, get_current_user
from app.models import Place
from app.core.rlimiter import limiter

router = APIRouter(prefix='/places', tags=['places', 'closest'])

@router.get("/", dependencies=[Depends(get_current_user)], status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_places(request: Request, session: SessionDep, place_id: uuid.UUID | None=None, name: str | None=None, country: uuid.UUID | None=None, city: uuid.UUID | None=None, offset: int=0, limit: int=Query(default=100, le=100)):
  """Get places"""
  query = select(Place)
  if place_id:
    query = query.where(Place.id == place_id)
  if name:
    query = query.where(Place.name.ilike(name))
  if country:
    query = query.where(Place.country == country)
  if city:
    query = query.where(Place.city == city)
  
  places = session.exec(query.offset(offset).limit(limit)).all()
  return places
