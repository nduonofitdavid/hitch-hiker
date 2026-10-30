import logging
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, Query, Request
from sqlmodel import select, or_
from sqlalchemy.exc import IntegrityError
from app.api.deps import SessionDep, AdminDep, Depends, get_current_superuser
from app.models import Country, City, StopsIn, Stops, get_datetime_utc, Records, AdminAction, Route, RouteIn, TransportMode, CityBase, TripStore, PlaceInAdmin, Place
from app.api.lib.sage_engine import refresh_sage_graph, SG, loader
from app.core.rlimiter import limiter

router = APIRouter(prefix='/admin', tags=['admin', 'admin_user'])

# stops
@router.post("/stops/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_stop(request: Request, session: SessionDep, admin_user: AdminDep, payload: StopsIn):
  """Create a Stop (Bus stop, car park, train station, etc.)"""
  country = loader.get_country(payload.country)
  city = loader.get_city(payload.city)
  if city == None or country == None: 
    raise HTTPException(detail="city or country was not found, check data properly", status_code=status.HTTP_404_NOT_FOUND)

  try:
    stop = Stops(name=payload.name, latitude=payload.latitude, longitude=payload.longitude, city=payload.city, country=payload.city)
    record = Records(
      action=AdminAction.CREATED, 
      performed_by=admin_user.id, 
      performed_at=get_datetime_utc(), 
      message=f"created stop for {payload.name} at {city[1]}, {country[1]}"
    )
    session.add(stop)
    session.add(record)
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception("Failed to add stop %s", payload.name)
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A conflict occured while adding this stop")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured")    

  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return stop

@router.put("/stops/update/{stop_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_stop(request: Request, session: SessionDep, stop_id: uuid.UUID, payload: StopsIn, admin_user: AdminDep):
  """Update a stop"""
  country = loader.get_country(payload.country)
  city = loader.get_city(payload.city)
  if city == None or country == None: 
    raise HTTPException(detail="city or country was not found, check data properly", status_code=status.HTTP_404_NOT_FOUND)
  
  stop = session.exec(select(Stops).where(Stops.id == stop_id)).first()
  if not stop:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The stop you tried updating does not exist!")

  stop.name = payload.name
  stop.latitude = payload.latitude
  stop.longitude = payload.longitude
  stop.city = payload.city
  stop.country = payload.country

  record = Records(action=AdminAction.EDITED, performed_by=admin_user.id, performed_at=get_datetime_utc(), message=f"Edited stop {stop.id}")
  session.add(record)
  try:
    session.commit()

  except Exception as e:
    session.rollback()
    logging.exception("Failed to update stop %s", stop_id)
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=409, detail="A conflict occurred while updating the data!")
    raise HTTPException(status_code=500, detail="An unexpected error occurred while updating the stop.")
  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return stop

# routes
@router.get("/routes", dependencies=[Depends(get_current_superuser)])
@limiter.limit("10/minute")
def get_routes(request: Request, session: SessionDep, route_id: uuid.UUID|None = None, start_id: uuid.UUID|None=None, end_id: uuid.UUID|None=None, transport_modes: list[uuid.UUID]|None=None, offset: int=0, limit: int = Query(default=100, le=100)):
  """Get the routes in the database"""
  query = select(Route)
  if route_id:
    query = query.where(Route.id == route_id)
  if start_id:
    query = query.where(Route.start == start_id)
  if end_id:
    query = query.where(Route.end == end_id)
  if transport_modes:
    transport_modes_store = session.exec(select(TransportMode).where(TransportMode.id._in(transport_modes))).all()
    query = query.where(Route.transport_modes.in_(transport_modes_store))
  
  routes = session.exec(query.offset(offset).limit(limit)).all()
  return routes

@router.post("/routes/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_route(request: Request, session: SessionDep, admin_user: AdminDep, payload: RouteIn):
  """Create a route between two stops"""
  res = session.exec(select(Stops).where(or_(Stops.id == payload.start, Stops.id == payload.end))).all()
  if not len(res) >= 2:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The start stop or end stop was not found!")
  del res

  transport_modes = session.exec(select(TransportMode).where(TransportMode.id.in_(payload.transport_mode))).all()
  if len(transport_modes) != len(payload.transport_mode):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A transport mode provided does not exist!")
  
  try:
    route = Route(start=payload.start, end=payload.end, time=payload.time, price=payload.price, transport_modes=transport_modes)
    record = Records(
      action=AdminAction.CREATED,
      performed_by=admin_user.id,
      performed_at=get_datetime_utc(),
      message=f"created route for {route.start} -> {route.end}"
    )
    session.add(route)
    session.add(record)
    session.commit()
    session.refresh(route)
  except Exception as e:
    session.rollback()
    logging.exception("Failed to add route.")
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A conflict occured while adding this route")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured")    
  
  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return route

