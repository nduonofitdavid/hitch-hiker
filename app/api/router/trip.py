"""Plan road and transit trips, save itinerary snapshots, and record completed journeys."""

from decimal import Decimal
import math
import uuid

from fastapi import APIRouter, HTTPException, status, Request
from sqlmodel import select
from sqlalchemy import update

from app.api.deps import SessionDep, CurrentUser
from app.models import TripIn, OptimizerWeight, TripOut, TripStore, TripStatus, UserStats, RouteBlock, RouteTripOut, StopTripOut, get_datetime_utc
from app.api.lib.sage_engine import loader, RoutingSnapshot
from app.api.lib.geometry import haversine
from app.api.lib.sage_lib import price_estimator, round_money
from app.api.lib.osm_engine import route_coordinates, OutsideCoverage, RoutingUnavailable
from app.core.config import settings
from app.core.rlimiter import limiter

router = APIRouter(prefix="/trip", tags=["trip"])

def _stop_out(stop):
  """Serialize a stop's name and coordinates for a trip segment."""
  return StopTripOut(name=stop.name, latitude=stop.latitude, longitude=stop.longitude)

def _transit_blocks(snapshot: RoutingSnapshot, routes) -> list[RouteBlock]:
  """Build ordered transit segments retaining selected service IDs and geometry."""
  blocks = []
  for route in routes:
    geometry = loader.get_route_geometry(route, snapshot)
    blocks.append(RouteBlock(
      route_position=len(blocks) + 1,
      start_node=_stop_out(snapshot.stops[route.start]),
      stop_node=_stop_out(snapshot.stops[route.end]),
      route=RouteTripOut(time=route.time, price=route.price, transport_modes=list(route.transport_modes)),
      route_id=route.id,
      route_geometry=list(geometry.coordinates),
      distance=geometry.distance,
      geometry_source=geometry.source,
    ))
  return blocks

def _trip_totals(blocks) -> tuple[Decimal, float, float]:
  """Sum exact fares, travel minutes, and physical metres for itinerary segments."""
  total_price = sum((Decimal(str(block.route.price)) for block in blocks if block.route), Decimal("0"))
  total_time = sum(block.route.time for block in blocks if block.route)
  total_distance = sum(block.distance or 0.0 for block in blocks)
  return total_price, total_time, total_distance

def _save_trip(session, user, blocks, start, stop, *, location=None, optimize_by=None):
  """Persist itinerary segments and validated totals and return the saved trip response."""
  total_price, total_time, total_distance = _trip_totals(blocks)
  if (
    not total_price.is_finite() or not 0 <= total_price < Decimal("1e12")
    or any(not math.isfinite(value) or not 0 <= value < 1e308 for value in (total_time, total_distance))
  ):
    raise HTTPException(status_code=422, detail="The combined fare, duration, or distance exceeds the supported trip range")
  blocks = [block.model_copy(update={"route_position": position}) for position, block in enumerate(blocks, start=1)]
  trip = TripStore(
    user_id=user.id,
    distance=total_distance,
    price=round_money(total_price),
    time=total_time,
    start_latitude=start[0], start_longitude=start[1],
    stop_latitude=stop[0], stop_longitude=stop[1],
    city=location.city if location else None,
    country=location.country if location else None,
    itinerary=[block.model_dump(mode="json") for block in blocks],
    optimize_by=optimize_by,
    distance_estimated=any("estimate" in block.geometry_source or "approximation" in block.geometry_source for block in blocks),
  )
  session.add(trip)
  session.commit()
  return TripOut(route_count=len(blocks), route_blocks=blocks, trip_id=trip.id)


def _road_journey(start, stop, mode="car"):
  """Plan a road journey and translate routing failures into HTTP errors."""
  try:
    journey = route_coordinates(start, stop, mode=mode)
  except OutsideCoverage as exc:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
  except RoutingUnavailable as exc:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
  if journey is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No road connection was found between these locations")
  return journey


