"""Build OpenStreetMap road graphs and plan car or walking journeys between coordinates."""

from dataclasses import dataclass
import math
from threading import RLock

import osmium
from osmium import osm
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from app.api.lib.geometry import haversine
from app.api.lib.osm_lib import populate_edge_length, routing_sanity_checks, validate_routing_graph, shortest_path, get_coords_osm

class RoutingUnavailable(ValueError):
  pass

class OutsideCoverage(ValueError):
  pass

class DirectedGraphBuilder(osmium.SimpleHandler):
  """Parse routable ways, access rules and node-based turn restrictions."""
  def __init__(self, routable: set, road_penalty: dict[str, float], mode: str="car") -> None:
    """Initialize graph construction with road types, penalties, and travel mode."""
    super().__init__()
    self.graph: nx.DiGraph = nx.DiGraph()
    self.ROUTABLE = routable
    self.ROAD_PENALTY = road_penalty
    self.mode = mode
    self._blocked_nodes = set()
    self._restrictions = []

  def _accessible(self, tags) -> bool:
    """Determine mode access using the most specific available OSM access tag."""
    keys = ("foot", "access") if self.mode == "walk" else ("motorcar", "motor_vehicle", "vehicle", "access")
    for key in keys:
      value = tags.get(key, "").lower()
      if value:
        return value not in {"no", "private", "agricultural", "forestry", "delivery", "customers", "destination", "permit"}
    return True

  def node(self, node) -> None:
    """Record nodes blocked by access tags or vehicle barriers."""
    barrier = node.tags.get("barrier", "")
    blocked = not self._accessible(node.tags)
    if self.mode == "car" and barrier in {"bollard", "block", "cycle_barrier", "stile", "turnstile"}:
      blocked = blocked or node.tags.get("motor_vehicle") not in {"yes", "designated"}
    if blocked:
      self._blocked_nodes.add(node.id)

  def relation(self, relation) -> None:
    """Collect supported vehicle turn restrictions and count unsupported relations."""
    if self.mode == "walk" or relation.tags.get("type") != "restriction":
      return
    if {"motorcar", "motor_vehicle"} & set(relation.tags.get("except", "").split(";")):
      return
    
    restriction = relation.tags.get("restriction:motorcar") or relation.tags.get("restriction:motor_vehicle") or relation.tags.get("restriction", "")
    members = {(member.role, member.type): member.ref for member in relation.members}
    from_way = members.get(("from", "w"))
    to_way = members.get(("to", "w"))
    via_node = members.get(("via", "n"))

    if from_way is not None and to_way is not None and via_node is not None and restriction.startswith(("no_", "only_")):
      self._restrictions.append((from_way, via_node, restriction, to_way))
    else:
      self.graph.graph["unsupported_turn_restrictions"] = self.graph.graph.get("unsupported_turn_restrictions", 0) + 1

  def way(self, w: osm.Way) -> None:
    """Add accessible road nodes and directed edges from an OSM way."""
    if w.tags.get("highway") not in self.ROUTABLE or not self._accessible(w.tags):
      return
    if self.mode == "walk" and w.tags.get("highway") in {"motorway", "motorway_link", "trunk", "trunk_link"} and w.tags.get("foot") not in {"yes", "designated", "permissive"}:
      return
    if self.mode == "car" and w.tags.get("highway") in {"footway", "path", "cycleway", "pedestrian", "steps", "bridleway"}:
      if not any(w.tags.get(key) in {"yes", "designated", "permissive"} for key in ("motorcar", "motor_vehicle")):
        return
    locations = []
    for nd in w.nodes:
      if not nd.location.valid():
        return
      locations.append((nd.ref, nd.location.lat, nd.location.lon))
    if len(locations) < 2:
      return
    for ref, lat, lon in locations:
      self.graph.add_node(ref, lat=lat, lon=lon)
    self._add_edges(w, [ref for ref, _, _ in locations])

  def _add_edges(self, w: osm.Way, node_refs: list[int]) -> None:
    """Add road edges according to mode-specific one-way rules."""
    highway = w.tags.get("highway")
    oneway = w.tags.get("oneway", "").lower()
    if self.mode == "walk":
      oneway = w.tags.get("oneway:foot", "no").lower()
    else:
      oneway = (w.tags.get("oneway:motorcar") or w.tags.get("oneway:motor_vehicle") or oneway).lower()
    if oneway in {"reversible", "alternating"}:
      return
    if self.mode != "walk" and not oneway and (w.tags.get("junction", "").lower() == "roundabout" or highway == "motorway"):
      oneway = "yes"

    forward_only = oneway in {"yes", "true", "1"}
    backward_only = oneway in {"-1", "reverse"}
    attrs = {
      "highway": highway,
      "maxspeed": w.tags.get("maxspeed"),
      "name": w.tags.get("name", ""),
      "surface": w.tags.get("surface", ""),
      "road_penalty": self.ROAD_PENALTY[highway],
      "length_m": 0.0,
      "way_id": w.id,
    }

    pairs = list(zip(node_refs, node_refs[1:]))
    if backward_only:
      pairs = [(v, u) for u, v in pairs]
    for u, v in pairs:
      if u != v and not self.graph.has_edge(u, v):
        self.graph.add_edge(u, v, **attrs)
      elif u != v and self.graph[u][v].get("way_id") != w.id:
        self.graph.graph["parallel_way_approximation"] = True
    if not forward_only and not backward_only:
      for u, v in zip(node_refs, node_refs[1:]):
        if u != v and not self.graph.has_edge(v, u):
          self.graph.add_edge(v, u, **attrs)

  def build(self, pbf_path: str) -> nx.DiGraph:
    """Parse an extract, remove blocked or isolated nodes, and attach turn rules."""
    self.apply_file(pbf_path, locations=True)
    self.graph.remove_nodes_from(self._blocked_nodes)
    self.graph.remove_nodes_from(list(nx.isolates(self.graph)))
    restrictions = {}
    
    for from_way, via_node, restriction, to_way in self._restrictions:
      restrictions.setdefault((from_way, via_node), []).append((restriction, to_way))
    self.graph.graph["turn_restrictions"] = restrictions
    return self.graph

