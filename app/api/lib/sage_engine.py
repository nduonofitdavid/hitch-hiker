from collections import defaultdict
import heapq
from typing import Callable
import uuid

from sqlmodel import select, Session
from sqlalchemy.orm import selectinload

from app.models import Stops, Route, OptimizerWeight, TripIn, Country, City, CachedRoute, CachedStop
from app.api.lib.sage_lib import haversine
from app.api.lib.osm_engine import snap_and_traverse
from app.core.db import engine

class Loader:
  """Load the knowledge tree from the database for traversal"""
  def __init__(self):
    self.stop_cache: dict[uuid.UUID, CachedStop] = {}
    self._route_cache = defaultdict(list)
    self._edge_lookup: dict[tuple[uuid.UUID, uuid.UUID], CachedRoute] = {}
    self._country_hash: dict = {}
    self._city_hash: dict = {}
    self._route_geometry_cache: dict[tuple[uuid.UUID, uuid.UUID], tuple[list[tuple[float, float]], float]] = {}

  def load_stops(self, session: Session, use_cache: bool=True):
    if use_cache and self.stop_cache:
      return self.stop_cache
    stops = session.exec(select(Stops).where(Stops.is_active)).all()
    new_stop_cache = {}
    for stop in stops:
      new_stop_cache[stop.id] = CachedStop(id=stop.id, name=stop.name, latitude=stop.latitude, longitude=stop.longitude, opposite=None, city=stop.city, country=stop.country)
    
    self.stop_cache = new_stop_cache
    return self.stop_cache

  def load_countries(self, session: Session, use_cache: bool=True):
    if use_cache and self._country_hash:
      return self._country_hash
    countries = session.exec(select(Country.id, Country.name)).all()
    self._country_hash = {id: name for id, name in countries}
    return self._country_hash

  def load_cities(self, session: Session, use_cache: bool=True):
    if use_cache and self._city_hash:
      return self._city_hash
    cities = session.exec(select(City.id, City.name)).all()
    self._city_hash = {id: name for id, name in cities}
    return self._city_hash

  def get_city(self, city_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    return self._city_hash[city_id] if city_id in self._city_hash else None
  
  def get_country(self, country_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    return self._country_hash[country_id] if country_id in self._country_hash else None
  
  def load_routes(self, session: Session, use_cache: bool=True):
    if use_cache and self._route_cache:
      return self._route_cache

    routes = session.exec(select(Route).where(Route.is_active == True).options(selectinload(Route.transport_modes))).all()
    new_route_cache = {}
    new_edge_lookup = {}
    
    for route in routes:
      cached_route = CachedRoute(start=route.start, end=route.end, time=route.time, price=route.price, transport_modes=[mode.name for mode in route.transport_modes])
      new_route_cache[route.start].append(cached_route)
      new_edge_lookup[(route.start, route.end)] = cached_route
    
    self._route_cache = new_route_cache
    self._edge_lookup = new_edge_lookup

    return self._route_cache
    
  def get_route(self, nodes: tuple[uuid.UUID, uuid.UUID]):
    """Get a route by the route id"""
    try:
      return self._edge_lookup[nodes]
    except KeyError:
      return None

  def load_route_geometry(self, session: Session):
    """Preloads the geometry for the knowledge graph and caches it in a dictionary"""
    routes = session.exec(select(Route).where(Route.is_active)).all()
    new_routes = {}
    for route in routes:
      start = self.stop_cache[route.start]
      end = self.stop_cache[route.end]
      res = snap_and_traverse(start.get_coords(), end.get_coords())
      if not res:
        continue
      path, dist = res
      new_routes[(route.start, route.end)] = path, dist
    self._route_geometry_cache = new_routes


  def get_route_geometry(self, route_key: tuple[uuid.UUID, uuid.UUID]) -> tuple[list[tuple[float, float]], float] | None:
    """Get a route geometry by the route key"""
    if route_key in self._route_geometry_cache:
      return self._route_geometry_cache[route_key]
    return None

  def refresh(self, session: Session):
    """Refresh the cached data"""
    self.load_stops(session=session, use_cache=False)
    self.load_routes(session=session, use_cache=False)
    self.load_cities(session=session, use_cache=False)
    self.load_countries(session=session, use_cache=False)
    self.load_route_geometry(session=session)

def init_loader(loader: Loader) -> None:
  """Fetch the initial data from the database upon server start-up"""
  with Session(engine) as session:
    loader.load_stops(session=session, use_cache=False)
    loader.load_routes(session=session, use_cache=False)
    loader.load_cities(session=session, use_cache=False)
    loader.load_countries(session=session, use_cache=False)
    loader.load_route_geometry(session=session)

loader = Loader()
init_loader(loader)

class SageGraph:
  """This graph works on the knowledge tree"""
  def __init__(self, vertices: dict[uuid.UUID, Stops], edges: dict[uuid.UUID, list[Route]]) -> None:
    self.vertices = vertices
    self.edges = edges

  def get_vertices(self):
    return self.vertices

  def get_edges(self):
    return self.edges

  def update_edges(self, edges: dict[uuid.UUID, list[Route]]):
    self.edges = edges

  def update_vertices(self, vertices: dict[uuid.UUID, Stops]):
    self.vertices = vertices

  def _dijkstra_rev_path(self, start: uuid.UUID, previous: dict[uuid.UUID, uuid.UUID], destination: uuid.UUID):
    route = []
    current = destination
    while True:
      route.append(current)
      if current == start:
        break
      if current not in previous:
        return []
      current = previous[current]
    route.reverse()
    return route

  def dijkstra_or_astar(self, start: uuid.UUID, destination: uuid.UUID, weight: OptimizerWeight, heuristic: Callable|None = None):
    if heuristic and (weight != OptimizerWeight.TIME):
      raise ValueError("Heuristic can only be used with time as weight")
    distance = {node_id: float("inf") for node_id in self.vertices}
    distance[start] = 0
    previous = {}
    visited = set()

    heap = [(0, start)]
    while heap:
      _, node_idx = heapq.heappop(heap)
      if node_idx == destination:
        break
      if node_idx in visited:
        continue
      visited.add(node_idx)

      for route in self.edges[node_idx]:
        g_n = route.time if weight == OptimizerWeight.TIME else route.price
        h_n = 0
        if heuristic:
          h_n = heuristic(self.vertices[route.end].get_coords(), self.vertices[destination].get_coords())
        tentative_distance = distance[node_idx] + g_n
        if tentative_distance < distance[route.end]:
          distance[route.end] = tentative_distance
          previous[route.end] = node_idx
          heapq.heappush(heap, (tentative_distance + h_n, route.end)) # type: ignore

    path_ids: list[uuid.UUID] = self._dijkstra_rev_path(start, previous, destination)
    if not path_ids:
      return None
    return path_ids

SG = SageGraph(vertices=loader.stop_cache, edges=loader._route_cache)

def refresh_sage_graph(session: Session, loader: Loader, graph: SageGraph):
  loader.refresh(session=session)
  graph.update_vertices(vertices=loader.stop_cache)
  graph.update_edges(edges=loader._route_cache)

def find_nearest(payload: TripIn):
  best_start_distance = float("inf")
  best_stop_distance  = float("inf")

  best_start_stop: uuid.UUID = None
  best_stop_stop: uuid.UUID = None

  stops = loader.stop_cache.values()
  if not stops:
    raise Exception("No node was found in the knowledge tree")

  for stop in stops:
    n_start_dist = haversine((payload.start_latitude, payload.start_longitude), stop.get_coords())
    if n_start_dist < best_start_distance:
      best_start_distance = n_start_dist
      best_start_stop = stop.id

    n_stop_dist = haversine((payload.stop_latitude, payload.stop_longitude), stop.get_coords())
    if n_stop_dist < best_stop_distance:
      best_stop_distance = n_stop_dist
      best_stop_stop = stop.id

  result = [(best_start_stop, best_start_distance), (best_stop_stop, best_stop_distance)]
  return result