"""Load revisioned transit graphs, select cheapest or fastest services, and cache route geometry."""

from collections import OrderedDict, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import heapq
import itertools
import logging
import math
from threading import RLock
from types import MappingProxyType
from typing import Callable
import uuid

from sqlmodel import select, Session
from sqlalchemy.orm import selectinload

from app.models import Stops, Route, OptimizerWeight, CachedRoute, CachedStop, GraphRevision
from app.api.lib.geometry import haversine
from app.api.lib.osm_engine import initialize_road_graph, reset_road_graph, route_coordinates, OutsideCoverage, RoutingUnavailable

class SageGraph:
  """A directed graph whose path reconstruction retains the chosen service."""
  def __init__(self, vertices: Mapping[uuid.UUID, CachedStop], edges: Mapping[uuid.UUID, Sequence[CachedRoute]]):
    """Copy stops and outgoing services into read-only graph mappings."""
    self.vertices = MappingProxyType(dict(vertices))
    self.edges = MappingProxyType({key: tuple(routes) for key, routes in edges.items()})

  def shortest_routes(self, start: uuid.UUID, destination: uuid.UUID, weight: OptimizerWeight, heuristic: Callable | None=None) -> tuple[CachedRoute, ...] | None:
    """Find the cheapest or fastest service sequence, validating any time heuristic."""
    if start not in self.vertices or destination not in self.vertices:
      return None
    if start == destination:
      return ()
    
    estimates = {node: 0.0 for node in self.vertices}
    if heuristic is not None:
      if weight != OptimizerWeight.TIME:
        raise ValueError("Heuristics can only optimize time")
      estimates = {node: float(heuristic(stop.get_coords(), self.vertices[destination].get_coords())) for node, stop in self.vertices.items()}
      if any(not math.isfinite(value) or value < 0 for value in estimates.values()) or abs(estimates[destination]) > 1e-9:
        raise ValueError("A routing heuristic must be finite, nonnegative, and zero at the destination")
    
      for node, routes in self.edges.items():
        if any(estimates[node] > route.time + estimates[route.end] + 1e-9 for route in routes):
          raise ValueError("The heuristic overestimates a recorded connection; use Dijkstra")
    
    distances = {start: 0.0}
    previous: dict[uuid.UUID, CachedRoute] = {}
    counter = itertools.count()
    heap = [(estimates[start], next(counter), 0.0, start)]
    while heap:
      _, _, distance, node = heapq.heappop(heap)
      if distance != distances.get(node):
        continue
      if node == destination:
        path = []
        while node != start:
          route = previous[node]
          path.append(route)
          node = route.start
        return tuple(reversed(path))
    
      for route in self.edges.get(node, ()):
        cost = route.time if weight == OptimizerWeight.TIME else route.price
        if route.end not in self.vertices or not math.isfinite(cost) or cost < 0:
          continue
        candidate = distance + cost
        if candidate < distances.get(route.end, math.inf):
          distances[route.end] = candidate
          previous[route.end] = route
          heapq.heappush(heap, (candidate + estimates[route.end], next(counter), candidate, route.end))
    return None

@dataclass(frozen=True)
class RoutingSnapshot:
  revision: int
  routes: Mapping[uuid.UUID, CachedRoute]
  graph: SageGraph

  @property
  def stops(self) -> Mapping[uuid.UUID, CachedStop]:
    """Return the immutable stops owned by this snapshot's graph."""
    return self.graph.vertices

@dataclass(frozen=True)
class RouteGeometry:
  coordinates: tuple[tuple[float, float], ...]
  distance: float
  source: str

  @property
  def estimated(self) -> bool:
    """Indicate whether geometry differs from an unqualified road path."""
    return self.source != "road"

def bump_graph_revision(session: Session) -> None:
  """Publish graph changes in the same transaction as the changed records."""
  dialect = session.get_bind().dialect.name
  if dialect == "postgresql":
    from sqlalchemy.dialects.postgresql import insert
  elif dialect == "sqlite":
    from sqlalchemy.dialects.sqlite import insert
  else:
    raise ValueError("Graph revision updates require PostgreSQL or SQLite")
  
  table = GraphRevision.__table__
  statement = insert(table).values(id=1, version=1)
  statement = statement.on_conflict_do_update(index_elements=[table.c.id], set_={"version": table.c.version + 1})
  session.execute(statement)