def _unit_vector(lat: float, lon: float):
  """Convert latitude/longitude degrees to a unit-sphere Cartesian vector."""
  phi, lam = math.radians(lat), math.radians(lon)
  return math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)

def validate_coordinates(coord: tuple[float, float]) -> None:
  """Reject nonfinite coordinates or values outside geographic bounds."""
  lat, lon = coord
  if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
    raise OutsideCoverage("Coordinates must be finite latitude/longitude values within geographic bounds")

@dataclass(frozen=True)
class RoadJourney:
  coordinates: tuple[tuple[float, float], ...]
  distance: float
  time: float
  connector_distance: float
  estimated: bool = False

class RoadGraph:
  def __init__(self, graph: nx.DiGraph, max_snap_distance_m: float=1000, mode: str="car"):
    """Validate and freeze a road graph and build its nearest-node spatial index."""
    routing_sanity_checks(graph)
    self.graph = nx.freeze(graph)
    self.node_ids = tuple(graph.nodes())
    self.tree = cKDTree(np.array([_unit_vector(graph.nodes[n]["lat"], graph.nodes[n]["lon"]) for n in self.node_ids]))
    self.max_snap_distance_m = max_snap_distance_m
    self.mode = mode

  def snap(self, coord: tuple[float, float]) -> int:
    """Return the nearest road node within the configured coverage distance."""
    validate_coordinates(coord)
    _, index = self.tree.query(_unit_vector(*coord))
    node = self.node_ids[int(index)]
    attrs = self.graph.nodes[node]
    distance = haversine(coord, (attrs["lat"], attrs["lon"]))
    if distance > self.max_snap_distance_m:
      raise OutsideCoverage(f"Location is outside road coverage (maximum snap distance {self.max_snap_distance_m:g} metres)")
    return node

  def traverse(self, start: tuple[float, float], stop: tuple[float, float]) -> RoadJourney | None:
    """Plan a road journey with physical distance, travel time, and snap connectors."""
    start_node, stop_node = self.snap(start), self.snap(stop)
    nodes, distance = shortest_path(self.graph, start_node, stop_node)
    if nodes is None:
      return None
    coords = get_coords_osm(self.graph, nodes)
    connector_distance = haversine(start, coords[0]) + haversine(coords[-1], stop)
    travel_time = 0.0
    
    for u, v in zip(nodes, nodes[1:]):
      attrs = self.graph[u][v]
      speed = 5.0 if self.mode == "walk" else 45.0
      raw_speed = str(attrs.get("maxspeed") or "").lower().split(";")[0]
      try:
        value = float(raw_speed.replace("mph", "").replace("km/h", "").strip())
        if value > 0 and math.isfinite(value) and self.mode != "walk":
          speed = value * 1.609344 if "mph" in raw_speed else value
      except ValueError:
        pass
      travel_time += attrs["length_m"] / 1000 / speed * 60
    
    travel_time += connector_distance / 1000 / (5.0 if self.mode == "walk" else 45.0) * 60
    if start != coords[0]:
      coords.insert(0, start)
    if stop != coords[-1]:
      coords.append(stop)
    estimated = bool(self.graph.graph.get("unsupported_turn_restrictions") or self.graph.graph.get("parallel_way_approximation"))
    return RoadJourney(tuple(coords), distance + connector_distance, travel_time, connector_distance, estimated)

_road_graphs: dict[str, RoadGraph] = {}
_road_lock = RLock()

def initialize_road_graph(path: str | None=None, mode: str="car", *, routable=None, road_penalty=None, max_snap_distance_m=None) -> RoadGraph:
  """Load, validate, and publish the road graph for a travel mode."""
  with _road_lock:
    if path is None or routable is None or road_penalty is None or max_snap_distance_m is None:
      from app.core.config import settings
      path = path or settings.OSM_DATA_PATH
      routable = settings.ROUTABLE if routable is None else routable
      road_penalty = settings.ROAD_PENALTY if road_penalty is None else road_penalty
      max_snap_distance_m = settings.MAX_SNAP_DISTANCE_M if max_snap_distance_m is None else max_snap_distance_m
    if mode == "walk":
      routable = set(routable) | {"footway", "path", "pedestrian", "steps", "living_street", "service", "residential", "cycleway"}
      road_penalty = {road: 1.0 for road in routable}

    builder = DirectedGraphBuilder(set(routable), road_penalty, mode=mode)
    graph = builder.build(path)
    populate_edge_length(graph)
    road = RoadGraph(graph, max_snap_distance_m, mode)
    _road_graphs[mode] = road
    return road

def get_road_graph(mode: str="car") -> RoadGraph:
  """Return an initialized mode graph or report routing unavailability."""
  with _road_lock:
    road = _road_graphs.get(mode)
    if road is None:
      raise RoutingUnavailable("Road routing has not been initialized")
    return road

def reset_road_graph() -> None:
  """Clear all process-local road graphs under the registry lock."""
  with _road_lock:
    _road_graphs.clear()

def route_coordinates(start_coord: tuple[float, float], stop_coord: tuple[float, float], mode: str="car") -> RoadJourney | None:
  """Plan a coordinate journey using the initialized graph for a travel mode."""
  return get_road_graph(mode).traverse(start_coord, stop_coord)

if __name__ == "__main__":
  print(validate_routing_graph(initialize_road_graph().graph))
