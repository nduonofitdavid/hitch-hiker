"""Administrator endpoints for catalogue maintenance, trip inspection, and audit records."""

import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, Query, Request, Path
from sqlmodel import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.api.deps import SessionDep, AdminDep, Depends, get_current_superuser
from app.models import Country, City, StopsIn, Stops, Records, AdminAction, Route, RouteIn, RouteOut, TransportMode, CityBase, TripStore, PlaceInAdmin, Place, PlaceType, LookupName, PlaceTypeIn
from app.api.validation import validate_geography, validate_place, validate_route, commit_changes, stage_audit, city_has_pending_edits
from app.core.rlimiter import limiter
from app.crud import retrieve_country

router = APIRouter(prefix='/admin', tags=['admin', 'admin_user'])

def validate_name(session, model, name, record_id=None, country_id=None):
  """Trim a name and reject case-insensitive duplicates within its scope."""
  name = name.strip()
  if not name:
    raise HTTPException(status_code=422, detail="Name cannot be blank")

  query = select(model.id).where(func.lower(model.name) == name.lower())
  if record_id is not None:
    query = query.where(model.id != record_id)
  if country_id is not None:
    query = query.where(model.country == country_id)
  if session.exec(query).first() is not None:
    raise HTTPException(status_code=409, detail="This name already exists")
  return name

