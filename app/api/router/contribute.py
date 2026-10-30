import logging
import uuid
from fastapi import APIRouter, status, Query, Request, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.deps import SessionDep, CurrentUser
from app.api.lib.sage_engine import loader
from app.models import Place, PlaceInUser, Contributor, PlaceType, ContributorRecord, ContributionStatus, ContributorAction, FlagPlace, get_datetime_utc, StopsIn, Stops, PlaceInUser, RouteIn, Route, TransportMode, FlagStop, FlagRoute, PendingPlace
from app.core.rlimiter import limiter
from app.core.config import settings
from app.utils import generate_new_contributor_email, send_email
from app.crud import retrieve_place_type, retrieve_city, retrieve_country

router = APIRouter(prefix='/contribute', tags=['contributor', 'contribute'])

def validate_contributor(session: SessionDep, user_id: uuid.UUID) -> Contributor:  
  """validate that a user is a contributor"""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == user_id)).first()
  if not contributor:
    raise HTTPException(detail="sorry, looks like you dont have a contributor profile, create one to contribute.", status_code=status.HTTP_404_NOT_FOUND)
  if not contributor.is_enabled:
    raise HTTPException(detail="sorry, looks like your contribution profile has is pending activation, contact admin", status_code=status.HTTP_403_FORBIDDEN)
  return contributor

# register to be a contributor
@router.post("/register/{user_id}", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def register_contributor(request: Request, session: SessionDep, current_user: CurrentUser):
  """register as a contributor"""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == current_user.id)).first()
  if contributor:
    raise HTTPException(detail="This user is already registered as a contributor", status_code=status.HTTP_400_BAD_REQUEST)
  contributor = Contributor(user_id=current_user.id, date_joined=get_datetime_utc())
  session.add(contributor)
  session.commit()
  session.refresh(contributor)

  if settings.emails_enabled and current_user.email:
    email_data = generate_new_contributor_email(email_to=current_user.email, username=current_user.email)
    send_email(email_to=current_user.email, subject=email_data.subject, html_content=email_data.html_content)
  
  session.refresh(contributor)
  return contributor

# contribute a stop
@router.post("/stop/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_stop(request: Request, session: SessionDep, current_user: CurrentUser, payload: StopsIn):
  """add a stop"""
  contributor = validate_contributor(session=session, user_id=current_user.id)

  country = retrieve_country(session=session, country_id=payload.country)
  city = retrieve_city(session=session, city_id=payload.city)
  if city == None or country == None:
    raise HTTPException(detail="Ensure the country and city provided exist", status_code=status.HTTP_400_BAD_REQUEST)

  stop = Stops(name=payload.name, latitude=payload.latitude, longitude=payload.longitude, is_active=False, city=payload.city, country=payload.country, contributed_by=current_user.id)
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"Created stop {payload.name}")
  session.add(stop)
  session.add(record)
  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception("Failed to add stop %s", payload.name)
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sorry, this stop already exist, check the details properly!")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured!")
  
  session.refresh(stop)
  return stop