class Loader:
  """Swap complete immutable graph snapshots; compute geometry only on demand."""
  def __init__(self):
    """Initialize an empty transit snapshot, synchronization lock, and geometry cache."""
    self._snapshot = RoutingSnapshot(-1, MappingProxyType({}), SageGraph({}, {}))
    self._lock = RLock()
    self._geometry_cache = OrderedDict()

  def _revision(self, session: Session) -> int:
    """Read the database graph revision, treating a missing row as revision zero."""
    return session.exec(select(GraphRevision.version).where(GraphRevision.id == 1)).first() or 0

  def _build(self, session: Session, revision: int) -> RoutingSnapshot:
    """Build an immutable snapshot of active stops and valid active services."""
    stops = {
      stop.id: CachedStop(id=stop.id, name=stop.name, latitude=stop.latitude, longitude=stop.longitude, opposite=stop.opposite, city=stop.city, country=stop.country)
      for stop in session.exec(select(Stops).where(Stops.is_active).execution_options(populate_existing=True)).all()
    }
    adjacency = defaultdict(list)
    routes = {}
    for route in session.exec(select(Route).where(Route.is_active).options(selectinload(Route.transport_modes)).execution_options(populate_existing=True)).all():
      if route.start not in stops or route.end not in stops:
        logging.warning("Ignoring active route %s with inactive endpoints", route.id)
        continue
      if route.start == route.end or not math.isfinite(route.time) or route.time <= 0 or not math.isfinite(route.price) or route.price < 0:
        logging.warning("Ignoring route %s with invalid costs or endpoints", route.id)
        continue
      cached = CachedRoute(id=route.id, start=route.start, end=route.end, time=route.time, price=float(route.price), transport_modes=tuple(mode.name for mode in route.transport_modes))
      routes[cached.id] = cached
      adjacency[cached.start].append(cached)
    for outgoing in adjacency.values():
      outgoing.sort(key=lambda route: str(route.id))
    return RoutingSnapshot(revision, MappingProxyType(routes), SageGraph(stops, adjacency))

  def snapshot(self, session: Session | None=None) -> RoutingSnapshot:
    """Return the published snapshot, refreshing if a supplied session detects changes."""
    if session is None:
      return self._snapshot
    revision = self._revision(session)
    if revision == self._snapshot.revision:
      return self._snapshot
    return self.refresh(session)

  def ensure_current(self, session: Session) -> RoutingSnapshot:
    """Return a snapshot checked against the current database graph revision."""
    return self.snapshot(session)

  def refresh(self, session: Session) -> RoutingSnapshot:
    """Publish a complete snapshot after confirming its database revision stayed stable."""
    with self._lock:
      # Rechecking the revision prevents publishing rows from mixed commits.
      for _ in range(3):
        revision = self._revision(session)
        snapshot = self._build(session, revision)
        if self._revision(session) == revision:
          self._snapshot = snapshot
          return snapshot
      raise RoutingUnavailable("Transit data changed while loading; retry the request")

  def get_route_geometry(self, route: CachedRoute | uuid.UUID, snapshot: RoutingSnapshot | None=None) -> RouteGeometry:
    """Cache service geometry with physical distance and an explicit approximation source."""
    snapshot = snapshot or self._snapshot
    if isinstance(route, uuid.UUID):
      route = snapshot.routes[route]
    start, stop = snapshot.stops[route.start], snapshot.stops[route.end]
    key = (route.id, start.get_coords(), stop.get_coords(), route.transport_modes)
    with self._lock:
      cached = self._geometry_cache.get(key)
      if cached is not None:
        self._geometry_cache.move_to_end(key)
        return cached
      
    geometry = None
    modes = {mode.casefold() for mode in route.transport_modes}
    if modes & {"car", "bus", "taxi", "minibus", "motorcycle", "tricycle"}:
      try:
        journey = route_coordinates(start.get_coords(), stop.get_coords())
        if journey is not None:
          source = "road" if modes <= {"car", "taxi"} and journey.connector_distance < 0.01 and not journey.estimated else "road_approximation"
          geometry = RouteGeometry(journey.coordinates, journey.distance, source)
      except (OutsideCoverage, RoutingUnavailable):
        pass
    if geometry is None:
      coords = (start.get_coords(), stop.get_coords())
      geometry = RouteGeometry(coords, haversine(*coords), "straight_line_estimate")
    
    with self._lock:
      self._geometry_cache[key] = geometry
      self._geometry_cache.move_to_end(key)
      while len(self._geometry_cache) > 10000:
        self._geometry_cache.popitem(last=False)
    return geometry

loader = Loader()

def initialize_routing(session: Session | None=None) -> RoutingSnapshot:
  """Initialize car and walking road graphs and load the active transit snapshot."""
  initialize_road_graph()
  initialize_road_graph(mode="walk")
  if session is not None:
    return loader.refresh(session)
  from app.core.db import engine
  with Session(engine) as startup_session:
    return loader.refresh(startup_session)

def reset_routing() -> None:
  """Clear process caches during shutdown or isolated tests."""
  with loader._lock:
    loader._snapshot = RoutingSnapshot(-1, MappingProxyType({}), SageGraph({}, {}))
    loader._geometry_cache.clear()
  reset_road_graph()
