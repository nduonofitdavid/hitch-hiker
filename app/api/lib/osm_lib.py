"""Measure and validate road graphs and find paths that respect supported turn restrictions."""

import networkx as nx
import numpy as np

import heapq
import itertools
import math

from app.api.lib.geometry import haversine_m

def populate_edge_length(G: nx.DiGraph) -> None:
  """Assign physical edge lengths in metres using vectorized great-circle distances."""
  if not G.number_of_edges():
    raise ValueError("The road extract contains no routable edges")
  u_arr = np.array([(G.nodes[u]['lat'], G.nodes[u]['lon']) for u, _ in G.edges()])
  v_arr = np.array([(G.nodes[v]['lat'], G.nodes[v]['lon']) for _, v in G.edges()])

  lengths = haversine_m(u_arr[:, 0], u_arr[:, 1], v_arr[:, 0], v_arr[:, 1])

  for (u, v), length in zip(G.edges(), lengths):
    G[u][v]["length_m"] = float(length)

def validate_routing_graph(G: nx.DiGraph) -> dict:
  """Report directed connectivity as well as physical edge integrity."""
  if not G.number_of_nodes() or not G.number_of_edges():
    raise ValueError("The road extract contains no routable roads")
  wcc = list(nx.weakly_connected_components(G))
  largest_wcc = max(wcc, key=len)
  coverage = len(largest_wcc) / G.number_of_nodes()

  dangling = [n for n in G.nodes() if G.degree(n) == 1]
  zero_length = [(u, v) for u, v, d in G.edges(data=True) if d.get("length_m", 1) <= 0]
  missing_highway = [(u, v) for u, v, d in G.edges(data=True) if not d.get("highway")]
  scc = list(nx.strongly_connected_components(G))

  metrics = {
    "total_nodes": G.number_of_nodes(),
    "total_edges": G.number_of_edges(),
    "weakly_connected_components": len(wcc),
    "largest_component_coverage": round(coverage, 4),
    "strongly_connected_components": len(scc),
    "largest_strong_component_coverage": round(max(map(len, scc)) / G.number_of_nodes(), 4),
    "dangling_nodes": len(dangling),
    "zero_length_edges": len(zero_length),
    "missing_highway_edges": len(missing_highway),
  }

  return metrics

def routing_sanity_checks(G: nx.DiGraph) -> None:
  """Reject invalid road graph types, coordinates, edge costs, and self-loops."""
  if not isinstance(G, nx.DiGraph):
    raise ValueError("Road routing requires a directed graph")

  missing_coords = [n for n, d in G.nodes(data=True) if "lat" not in d or "lon" not in d]
  if missing_coords:
    raise ValueError(f"{len(missing_coords)} road nodes have missing coordinates")
  invalid_coords = [n for n, d in G.nodes(data=True) if not math.isfinite(d["lat"]) or not math.isfinite(d["lon"]) or not -90 <= d["lat"] <= 90 or not -180 <= d["lon"] <= 180]
  if invalid_coords:
    raise ValueError(f"{len(invalid_coords)} road nodes have invalid coordinates")

  bad_lengths = [(u, v) for u, v, d in G.edges(data=True) if not math.isfinite(d.get("length_m", 0)) or d.get("length_m", 0) <= 0 or not math.isfinite(d.get("road_penalty", 1)) or d.get("road_penalty", 1) <= 0]
  if bad_lengths:
    raise ValueError(f"{len(bad_lengths)} road edges have non-positive length")

  loops = list(nx.selfloop_edges(G))
  if loops:
    raise ValueError(f"{len(loops)} road self-loops found")
  validate_routing_graph(G)

def shortest_path(G: nx.DiGraph, start_node: int, stop_node: int, key: str="length_m"):
  """Minimize weighted road cost with incoming-way turn state and return physical metres."""
  if start_node not in G or stop_node not in G:
    return None, float("inf")
  initial = (None, start_node, None)
  dist = {initial: 0.0}
  prev = {}
  visited = set()
  counter = itertools.count()
  pq = [(0.0, next(counter), initial)]
  restrictions = G.graph.get("turn_restrictions", {})
  final = None

  while pq:
    d, _, state = heapq.heappop(pq)
    if state in visited:
      continue
    visited.add(state)
    previous_node, u, incoming_way = state
    if u == stop_node:
      final = state
      break

    for v, attrs in G[u].items():
      outgoing_way = attrs.get("way_id")
      rules = restrictions.get((incoming_way, u), ())
      allowed = True
      
      for restriction, to_way in rules:
        matches = outgoing_way == to_way
        if restriction.endswith("u_turn"):
          matches = matches and v == previous_node
        if restriction.startswith("no_") and matches:
          allowed = False
        elif restriction.startswith("only_") and not matches:
          allowed = False

      if not allowed:
        continue
      cost = attrs[key] * attrs.get("road_penalty", 1.0)
      if not math.isfinite(cost) or cost <= 0:
        raise ValueError("Road costs must be positive and finite")
      
      nd = d + cost
      next_state = (u, v, outgoing_way) if restrictions else (None, v, None)
      if nd < dist.get(next_state, float("inf")):
        dist[next_state] = nd
        prev[next_state] = state
        heapq.heappush(pq, (nd, next(counter), next_state))

  if final is None:
    return None, float("inf")
  
  path = [final[1]]
  while final != initial:
    final = prev[final]
    path.append(final[1])
  path.reverse()

  distance = sum(G[u][v]["length_m"] for u, v in zip(path, path[1:]))
  return path, float(distance)

def get_coords_osm(G: nx.DiGraph, path: list[int]) -> list[tuple[float, float]]:
  """Return latitude/longitude pairs for road nodes in path order."""
  coords = [
    (G.nodes[node_id]['lat'], G.nodes[node_id]['lon'])
    for node_id in path
  ]
  return coords