@router.post("/stop/flag/{stop_id}", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def flag_stop(request: Request, session: SessionDep, current_user: CurrentUser, stop_id: uuid.UUID,  reason: str = Query(min_length=15, max_length=128)):
  stop_id_store = session.exec(select(Stops.id).where(Stops.id == stop_id)).first()
  if not stop_id_store:
    raise HTTPException(detail="The place you tried flagging does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  flag = FlagStop(flagged_by=current_user.id, reason=reason, stop_id=stop_id_store)
  session.add(flag)
  session.commit()
  session.refresh(flag)

  return flag
  
# contribute route
@router.post("/route/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_route(request: Request, session: SessionDep, current_user: CurrentUser, payload: RouteIn):
  """add a route"""
  contributor = validate_contributor(session=session, user_id=current_user.id)

  if payload.start not in loader.stop_cache:
    raise HTTPException(detail="The start location provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
  if payload.end not in loader.stop_cache:
    raise HTTPException(detail="The end location provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
  if payload.time <= 0.0 or payload.price <= 0.0:
    raise HTTPException(detail="The price or time provided cannot be 0 or less", status_code=status.HTTP_400_BAD_REQUEST)
  
  transport_modes = session.exec(select(TransportMode).where(TransportMode.id.in_(payload.transport_mode))).all()
  if len(transport_modes) != len(payload.transport_mode):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A transport mode provided does not exist!")
  
  route = Route(start=payload.start, end=payload.end, is_active=False, time=payload.time, price=payload.price, contributed_by=current_user.id, transport_modes=transport_modes)
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"created route from {loader.stop_cache[payload.start].name} to {loader.stop_cache[payload.end].name}")
  session.add(route)
  session.add(record)
  session.commit()
  session.refresh(route)
  
  return route

@router.post("/route/flag/{route_id}", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def flag_route(request: Request, session: SessionDep, current_user: CurrentUser, route_id: uuid.UUID, reason: str = Query(min_length=15, max_length=128)):
  """flag a route"""
  route_id_store = session.exec(select(Route.id).where(Route.id == route_id)).first()
  if not route_id_store:
    raise HTTPException(detail="The place you tried flagging does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  flag = FlagRoute(flagged_by=current_user.id, reason=reason, route_id=route_id_store)
  session.add(flag)
  session.commit()
  session.refresh(flag)

  return flag

# contribute a place
@router.post("/place/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_place(request: Request, session: SessionDep, current_user: CurrentUser, payload: PlaceInUser):
  """Add a place"""
  contributor = validate_contributor(session=session, user_id=current_user.id)

  country = retrieve_country(session=session, country_id=payload.country)
  city = retrieve_city(session=session, city_id=payload.city)
  if country == None or city == None:
    raise HTTPException(detail="Ensure the country and city provided exist", status_code=status.HTTP_400_BAD_REQUEST)
  
  if payload.place_type:
    place_type = session.exec(select(PlaceType).where(PlaceType.id == payload.place_type)).first()
    if not place_type: 
      raise HTTPException(detail="The place type provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
    
  place = Place(name=payload.name, description=payload.description, is_active=False, place_type=payload.place_type, created_by=current_user.id, latitude=payload.latitude, longitude=payload.longitude, city=payload.city, country=payload.country)

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
  session.refresh(place)
  return place

@router.put("/place/update/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def update_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, payload: PlaceInUser):
  """update a place"""
  contributor = validate_contributor(session=session, user_id=current_user.id)
  country = retrieve_country(session=session, country_id=payload.country)
  city = retrieve_city(session=session, city_id=payload.city)
  if country == None or city == None:
    raise HTTPException(detail="The city or country provided is not valid, check again", status_code=status.HTTP_400_BAD_REQUEST)
  
  del country
  del city

  if payload.place_type:
    place_type = retrieve_place_type(session=session, place_t_id=payload.place_type)
    if not place_type: 
      raise HTTPException(detail="The place type provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)

  place = session.exec(select(Place).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried updating does not exist", status_code=status.HTTP_404_NOT_FOUND)

  if not place.created_by == current_user.id:
    raise HTTPException(detail="You can only update a place that was created by you, flag it instead", status_code=status.HTTP_403_FORBIDDEN)

  new_place = PendingPlace(place_id=place.id, name=payload.name, place_type=payload.place_type, latitude=payload.latitude, longitude=payload.longitude, country=payload.country, city=payload.country, description=payload.description)

  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.UPDATE, contribution_status=ContributionStatus.PENDING, description=f"Updated place with named {place.name}")
  session.add(new_place)
  session.add(record)
  session.commit()
  session.refresh(new_place)

  return new_place

@router.post('/place/flag/{place_id}', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def flag_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, reason: str = Query(min_length=15, max_length=128)):
  """opens a ticket for a place that has an issue"""
  place = session.exec(select(Place.id).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried flagging does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  flag = FlagPlace(flagged_by=current_user.id, reason=reason, place_id=place)
  session.add(flag)
  session.commit()
  session.refresh(flag)
  return flag
