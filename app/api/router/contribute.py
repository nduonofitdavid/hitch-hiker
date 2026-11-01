"""Contributor registration, catalogue proposals, flags, and archival requests."""

import uuid
from fastapi import APIRouter, status, Query, Request, HTTPException
from sqlmodel import select

from app.api.deps import SessionDep, CurrentUser
from app.api.validation import validate_geography, validate_route, validate_place, commit_changes, FLAG_TARGETS, CONTRIBUTION_TARGETS
from app.models import Place, PlaceInUser, Contributor, ContributorRecord, ContributionStatus, ContributorAction, get_datetime_utc, StopsIn, Stops, RouteIn, Route, RouteOut, PendingPlaceOut
from app.core.rlimiter import limiter
from app.utils import generate_new_contributor_email, notify_by_email

router = APIRouter(prefix='/contribute', tags=['contributor', 'contribute'])

def submit_flag(session: SessionDep, user, target_type: str, target_id: uuid.UUID, reason: str):
  """Create and return a pending flag for an existing catalogue target."""
  flag_model, target_model, target_field = FLAG_TARGETS[target_type]
  if session.get(target_model, target_id) is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The place you tried flagging does not exist!")
  flag = flag_model(flagged_by=user.id, reason=reason, **{target_field: target_id})
  session.add(flag)
  commit_changes(session)
  session.refresh(flag)
  return flag

def validate_contributor(session: SessionDep, user_id: uuid.UUID) -> Contributor:  
  """Lock and return the user's enabled contributor profile."""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == user_id).with_for_update()).first()
  if not contributor:
    raise HTTPException(detail="sorry, looks like you dont have a contributor profile, create one to contribute.", status_code=status.HTTP_404_NOT_FOUND)
  if not contributor.is_enabled:
    raise HTTPException(detail="sorry, looks like your contribution profile has is pending activation, contact admin", status_code=status.HTTP_403_FORBIDDEN)
  return contributor

