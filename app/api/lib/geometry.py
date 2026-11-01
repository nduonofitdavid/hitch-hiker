"""Great-circle distances for individual coordinates and NumPy arrays."""
import math

import numpy as np

EARTH_RADIUS_M = 6_371_000.0

def _haversine_m(lat1, lon1, lat2, lon2, *, vectorized=False):
  """Apply one distance formula with scalar math or vectorized NumPy operations."""
  if vectorized:
    radians, sin, cos, sqrt, atan2 = np.radians, np.sin, np.cos, np.sqrt, np.arctan2
  else:
    radians, sin, cos, sqrt, atan2 = math.radians, math.sin, math.cos, math.sqrt, math.atan2
  phi1, phi2 = radians(lat1), radians(lat2)
  dphi, dlam = radians(lat2 - lat1), radians(lon2 - lon1)
  a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
  a = np.clip(a, 0.0, 1.0) if vectorized else min(1.0, max(0.0, a))
  return 2 * EARTH_RADIUS_M * atan2(sqrt(a), sqrt(1 - a))

def haversine_m(lat1, lon1, lat2, lon2):
  """Return metres between WGS84 points, supporting broadcastable coordinate arrays."""
  return _haversine_m(lat1, lon1, lat2, lon2, vectorized=True)

def haversine(first: tuple[float, float], second: tuple[float, float]) -> float:
  """Return the great-circle distance in metres between latitude/longitude pairs."""
  return _haversine_m(*first, *second)
