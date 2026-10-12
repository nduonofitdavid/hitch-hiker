import uuid

from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.responses import JSONResponse
from sqlmodel import select

from app.api.deps import SessionDep, CurrentUser, get_current_user
from app.models import TripIn, OptimizerWeight, TripOut, TripStore, TripStatus, UserStats, RouteBlock, RouteTripOut, Stops, Coordinates, CoordTStopOut
from app.api.lib.sage_engine import SG, find_nearest, loader
from app.api.lib.sage_lib import distance_to_time, price_estimator
from app.api.lib.osm_engine import snap_and_traverse
from app.api.lib.helpers import coord_to_stop
from app.core.rlimiter import limiter

router = APIRouter(prefix="/trip", tags=["trip"])

@router.post('/', response_model=TripOut, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_directions(request: Request, session: SessionDep, user: CurrentUser, start_id: uuid.UUID | None=None, end_id: uuid.UUID | None=None, payload: TripIn|None=None, optimize_by: OptimizerWeight=OptimizerWeight.PRICE):
  """
  Overview: Returns directions using the knowledge graph
  Desc: Returns the directions to a stop using the knowledge graph, either through the id's of the stops, or the user's location
  It can either route with the start_id and end_id of the stops, or it takes the user's geographic coordinates, and uses that to route
  finding directions from their start location through stops, to their stop location.

  """
  if not all([start_id, end_id]) and not payload:
    raise HTTPException(detail="Bad request, you have to provide either start and stop ids, or a JSON payload of containing the coords!", status_code=status.HTTP_400_BAD_REQUEST)

  actual_start: tuple[CoordTStopOut|None, bool] = (None, False)
  actual_stop: tuple[CoordTStopOut|None, bool] = (None, False)
  stop_count = 1

  if start_id and end_id:
    try:
      start_stop = loader.stop_cache[start_id]
      end_stop = loader.stop_cache[end_id]
    except KeyError:
      raise HTTPException(detail="The start or end positions are not valid!", status_code=status.HTTP_400_BAD_REQUEST)

    payload_ = TripIn(start_latitude=start_stop.latitude, start_longitude=start_stop.longitude, stop_latitude=end_stop.latitude, stop_longitude=end_stop.longitude)
  else:
    assert payload
    payload_ = payload
    start_Ksnap, stop_Ksnap = find_nearest(payload_)
    start_stop = loader.stop_cache[start_Ksnap[0]]
    end_stop = loader.stop_cache[stop_Ksnap[0]]

    start_ac = (payload_.start_latitude, payload_.start_longitude)
    if start_ac != start_stop.get_coords():
      rb = coord_to_stop(ac_start=start_ac, kn_stop=start_stop)
      actual_start = (rb, False)
      stop_count+=1
    else:
      actual_start = (None, True)

    stop_ac = (payload_.stop_latitude, payload_.stop_longitude)
    if stop_ac != end_stop.get_coords():
      rb = coord_to_stop(ac_start=stop_ac, kn_stop=end_stop, route_index=stop_count)
      actual_stop = (rb, False)
    else:
      actual_stop = (None, True)

  path = SG.dijkstra_or_astar(start=start_stop.id, destination=end_stop.id, weight=optimize_by)
  if not path:
    raise HTTPException(detail="No route was found to the desired destination, from that starting point!", status_code=status.HTTP_404_NOT_FOUND)

  route_blocks = []
  total_distance: float = 0.0
  total_price: float = 0.0
  total_time: float = 0.0

  if actual_start[0]:
    # we make assumptions here because we can either have (RB, False) or (None, True)
    start_rb = actual_start[0]
    route_blocks.append(start_rb.rb)
    total_distance+=start_rb.dist
    total_price+=start_rb.rb.route.price
    total_time+=start_rb.rb.route.time

  for start, stop in zip(path, path[1:]):
    route = loader.get_route((start, stop))
    if route:
      total_price+=route.price
      total_time+=route.time
    start_node = loader.stop_cache[start]
    stop_node = loader.stop_cache[stop]

    res = loader.get_route_geometry((start, stop))
    if not res:
      osm_path = []
    else:
      osm_path, dist = res
      total_distance+=dist
    route_blocks.append({"route_position": stop_count, "start_node": start_node, "stop_node": stop_node, "route": route, "route_geometry": osm_path})
    stop_count+=1

  if actual_stop[0]:
    stop_rb = actual_start[0]
    v = stop_rb.rb
    v.route_position = stop_count
    route_blocks.append(v)
    total_distance+=stop_rb.dist
    total_price+=v.route.price
    total_time+=v.route.time

  trip_store = TripStore(
    user_id=user.id, 
    distance=total_distance,
    price=total_price, 
    time=total_time,
    city=start_stop.city,
    country=start_stop.country,
    start_latitude=payload_.start_latitude, 
    start_longitude=payload_.start_longitude, 
    stop_latitude=payload_.stop_latitude,
    stop_longitude=payload_.stop_longitude,
  ) 
  session.add(trip_store)
  session.commit()
  session.refresh(trip_store)

  return TripOut(route_count=stop_count, route_blocks=route_blocks, trip_id=trip_store.id)

# we can use this one as driver mode
@router.post('/default', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_direction_default(request: Request, session: SessionDep, payload: TripIn, user: CurrentUser):
  """Regular path traversal through the OSM graph, returning normal directions like that of a regular map"""
  start_coords = (payload.start_latitude, payload.start_longitude)
  stop_coords = (payload.stop_latitude, payload.stop_longitude)
  res = snap_and_traverse(start_coord=start_coords, stop_coord=stop_coords)
  if not res:
    raise HTTPException(detail="Sorry, no route was found!", status_code=status.HTTP_404_NOT_FOUND)
  
  path, dist = res
  price = price_estimator(dist)
  route = RouteTripOut(time=distance_to_time(dist), price=price, transport_modes=['Car'])
  route_blocks = RouteBlock(route_position=1, start_node=None, stop_node=None, route=route, route_geometry=path)
  trip_store = TripStore(
    user_id=user.id,
    distance=dist,
    price=price,
    start_latitude=payload.start_latitude,
    start_longitude=payload.start_longitude,
    stop_latitude=payload.stop_latitude,
    stop_longitude=payload.stop_longitude
  )
  session.add(trip_store)
  session.commit()
  session.refresh(trip_store)

  return TripOut(route_count=1, route_blocks=[route_blocks], trip_id=trip_store.id)

@router.post('/complete/{trip_id}', status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def mark_complete(request: Request, session: SessionDep, trip_id: uuid.UUID, user: CurrentUser):
  """This will mark a trip as complete"""
  trip = session.exec(select(TripStore).where(TripStore.id == trip_id, TripStore.user_id == user.id)).first()
  if not trip:
    raise HTTPException(detail="The trip you requested for was not found!", status_code=status.HTTP_400_BAD_REQUEST)

  trip.trip_status = TripStatus.COMPLETED
  session.add(trip)
  user_stats = session.exec(select(UserStats).where(UserStats.user_id == user.id)).first()
  if not user_stats:
    user_stats = UserStats(user_id=user.id, no_of_trips=1, distance_covered=trip.distance, total_spent=trip.price, average_time=trip.time)
  else:
    user_stats.no_of_trips+=1
    user_stats.distance_covered+=trip.distance
    user_stats.total_spent+=trip.price
    user_stats.average_time+=trip.time
  
  session.add(user_stats)
  session.commit()
  return JSONResponse(content={"message": "success"}, status_code=status.HTTP_200_OK)