@router.post('/', response_model=TripOut, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_directions(request: Request, session: SessionDep, user: CurrentUser, start_id: uuid.UUID, end_id: uuid.UUID, optimize_by: OptimizerWeight=OptimizerWeight.PRICE):
  """Find a connection through active transport stops and recorded services."""
  snapshot = loader.ensure_current(session)
  start_stop, end_stop = snapshot.stops.get(start_id), snapshot.stops.get(end_id)
  if start_stop is None or end_stop is None:
    raise HTTPException(detail="The start or end stop is not active or does not exist", status_code=status.HTTP_400_BAD_REQUEST)
  routes = snapshot.graph.shortest_routes(start_id, end_id, optimize_by)
  if routes is None:
    raise HTTPException(detail="No transport connection was found", status_code=status.HTTP_404_NOT_FOUND)
  return _save_trip(session, user, _transit_blocks(snapshot, routes), start_stop.get_coords(), end_stop.get_coords(), location=start_stop, optimize_by=optimize_by)

@router.post('/default', response_model=TripOut, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_direction_default(request: Request, session: SessionDep, payload: TripIn, user: CurrentUser):
  """Estimate a car journey; distances are metres, time minutes and fares NGN."""
  start = (payload.start_latitude, payload.start_longitude)
  stop = (payload.stop_latitude, payload.stop_longitude)
  journey = _road_journey(start, stop)
  blocks = []
  if start != stop:
    blocks.append(RouteBlock(
      route_position=1,
      route=RouteTripOut(time=journey.time, price=price_estimator(journey.distance), transport_modes=["Car"]),
      route_geometry=list(journey.coordinates), distance=journey.distance,
      geometry_source="road_approximation" if journey.connector_distance > 0.01 or journey.estimated else "road",
    ))
  snapshot = loader.ensure_current(session)
  nearest = min(snapshot.stops.values(), key=lambda node: haversine(start, node.get_coords()), default=None)
  location = nearest if nearest and haversine(start, nearest.get_coords()) <= settings.MAX_SNAP_DISTANCE_M else None
  return _save_trip(session, user, blocks, start, stop, location=location)


def _candidate_stops(snapshot, coord):
  """Select nearby stops and their covered opposite boarding points."""
  candidates = sorted(snapshot.stops.values(), key=lambda node: haversine(coord, node.get_coords()))
  selected = {node.id: node for node in candidates[:3] if haversine(coord, node.get_coords()) <= settings.MAX_SNAP_DISTANCE_M}
  # Opposite stops represent distinct boarding points and retain their direction.
  for node in tuple(selected.values()):
    opposite = snapshot.stops.get(node.opposite)
    if opposite and haversine(coord, opposite.get_coords()) <= settings.MAX_SNAP_DISTANCE_M:
      selected[opposite.id] = opposite
  return tuple(selected.values())


def _walking_block(start, stop, *, start_node=None, stop_node=None):
  """Build a walking segment unless its endpoints are effectively identical."""
  if haversine(start, stop) < 0.01:
    return None
  journey = _road_journey(start, stop, mode="walk")
  return RouteBlock(
    route_position=1,
    start_node=_stop_out(start_node) if start_node else None,
    stop_node=_stop_out(stop_node) if stop_node else None,
    route=RouteTripOut(time=journey.time, price=0.0, transport_modes=["Walk"]),
    route_geometry=list(journey.coordinates), distance=journey.distance,
    geometry_source="walking_road_approximation" if journey.connector_distance > 0.01 or journey.estimated else "walking_road",
  )


def _walking_connectors(coord, candidates, *, to_stops):
  """Calculate each directed walking connector once, retaining errors for eligible pairs."""
  connectors = {}
  for node in candidates:
    try:
      connectors[node.id] = (
        _walking_block(coord, node.get_coords(), stop_node=node) if to_stops
        else _walking_block(node.get_coords(), coord, start_node=node)
      )
    except HTTPException as exc:
      connectors[node.id] = exc
  return connectors


@router.post('/transit', response_model=TripOut, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_direction_transit(request: Request, session: SessionDep, payload: TripIn, user: CurrentUser, optimize_by: OptimizerWeight=OptimizerWeight.PRICE):
  """Connect coordinates to nearby boarding stops with walking segments."""
  snapshot = loader.ensure_current(session)
  start, stop = (payload.start_latitude, payload.start_longitude), (payload.stop_latitude, payload.stop_longitude)
  if not snapshot.stops:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No active transport stops are available")
  starts, stops = _candidate_stops(snapshot, start), _candidate_stops(snapshot, stop)
  if not starts or not stops:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A location is outside transport stop coverage")
  if start == stop:
    return _save_trip(session, user, [], start, stop, location=starts[0], optimize_by=optimize_by)
  first_connectors = _walking_connectors(start, starts, to_stops=True)
  last_connectors = _walking_connectors(stop, stops, to_stops=False)
  best = None
  connector_error = None
  for start_stop in starts:
    for end_stop in stops:
      if start_stop.id == end_stop.id:
        continue
      routes = snapshot.graph.shortest_routes(start_stop.id, end_stop.id, optimize_by)
      if routes is None:
        continue
      first, last = first_connectors[start_stop.id], last_connectors[end_stop.id]
      error = first if isinstance(first, HTTPException) else last
      if isinstance(error, HTTPException):
        connector_error = error
        continue
      blocks = ([first] if first else []) + _transit_blocks(snapshot, routes) + ([last] if last else [])
      price, duration, distance = _trip_totals(blocks)
      score = (price, duration, distance) if optimize_by == OptimizerWeight.PRICE else (duration, price, distance)
      if best is None or score < best[0]:
        best = (score, blocks, start_stop)
  if best is None:
    if connector_error:
      raise connector_error
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transport connection was found between nearby stops")
  return _save_trip(session, user, best[1], start, stop, location=best[2], optimize_by=optimize_by)


@router.post('/complete/{trip_id}', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def mark_complete(request: Request, session: SessionDep, trip_id: uuid.UUID, user: CurrentUser):
  """Complete once, updating statistics in the same database transaction."""
  try:
    changed = session.execute(update(TripStore).where(
      TripStore.id == trip_id, TripStore.user_id == user.id, TripStore.trip_status == TripStatus.PENDING,
    ).values(trip_status=TripStatus.COMPLETED, completed_at=get_datetime_utc())).rowcount
    if changed != 1:
      exists = session.exec(select(TripStore.id).where(TripStore.id == trip_id, TripStore.user_id == user.id)).first()
      session.rollback()
      if exists:
        raise HTTPException(detail="This trip has already been completed", status_code=status.HTTP_409_CONFLICT)
      raise HTTPException(detail="The requested trip was not found", status_code=status.HTTP_404_NOT_FOUND)
    
    trip = session.exec(select(TripStore).where(TripStore.id == trip_id)).one()
    if session.get_bind().dialect.name == "postgresql":
      from sqlalchemy.dialects.postgresql import insert
    else:
      from sqlalchemy.dialects.sqlite import insert
    
    table = UserStats.__table__
    statement = insert(table).values(
      id=uuid.uuid4(), user_id=user.id, no_of_trips=1,
      distance_covered=trip.distance, total_spent=trip.price, total_time=trip.time,
    )
    statement = statement.on_conflict_do_update(index_elements=[table.c.user_id], set_={
      "no_of_trips": table.c.no_of_trips + 1,
      "distance_covered": table.c.distance_covered + statement.excluded.distance_covered,
      "total_spent": table.c.total_spent + statement.excluded.total_spent,
      "total_time": table.c.total_time + statement.excluded.total_time,
    })
    session.execute(statement)
    session.commit()
  except Exception:
    session.rollback()
    raise
  return {"message": "success"}
