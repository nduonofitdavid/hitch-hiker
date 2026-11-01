"""Configure the database engine, initialize the administrator, and import validated transit data."""

from sqlmodel import Session, create_engine, select
from app import crud
from app.core.config import settings
from app.models import City, Country, Route, Stops, TransportMode, UserCreate
from app.seed import load_seed_data

engine = create_engine(str(settings.DATABASE_URL), pool_pre_ping=True)

def add_nodes(session: Session, stop_file: str, route_file: str, *, country_name: str = 'Nigeria', city_name: str = 'Abuja') -> None:
  """Atomically import validated transit data using a session with no active transaction.

  Repeated imports reuse identical stops and Car routes and reject conflicting records.
  """
  for value in (country_name, city_name):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
      raise ValueError('Country and city names must contain 1 to 128 characters')
  country_name, city_name = country_name.strip(), city_name.strip()
  stops, routes = load_seed_data(stop_file, route_file)

  with session.begin():
    changed = False
    country = session.exec(select(Country).where(Country.name == country_name)).first()
    if not country:
      country = Country(name=country_name)
      session.add(country)
      session.flush()
      changed = True
    city = session.exec(select(City).where(City.name == city_name, City.country == country.id)).first()
    if not city:
      city = City(name=city_name, country=country.id)
      session.add(city)
      session.flush()
      changed = True
    cab = session.exec(select(TransportMode).where(TransportMode.name == 'Car')).first()
    if not cab:
      cab = TransportMode(name='Car')
      session.add(cab)
      session.flush()
      changed = True

    locations = {(stop.latitude, stop.longitude): stop for stop in session.exec(select(Stops).where(Stops.city == city.id, Stops.country == country.id)).all()}
    stored_stops = {}
    for stop in stops:
      stored = locations.get((stop.latitude, stop.longitude))
      if stored:
        if stored.name != stop.name:
          raise ValueError(f'Name conflicts with the existing stop at coordinates for {stop.name}')
      else:
        stored = Stops(name=stop.name, latitude=stop.latitude, longitude=stop.longitude, country=country.id, city=city.id)
        session.add(stored)
        changed = True
      stored_stops[stop.id] = stored.id
    session.flush()

    stop_ids = list(stored_stops.values())
    existing_routes = {}
    for route in session.exec(select(Route).join(Route.transport_modes).where(TransportMode.id == cab.id, Route.start.in_(stop_ids), Route.end.in_(stop_ids))).all():
      existing_routes.setdefault((route.start, route.end), set()).add((route.price, route.time))
    for route in routes:
      start_id, end_id = stored_stops[route.start], stored_stops[route.end]
      recorded = existing_routes.get((start_id, end_id))
      if recorded:
        if (route.price, route.time) not in recorded:
          raise ValueError('Fare or time conflicts with an existing Car route')
      else:
        session.add(Route(start=start_id, end=end_id, price=route.price, time=route.time, transport_modes=[cab]))
        changed = True
    if changed:
      from app.api.lib.sage_engine import bump_graph_revision
      bump_graph_revision(session)

def init_db(session: Session) -> None:
  """Create the configured initial administrator if its account is absent."""
  user = crud.get_user_by_email(session=session, email=settings.FIRST_SUPERUSER)
  if not user:
    user_in = UserCreate(
      email=settings.FIRST_SUPERUSER,
      password=settings.FIRST_SUPERUSER_PASSWORD,
      is_superuser=True,
    )
    user = crud.create_user(session=session, user_create=user_in)