@router.put("/routes/update/{route_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_route(request: Request, session: SessionDep, route_id: uuid.UUID, payload: RouteIn, admin_user: AdminDep):
  """Update a route"""
  route = session.exec(select(Route).where(Route.id == route_id)).first()
  transport_modes = session.exec(select(TransportMode).where(TransportMode.id.in_(payload.transport_mode))).all()
  if len(transport_modes) != len(payload.transport_mode):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A transport mode provided does not exist!")
  
  if not route:
    raise HTTPException(detail="The route provided does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  route.start = payload.start
  route.end = payload.end
  route.time = payload.time
  route.price = payload.price
  route.transport_modes = transport_modes

  record = Records(
    action=AdminAction.EDITED,
    performed_by=admin_user.id,
    performed_at=get_datetime_utc(),
    message=f"Edit on route {route.id}"
  )
  session.add(route)
  session.add(record)
  session.commit()
  session.refresh(route)

  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return route

# trip
@router.get("/trips", dependencies=[Depends(get_current_superuser)], status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def get_trips_admin(request: Request, session: SessionDep, trip_id: uuid.UUID | None=None, user_id: uuid.UUID|None=None, price: float|None=None, distance: float|None=None, country: uuid.UUID | None=None, city: uuid.UUID | None=None, offset: int=0, limit: int=Query(default=100, le=100)):
  """Get trips for an admin user"""
  query = select(TripStore)
  if trip_id:
    query = query.where(TripStore.id == trip_id)
  if user_id:
    query = query.where(TripStore.user_id == user_id)
  if price:
    query = query.where(TripStore.price == price)
  if distance:
    query = query.where(TripStore.distance == distance)
  if country:
    query = query.where(TripStore.country == country)
  if city:
    query = query.where(TripStore.city == city)
  
  trips = session.exec(query.offset(offset).limit(limit)).all()
  return trips

# records
@router.get("/records", dependencies=[Depends(get_current_superuser)], status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def get_records(request: Request, session: SessionDep, record_id: uuid.UUID | None=None, action: AdminAction | None=None, performed_by: uuid.UUID | None=None, performed_at: datetime | None=None, offset: int=0, limit: int=Query(default=100, le=100)):
  """Get records"""
  query = select(Records)
  if record_id:
    query = query.where(Records.id == record_id)
  if action:
    query = query.where(Records.action == action)
  if performed_by:
    query = query.where(Records.performed_by == performed_by)
  if performed_at:
    query = query.where(Records.performed_at == performed_at)
  
  records = session.exec(query.offset(offset).limit(limit)).all()
  return records

# country
@router.post("/country/add/{name}", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_country(request: Request, session: SessionDep, admin_user: AdminDep, name: str):
  """Add a country"""
  country = session.exec(select(Country).where(Country.name.ilike(name))).first()
  if country:
    raise HTTPException(detail="The country exists already", status_code=status.HTTP_400_BAD_REQUEST)
  
  country = Country(name=name)
  record = Records(action=AdminAction.CREATED, performed_by=admin_user.id, performed_at=get_datetime_utc(), message=f"Created country {name}")
  session.add(country)
  session.add(record)
  session.commit()
  session.refresh(country)

  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return country

@router.put("/country/update/{country_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_country(request: Request, session: SessionDep, admin_user: AdminDep, country_id: uuid.UUID, name: str):
  """Update a country"""
  country = session.exec(select(Country).where(Country.id == country_id)).first()
  if not country:
    raise HTTPException(detail="The country you tried editing was not found", status_code=status.HTTP_404_NOT_FOUND)
  
  country.name = name
  record = Records(action=AdminAction.EDITED, performed_by=admin_user.id, message=f"Edited country: {name}")
  session.add(country)
  session.add(record)
  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception(f"Failed to edit country: {country.name}")
    if isinstance(e, IntegrityError):
      raise HTTPException(detail="The name provided already exists in the system", status_code=status.HTTP_409_CONFLICT)
    raise HTTPException(detail="Sorry an unexpected error occured, try again later!", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

  session.refresh(country)
  return country

# city
@router.post("/city/add/", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_city(request: Request, session: SessionDep, admin_user: AdminDep, payload: CityBase):
  """Add a city"""
  if not loader.check_country(payload.country):
    raise HTTPException(detail="The country provided is not valid!", status_code=status.HTTP_400_BAD_REQUEST)
  
  city = City(name=payload.name, country=payload.country)
  record = Records(action=AdminAction.CREATED, performed_by=admin_user.id, performed_at=get_datetime_utc(), message=f"Created city {payload.name}")
  session.add(city)
  session.add(record)
  try:
    session.commit()
  except Exception as e:
    if isinstance(e, IntegrityError):
      raise HTTPException(detail="This city already exists in the system", status_code=status.HTTP_409_CONFLICT)
    raise HTTPException(detail="Sorry an unexpected error occured, try again later!", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
  session.refresh(city)

  refresh_sage_graph(session=session, loader=loader, graph=SG)
  return city

@router.put("/city/update/{city_id}")
@limiter.limit("10/minute")
def edit_city(request: Request, session: SessionDep, admin_user: AdminDep, city_id: uuid.UUID, payload: CityBase):
  if not loader.check_country(payload.country):
    raise HTTPException(detail="The country provided does not exist!", status_code=status.HTTP_400_BAD_REQUEST)
  city = session.exec(select(City).where(City.id == city_id)).first()
  if not city:
    raise HTTPException(detail="The city does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  city.name = payload.name
  city.country = payload.country
  record = Records(action=AdminAction.EDITED, performed_by=admin_user.id, message=f"Edited city, {city.name}")
  session.add(city)
  session.add(record)
  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception(f"Failed to edity city: {city.name}")
    if isinstance(e, IntegrityError):
      raise HTTPException(detail="Sorry, there was a conflict while updating the city, check if a city already has the provided details!", status_code=status.HTTP_409_CONFLICT)
    raise HTTPException(detail="Sorry an unexpected error occured, try again later!", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

  session.refresh(city)
  return city
    
# places api
@router.post("/places/add", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def add_place(request: Request, session: SessionDep, admin_user: AdminDep, payload: PlaceInAdmin):
  """Create a place"""
  if not loader.check_country(payload.country):
    raise HTTPException(detail="The country provided does not exist!", status_code=status.HTTP_400_BAD_REQUEST)
  if not loader.check_city(payload.city):
    raise HTTPException(detail="The city provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)

  place = Place(name=payload.name, latitude=payload.latitude, longitude=payload.longitude, is_active=payload.is_active, description=payload.description, city=payload.city, country=payload.country)
  record = Records(action=AdminAction.CREATED, performed_by=admin_user.id, message=f"Created place {payload.name}")
  try:
    session.add(place)
    session.add(record)
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception(f"Failed to add place: {payload.name}")
    if isinstance(e, IntegrityError):
      raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A conflict occured while adding place")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Sorry, an unexpected error occured")    

  session.refresh(place)
  return place

@router.put("/places/toggle-active/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def toggle_place_activeness(request: Request, session: SessionDep, admin_user: AdminDep, place_id: uuid.UUID):
  """Toggle the activeness of a place on or off"""
  place = session.exec(select(Place).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place provided does not exist!", status_code=status.HTTP_404_NOT_FOUND)
  
  place.is_active = not (place.is_active == True)
  record = Records(action=AdminAction.EDITED, performed_by=admin_user.id, message=f"Set place is_active to {place.is_active}")
  session.add(place)
  session.add(record)
  session.commit()
  session.refresh(place)
  return place

@router.put("/places/update/{place_id}", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
def update_place(request: Request, session: SessionDep, admin_user: AdminDep, place_id: uuid.UUID, payload: PlaceInAdmin):
  """Update a place"""
  if not loader.check_country(payload.country):
    raise HTTPException(detail="The country provided does not exist!", status_code=status.HTTP_400_BAD_REQUEST)
  if not loader.check_city(payload.city):
    raise HTTPException(detail="The city provided does not exist", status_code=status.HTTP_400_BAD_REQUEST)
  
  place = session.exec(select(Place).where(Place.id == place_id)).first()
  if not place:
    raise HTTPException(detail="The place you tried updating was not found!", status_code=status.HTTP_404_NOT_FOUND)
  
  place.name = payload.name
  place.latitude = payload.latitude
  place.longitude = payload.longitude
  place.is_active = payload.is_active
  place.description = payload.description
  place.city = payload.city
  place.country = payload.country

  record = Records(action=AdminAction.EDITED, performed_by=admin_user.id, message=f"Edited place {place.name}")
  session.add(place)
  session.add(record)
  try:
    session.commit()
  except Exception as e:
    session.rollback()
    logging.exception(f"Failed to edit place: {payload.name}")
    if isinstance(e, IntegrityError):
      raise HTTPException(detail="A conflict occured while editing the place!", status_code=status.HTTP_409_CONFLICT)
    raise HTTPException(detail="Sorry, an unexpected error occured", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

  session.refresh(place)
  return place