# register to be a contributor
@router.post("/register", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def register_contributor(request: Request, session: SessionDep, current_user: CurrentUser):
  """Create a disabled contributor profile and attempt its notification email."""
  contributor = session.exec(select(Contributor).where(Contributor.user_id == current_user.id)).first()
  if contributor:
    raise HTTPException(detail="This user is already registered as a contributor", status_code=status.HTTP_409_CONFLICT)
  
  contributor = Contributor(user_id=current_user.id, date_joined=get_datetime_utc())
  session.add(contributor)
  commit_changes(session)
  session.refresh(contributor)

  notify_by_email(email_to=current_user.email, prepare=lambda: generate_new_contributor_email(email_to=current_user.email, username=current_user.email))
  return contributor

# contribute a stop
@router.post("/stop/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_stop(request: Request, session: SessionDep, current_user: CurrentUser, payload: StopsIn):
  """Submit an inactive stop with its recorded proposal for administrative review."""
  contributor = validate_contributor(session=session, user_id=current_user.id)
  validate_geography(session, payload.city, payload.country)

  stop = Stops(name=payload.name, latitude=payload.latitude, longitude=payload.longitude, is_active=False, city=payload.city, country=payload.country, contributed_by=current_user.id)
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"Created stop {payload.name}", target_type="stop", target_id=stop.id, proposed_changes=payload.model_dump(mode="json"))
  session.add(stop)
  session.add(record)
  commit_changes(session)
  
  session.refresh(stop)
  return stop

@router.post("/stop/flag/{stop_id}", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def flag_stop(request: Request, session: SessionDep, current_user: CurrentUser, stop_id: uuid.UUID,  reason: str = Query(min_length=15, max_length=128)):
  """Create a pending flag for an existing stop."""
  return submit_flag(session, current_user, "stop", stop_id, reason)
  
# contribute route
@router.post("/route/add", status_code=status.HTTP_201_CREATED, response_model=RouteOut)
@limiter.limit("5/minute")
def add_route(request: Request, session: SessionDep, current_user: CurrentUser, payload: RouteIn):
  """Submit an inactive validated route with its proposal for review."""
  contributor = validate_contributor(session=session, user_id=current_user.id)
  transport_modes = validate_route(session, payload)
  
  route = Route(start=payload.start, end=payload.end, is_active=False, time=payload.time, price=payload.price, contributed_by=current_user.id, transport_modes=transport_modes)
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"Created route {payload.start} to {payload.end}", target_type="route", target_id=route.id, proposed_changes=payload.model_dump(mode="json"))
  session.add(route)
  session.add(record)
  commit_changes(session)
  session.refresh(route)
  
  return route

@router.post("/route/flag/{route_id}", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def flag_route(request: Request, session: SessionDep, current_user: CurrentUser, route_id: uuid.UUID, reason: str = Query(min_length=15, max_length=128)):
  """Create a pending flag for an existing route."""
  return submit_flag(session, current_user, "route", route_id, reason)

# contribute a place
@router.post("/place/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def add_place(request: Request, session: SessionDep, current_user: CurrentUser, payload: PlaceInUser):
  """Submit an inactive place with its recorded proposal for review."""
  contributor = validate_contributor(session=session, user_id=current_user.id)
  validate_place(session, payload)
    
  place = Place(name=payload.name, description=payload.description, is_active=False, place_type=payload.place_type, created_by=current_user.id, latitude=payload.latitude, longitude=payload.longitude, city=payload.city, country=payload.country)
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.CREATE, contribution_status=ContributionStatus.PENDING, description=f"Created place named {payload.name}, with coordinates {payload.latitude}, {payload.longitude}", target_type="place", target_id=place.id, proposed_changes=payload.model_dump(mode="json"))
  session.add(place)
  session.add(record)

  commit_changes(session)
  session.refresh(place)
  return place

@router.put("/place/update/{place_id}", status_code=status.HTTP_200_OK, response_model=PendingPlaceOut)
@limiter.limit("5/minute")
def update_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, payload: PlaceInUser):
  """Submit a pending edit to an owned place without changing published data."""
  contributor = validate_contributor(session=session, user_id=current_user.id)
  validate_place(session, payload)

  place = session.exec(select(Place).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried updating does not exist", status_code=status.HTTP_404_NOT_FOUND)

  if not place.created_by == current_user.id:
    raise HTTPException(detail="You can only update a place that was created by you, flag it instead", status_code=status.HTTP_403_FORBIDDEN)

  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.UPDATE, contribution_status=ContributionStatus.PENDING, description=f"Updated place named {place.name}", target_type="place", target_id=place.id, proposed_changes=payload.model_dump(mode="json"), original_data=PlaceInUser.model_validate(place).model_dump(mode="json"), pending_place_id=uuid.uuid4())
  session.add(record)
  commit_changes(session)
  session.refresh(record)
  return PendingPlaceOut.from_record(record, current_user.id)

@router.post('/place/flag/{place_id}', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def flag_place(request: Request, session: SessionDep, current_user: CurrentUser, place_id: uuid.UUID, reason: str = Query(min_length=15, max_length=128)):
  """Create a pending flag for an existing place."""
  return submit_flag(session, current_user, "place", place_id, reason)

@router.post("/{target_type}/{target_id}/request-deletion", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def request_deletion(request: Request, session: SessionDep, current_user: CurrentUser, target_type: str, target_id: uuid.UUID, reason: str = Query(min_length=15, max_length=256)):
  """Request archival of an owned submission while preserving its history."""
  contributor = validate_contributor(session, current_user.id)
  model = CONTRIBUTION_TARGETS.get(target_type)
  if model is None:
    raise HTTPException(status_code=400, detail="Use stop, route, or place")
  target = session.get(model, target_id)
  if target is None:
    raise HTTPException(status_code=404, detail="Submission not found")
  
  owner = target.created_by if isinstance(target, Place) else target.contributed_by
  if owner != current_user.id:
    raise HTTPException(status_code=403, detail="Request deletion only for your own submissions; flag other records")
  
  record = ContributorRecord(contributor_id=contributor.id, action_type=ContributorAction.REQUEST_DELETION, contribution_status=ContributionStatus.PENDING, description=reason, target_type=target_type, target_id=target.id, original_data=target.model_dump(mode="json"))
  session.add(record)
  commit_changes(session)
  session.refresh(record)
  return record
