import uuid

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlmodel import select

from app.api.deps import SessionDep, CurrentUser
from app.models import TripIn, OptimizerWeight, TripOut, TripStore, TripStatus, UserStats, RouteBlock, RouteTripOut
from app.api.lib.sage_engine import SG, loader
from app.api.lib.sage_lib import distance_to_time, price_estimator
from app.api.lib.osm_engine import snap_and_traverse
from app.core.rlimiter import limiter

router = APIRouter(prefix="/trip", tags=["trip"])

@router.post('/', response_model=TripOut, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def get_directions(request: Request, session: SessionDep, user: CurrentUser, start_id: uuid.UUID, end_id: uuid.UUID, optimize_by: OptimizerWeight=OptimizerWeight.PRICE):
  """
  So for now, we will refactor this function to just navigate between stops. then later on
  we will add moving from current location.
  """
  try:
    start_stop = loader.stop_cache[start_id]
    end_stop = loader.stop_cache[end_id]
  except KeyError:
    raise HTTPException(detail="The start or end positions are not valid!", status_code=status.HTTP_400_BAD_REQUEST)

  path = SG.dijkstra_or_astar(start=start_stop.id, destination=end_stop.id, weight=optimize_by)
  if not path:
    raise HTTPException(detail="No route was found to the desired destination, from that starting point!", status_code=status.HTTP_404_NOT_FOUND)

  route_blocks = []
  total_distance: float = 0.0
  total_price: float = 0.0
  total_time: float = 0.0
  stop_count: int = 0

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

  trip_store = TripStore(
    user_id=user.id, 
    distance=total_distance,
    price=total_price, 
    time=total_time,
    city=start_stop.city,
    country=start_stop.country,
    start_latitude=start_stop.latitude, 
    start_longitude=start_stop.longitude, 
    stop_latitude=end_stop.latitude,
    stop_longitude=end_stop.longitude,
  ) 
  session.add(trip_store)
  session.commit()
  session.refresh(trip_store)

  return TripOut(route_count=stop_count, route_blocks=route_blocks, trip_id=trip_store.id)

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
    time=distance_to_time(dist),
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

  if trip.trip_status == TripStatus.COMPLETED:
    raise HTTPException(detail="This trip has already been completed", status_code=status.HTTP_409_CONFLICT)
  
  trip.trip_status = TripStatus.COMPLETED
  session.add(trip)
  user_stats = session.exec(select(UserStats).where(UserStats.user_id == user.id)).first()
  if not user_stats:
    user_stats = UserStats(user_id=user.id, no_of_trips=1, distance_covered=trip.distance, total_spent=trip.price, average_time=trip.time)
  else:
    user_stats.no_of_trips+=1
    user_stats.distance_covered+=trip.distance
    user_stats.total_spent+=trip.price
    user_stats.total_time+=trip.time
  
  session.add(user_stats)
  session.commit()
  return JSONResponse(content={"message": "success"}, status_code=status.HTTP_200_OK)