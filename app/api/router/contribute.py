import logging
import uuid
from fastapi import APIRouter, Depends, status, Query, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.deps import SessionDep, get_current_user, CurrentUser
from app.api.lib.sage_engine import loader
from app.models import Place, PlaceInUser, Contributor, PlaceType, ContributorRecord, ContributionStatus, ContributorAction, FlagPlace
from app.core.rlimiter import limiter

router = APIRouter(prefix='/contribute', tags=['contributor', 'contribute'])

def validate_contributor(session: SessionDep, contributor_id: uuid.UUID):  
  contributor = session.exec(select(Contributor).where(Contributor.user_id == contributor_id)).first()
  if not contributor:
    raise HTTPException(detail="sorry, looks like you dont have a contributor profile, create one to contribute.", status_code=status.HTTP_404_NOT_FOUND)
  if not contributor.is_enabled:
    raise HTTPException(detail="sorry, looks like your contribution profile has is pending activation, contact admin", status_code=status.HTTP_403_FORBIDDEN)

# contribute a stop


# contribute route

# contribute a place
@router.post("/add-place", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_place(request: Request, session: SessionDep, current_user: CurrentUser, payload: PlaceInUser):
  """Add a place"""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == current_user.id)).first()
  validate_contributor(session=session, contributor_id=contributor.id)

  if not loader.check_city(payload.city) or not loader.check_country(payload.country):
    raise HTTPException(detail="Ensure the country and city provided exist", status_code=status.HTTP_400_BAD_REQUEST)
  if payload.place_type:
    place_type = session.exec(select(PlaceType).where(PlaceType.id == payload.place_type)).first()
    if not place_type: 
      raise HTTPException(detail="The place type provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
    
  place = Place(
    name=payload.name, 
    description=payload.description, 
    is_active=False, 
    place_type=payload.place_type, 
    latitude=payload.latitude, 
    longitude=payload.longitude, 
    city=payload.city, 
    country=payload.country
  )

  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"Created place with named {payload.name}, with coordinates {payload.latitude}, {payload.longitude}")
  session.add(place)
  session.add(record)

  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception("Failed to add place %s", payload.name)
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sorry, this place already exist, check the details properly!")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured!")

  return {"detail": "The place has been added, pending admin approval", "place": place}

@router.put("/place/update/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def update_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, payload: PlaceInUser):
  """update a place"""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == current_user.id)).first()
  validate_contributor(session=session, contributor_id=contributor.id)

  if not loader.check_city(payload.city) or not loader.check_country(payload.country):
    raise HTTPException(detail="Ensure the country and city provided exist", status_code=status.HTTP_400_BAD_REQUEST)
  if payload.place_type:
    place_type = session.exec(select(PlaceType).where(PlaceType.id == payload.place_type)).first()
    if not place_type: 
      raise HTTPException(detail="The place type provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)

  place = session.exec(select(Place).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried updating does not exist", status_code=status.HTTP_404_NOT_FOUND)

  if not current_user.is_superuser:
    if not place.created_by == current_user.id:
      raise HTTPException(detail="You can only update a place that was created by you, flag it instead", status_code=status.HTTP_403_FORBIDDEN)

  place.name=payload.name
  place.country=payload.country
  place.city=payload.city
  place.latitude=payload.latitude
  place.longitude=payload.longitude
  place.place_type=payload.place_type
  place.description=payload.description
  place.is_active=False

  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.UPDATE, contribution_status=ContributionStatus.PENDING, description=f"Updated place with named {payload.name}, with coordinates {payload.latitude}, {payload.longitude}")
  session.add(place)
  session.add(record)

  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception("Failed to update place %s", payload.name)
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sorry, this place already exist, check the details properly!")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured!")

  return {"detail": "The place has been added, pending admin approval", "place": place}

@router.post('/place/flag/{place_id}', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def flag_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, reason: str = Query(max_length=128)):
  """opens a ticket for a place that has an issue"""
  place = session.exec(select(Place.id).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried flagging does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  flag = FlagPlace(flagged_by=current_user.id, reason=reason, place_id=place)
  session.add(flag)
  session.commit()

  return flag