# stops
@router.post("/stops/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_stop(request: Request, session: SessionDep, admin_user: AdminDep, payload: StopsIn):
  """Create an active stop and publish its routing revision atomically."""
  city, country = validate_geography(session, payload.city, payload.country)
  stop = Stops(**payload.model_dump())
  session.add(stop)
  stage_audit(session, admin_user, f"Created stop {stop.id} at {city.name}, {country.name}", action=AdminAction.CREATED)

  commit_changes(session, graph_changed=True)
  session.refresh(stop)
  return stop

@router.put("/stops/update/{stop_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_stop(request: Request, session: SessionDep, stop_id: uuid.UUID, payload: StopsIn, admin_user: AdminDep):
  """Update a locked stop and publish its routing revision atomically."""
  validate_geography(session, payload.city, payload.country)
  
  stop = session.exec(select(Stops).where(Stops.id == stop_id).with_for_update()).first()
  if not stop:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The stop you tried updating does not exist!")

  stop.sqlmodel_update(payload.model_dump())
  stage_audit(session, admin_user, f"Edited stop {stop.id}")
  commit_changes(session, graph_changed=True)
  session.refresh(stop)
  return stop

# routes
@router.get("/routes", response_model=list[RouteOut], dependencies=[Depends(get_current_superuser)])
@limiter.limit("10/minute")
def get_routes(request: Request, session: SessionDep, route_id: uuid.UUID|None = None, start_id: uuid.UUID|None=None, end_id: uuid.UUID|None=None, transport_modes: list[uuid.UUID]|None=Query(default=None), offset: int=Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
  """List routes matching the supplied filters in a stable order."""
  query = select(Route)
  if route_id:
    query = query.where(Route.id == route_id)
  if start_id:
    query = query.where(Route.start == start_id)
  if end_id:
    query = query.where(Route.end == end_id)
  if transport_modes:
    query = query.where(Route.transport_modes.any(TransportMode.id.in_(transport_modes)))
  
  routes = session.exec(query.order_by(Route.id).offset(offset).limit(limit)).all()
  return routes

@router.post("/routes/add", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_route(request: Request, session: SessionDep, admin_user: AdminDep, payload: RouteIn):
  """Create a validated route and publish its routing revision atomically."""
  transport_modes = validate_route(session, payload)
  route = Route(start=payload.start, end=payload.end, time=payload.time, price=payload.price, transport_modes=transport_modes)
  session.add(route)
  stage_audit(session, admin_user, f"Created route {route.id}: {route.start} -> {route.end}", action=AdminAction.CREATED)
  
  commit_changes(session, graph_changed=True)
  session.refresh(route)
  return route

@router.put("/routes/update/{route_id}", response_model=RouteOut, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_route(request: Request, session: SessionDep, route_id: uuid.UUID, payload: RouteIn, admin_user: AdminDep):
  """Update a locked route after validating its endpoints and modes."""
  if session.get(Route, route_id) is None:
    raise HTTPException(detail="The route provided does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  transport_modes = validate_route(session, payload)
  route = session.exec(select(Route).where(Route.id == route_id).with_for_update().execution_options(populate_existing=True)).first()
  if route is None:
    raise HTTPException(detail="The route provided does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  route.start = payload.start
  route.end = payload.end
  route.time = payload.time
  route.price = payload.price
  route.transport_modes = transport_modes

  session.add(route)
  stage_audit(session, admin_user, f"Edit on route {route.id}")

  commit_changes(session, graph_changed=True)
  session.refresh(route)
  return route

# trip
@router.get("/trips", dependencies=[Depends(get_current_superuser)], status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def get_trips_admin(request: Request, session: SessionDep, trip_id: uuid.UUID | None=None, user_id: uuid.UUID|None=None, price: float|None=Query(default=None, ge=0, allow_inf_nan=False), distance: float|None=Query(default=None, ge=0, allow_inf_nan=False), country: uuid.UUID | None=None, city: uuid.UUID | None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List saved trips matching the supplied administrative filters."""
  query = select(TripStore)
  if trip_id:
    query = query.where(TripStore.id == trip_id)
  if user_id:
    query = query.where(TripStore.user_id == user_id)
  if price is not None:
    query = query.where(TripStore.price == price)
  if distance is not None:
    query = query.where(TripStore.distance == distance)
  if country:
    query = query.where(TripStore.country == country)
  if city:
    query = query.where(TripStore.city == city)
  
  trips = session.exec(query.order_by(TripStore.carried_out_at.desc(), TripStore.id).offset(offset).limit(limit)).all()
  return trips

# records
@router.get("/records", dependencies=[Depends(get_current_superuser)], status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def get_records(request: Request, session: SessionDep, record_id: uuid.UUID | None=None, action: AdminAction | None=None, performed_by: uuid.UUID | None=None, performed_at: datetime | None=None, offset: int=Query(default=0, ge=0), limit: int=Query(default=100, ge=1, le=100)):
  """List administrative audit records matching the supplied filters."""
  query = select(Records)
  if record_id:
    query = query.where(Records.id == record_id)
  if action is not None:
    query = query.where(Records.action == action)
  if performed_by:
    query = query.where(Records.performed_by == performed_by)
  if performed_at:
    query = query.where(Records.performed_at == performed_at)
  
  records = session.exec(query.order_by(Records.performed_at.desc(), Records.id).offset(offset).limit(limit)).all()
  return records

# country
@router.post("/country/add/{name}", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_country(request: Request, session: SessionDep, admin_user: AdminDep, name: str=Path(min_length=1, max_length=128)):
  """Create a uniquely named country and record the administrative action."""
  name = validate_name(session, Country, name)
  country = Country(name=name)
  session.add(country)
  stage_audit(session, admin_user, f"Created country {name}", action=AdminAction.CREATED)

  commit_changes(session, graph_changed=True)
  session.refresh(country)
  return country

@router.put("/country/update/{country_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_country(request: Request, session: SessionDep, admin_user: AdminDep, country_id: uuid.UUID, name: str=Query(min_length=1, max_length=128)):
  """Rename a country and record the administrative action."""
  country = session.exec(select(Country).where(Country.id == country_id)).first()
  if not country:
    raise HTTPException(detail="The country you tried editing was not found", status_code=status.HTTP_404_NOT_FOUND)
  
  name = validate_name(session, Country, name, record_id=country_id)
  country.name = name
  session.add(country)
  stage_audit(session, admin_user, f"Edited country: {name}")

  commit_changes(session, graph_changed=True)
  session.refresh(country)
  return country

# city
@router.post("/city/add/", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_city(request: Request, session: SessionDep, admin_user: AdminDep, payload: CityBase):
  """Create a city with a name unique within its country."""
  country = session.exec(select(Country.id).where(Country.id == payload.country)).first()
  if not country:
    raise HTTPException(detail="The country you provided for was not found", status_code=status.HTTP_400_BAD_REQUEST)
  
  name = validate_name(session, City, payload.name, country_id=payload.country)
  city = City(name=name, country=country)
  session.add(city)
  stage_audit(session, admin_user, f"Created city {payload.name}", action=AdminAction.CREATED)
  
  commit_changes(session)
  session.refresh(city)
  return city

@router.put("/city/update/{city_id}")
@limiter.limit("10/minute")
def edit_city(request: Request, session: SessionDep, admin_user: AdminDep, city_id: uuid.UUID, payload: CityBase):
  """Update a locked city while preventing country changes with linked records."""
  country = retrieve_country(session=session, country_id=payload.country)
  if not country:
    raise HTTPException(detail="The country provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
  city = session.exec(select(City).where(City.id == city_id).with_for_update()).first()
  if not city:
    raise HTTPException(detail="The city does not exist!", status_code=status.HTTP_404_NOT_FOUND)

  if city.country != payload.country:
    for model in (Stops, Place, TripStore):
      if session.exec(select(model.id).where(model.city == city_id).limit(1)).first() is not None:
        raise HTTPException(status_code=409, detail="A city with linked records cannot move to another country")
    if city_has_pending_edits(session, city_id):
      raise HTTPException(status_code=409, detail="A city with linked records cannot move to another country")

  name = validate_name(session, City, payload.name, record_id=city_id, country_id=payload.country)
  city.name = name
  city.country = payload.country
  session.add(city)
  stage_audit(session, admin_user, f"Edited city, {city.name}")

  commit_changes(session, graph_changed=True)
  session.refresh(city)
  return city
    
# places api
@router.post("/places/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_place(request: Request, session: SessionDep, admin_user: AdminDep, payload: PlaceInAdmin):
  """Create a place with validated geography and its administrator as creator."""
  validate_place(session, payload)

  place = Place(**payload.model_dump(), created_by=admin_user.id)
  session.add(place)
  stage_audit(session, admin_user, f"Created place {payload.name}", action=AdminAction.CREATED)

  commit_changes(session)
  session.refresh(place)
  return place

@router.put("/stops/active/{stop_id}")
@limiter.limit("10/minute")
def set_stop_active(request: Request, session: SessionDep, admin_user: AdminDep, stop_id: uuid.UUID, is_active: bool):
  """Set a locked stop's availability and publish the routing revision."""
  stop = session.exec(select(Stops).where(Stops.id == stop_id).with_for_update()).first()
  if stop is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The stop was not found")
  stop.is_active = is_active
  stage_audit(session, admin_user, f"Set stop {stop.id} is_active to {is_active}")

  commit_changes(session, graph_changed=True)
  session.refresh(stop)
  return stop

@router.put("/routes/active/{route_id}", response_model=RouteOut)
@limiter.limit("10/minute")
def set_route_active(request: Request, session: SessionDep, admin_user: AdminDep, route_id: uuid.UUID, is_active: bool):
  """Set route availability after validating active endpoints and concurrent changes."""
  route = session.get(Route, route_id)
  if route is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The route was not found")
  captured = (route.start, route.end, frozenset(mode.id for mode in route.transport_modes))

  if is_active:
    validate_route(session, RouteIn(start=route.start, end=route.end, time=route.time, price=route.price, transport_mode=[mode.id for mode in route.transport_modes]))
  route = session.exec(select(Route).where(Route.id == route_id).options(selectinload(Route.transport_modes)).with_for_update().execution_options(populate_existing=True)).first()

  if route is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The route was not found")
  if is_active and captured != (route.start, route.end, frozenset(mode.id for mode in route.transport_modes)):
    raise HTTPException(status_code=409, detail="The route changed while being enabled; retry the request")
  route.is_active = is_active

  stage_audit(session, admin_user, f"Set route {route.id} is_active to {is_active}")

  commit_changes(session, graph_changed=True)
  session.refresh(route)
  return route

# transport modes
@router.post("/transport-modes/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_transport_mode(request: Request, session: SessionDep, admin_user: AdminDep, payload: LookupName):
  """Create a uniquely named transport mode and publish the routing revision."""
  name = validate_name(session, TransportMode, payload.name)
  mode = TransportMode(name=name)
  session.add(mode)
  stage_audit(session, admin_user, f"Created transport mode {mode.id}: {name}", action=AdminAction.CREATED)

  commit_changes(session, graph_changed=True)
  session.refresh(mode)
  return mode

@router.put("/transport-modes/update/{mode_id}")
@limiter.limit("10/minute")
def update_transport_mode(request: Request, session: SessionDep, admin_user: AdminDep, mode_id: uuid.UUID, payload: LookupName):
  """Rename a transport mode and publish the routing revision."""
  mode = session.get(TransportMode, mode_id)
  if mode is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The transport mode was not found")

  mode.name = validate_name(session, TransportMode, payload.name, record_id=mode_id)
  stage_audit(session, admin_user, f"Edited transport mode {mode_id}: {mode.name}")

  commit_changes(session, graph_changed=True)
  session.refresh(mode)
  return mode

# place types
@router.post("/place-types/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_place_type(request: Request, session: SessionDep, admin_user: AdminDep, payload: PlaceTypeIn):
  """Create a uniquely named place type and record the administrative action."""
  name = validate_name(session, PlaceType, payload.name)
  place_type = PlaceType(name=name, description=payload.description)
  session.add(place_type)
  stage_audit(session, admin_user, f"Created place type {place_type.id}: {name}", action=AdminAction.CREATED)

  commit_changes(session)
  session.refresh(place_type)
  return place_type

@router.put("/place-types/update/{place_type_id}")
@limiter.limit("10/minute")
def update_place_type(request: Request, session: SessionDep, admin_user: AdminDep, place_type_id: uuid.UUID, payload: PlaceTypeIn):
  """Update a place type's name and description with an audit record."""
  place_type = session.get(PlaceType, place_type_id)
  if place_type is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The place type was not found")

  place_type.name = validate_name(session, PlaceType, payload.name, record_id=place_type_id)
  place_type.description = payload.description
  stage_audit(session, admin_user, f"Edited place type {place_type_id}: {place_type.name}")

  commit_changes(session)
  session.refresh(place_type)
  return place_type

@router.put("/places/toggle-active/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def toggle_place_activeness(request: Request, session: SessionDep, admin_user: AdminDep, place_id: uuid.UUID):
  """Toggle a locked place's availability and record the administrative action."""
  place = session.exec(select(Place).where(Place.id == place_id).with_for_update()).first()
  if not place:
    raise HTTPException(detail="The place provided does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  place.is_active = not (place.is_active == True)
  session.add(place)
  stage_audit(session, admin_user, f"Set place is_active to {place.is_active}")

  commit_changes(session)
  session.refresh(place)
  return place

@router.put("/places/update/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_place(request: Request, session: SessionDep, admin_user: AdminDep, place_id: uuid.UUID, payload: PlaceInAdmin):
  """Update a locked place after validating its geography and type."""
  validate_place(session, payload)

  place = session.exec(select(Place).where(Place.id == place_id).with_for_update()).first()
  if not place:
    raise HTTPException(detail="The place you tried updating was not found!", status_code=status.HTTP_404_NOT_FOUND)
  
  place.sqlmodel_update(payload.model_dump())
  session.add(place)
  stage_audit(session, admin_user, f"Edited place {place.name}")

  commit_changes(session)
  session.refresh(place)
  return place
