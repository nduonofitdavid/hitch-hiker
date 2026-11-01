"""Parse and validate stop and route seed files before importing transit data."""

import json
import math
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class SeedStop:
  id: int
  name: str
  latitude: float
  longitude: float

@dataclass(frozen=True)
class SeedRoute:
  start: int
  end: int
  price: float
  time: float

def _seed_number(value: object, field: str, minimum: float, maximum: float) -> float:
  """Validate a finite seed number against inclusive bounds, rejecting booleans."""
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError(f'{field} must be a number')
  number = float(value)
  if not math.isfinite(number) or not minimum <= number <= maximum:
    raise ValueError(f'{field} must be finite and between {minimum} and {maximum}')
  return number

def load_seed_data(stop_file: str, route_file: str) -> tuple[list[SeedStop], list[SeedRoute]]:
  """Validate both JSON files and expand compatible bidirectional routes once."""
  with open(stop_file, encoding='utf-8') as file:
    raw_stops = json.load(file)
  with open(route_file, encoding='utf-8') as file:
    raw_routes = json.load(file)
  if not isinstance(raw_stops, list) or not isinstance(raw_routes, list):
    raise ValueError('Stop and route files must contain JSON lists')

  stops = {}
  locations = set()
  for row in raw_stops:
    if not isinstance(row, dict) or not {'id', 'name', 'latitude', 'longitude'} <= row.keys():
      raise ValueError('Each stop requires id, name, latitude, and longitude')
    if type(row['id']) is not int or row['id'] in stops:
      raise ValueError('Stop IDs must be unique integers')
    if not isinstance(row['name'], str) or not row['name'].strip() or len(row['name']) > 128:
      raise ValueError('Stop names must contain 1 to 128 characters')
    stop = SeedStop(row['id'], row['name'], _seed_number(row['latitude'], 'latitude', -90, 90), _seed_number(row['longitude'], 'longitude', -180, 180))
    location = (stop.latitude, stop.longitude)
    
    if location in locations:
      raise ValueError('Imported stops cannot share the same coordinates')
    stops[stop.id] = stop
    locations.add(location)

  directed_routes = {}
  for row in raw_routes:
    if not isinstance(row, dict) or not {'start', 'end', 'price', 'time', 'bi_directional'} <= row.keys():
      raise ValueError('Each route requires start, end, price, time, and bi_directional')
    if type(row['start']) is not int or type(row['end']) is not int or row['start'] not in stops or row['end'] not in stops:
      raise ValueError('Every route endpoint must reference a stop in the stop file')
    if row['start'] == row['end'] or type(row['bi_directional']) is not bool:
      raise ValueError('Routes require different endpoints and a boolean bi_directional')
    price = _seed_number(row['price'], 'price', 0, 999999999999.99)
    time = _seed_number(row['time'], 'time', 0, math.nextafter(1e308, 0))
    if Decimal(str(row['price'])) != Decimal(str(row['price'])).quantize(Decimal('0.01')):
      raise ValueError('Route prices must use no more than two decimal places')
    if time <= 0:
      raise ValueError('Route time must be greater than zero')
    endpoints = [(row['start'], row['end'])]
    if row['bi_directional']:
      endpoints.append((row['end'], row['start']))
   
    for start, end in endpoints:
      route = SeedRoute(start, end, price, time)
      if (start, end) in directed_routes and directed_routes[start, end] != route:
        raise ValueError('Conflicting fares or times for the same directed route')
      directed_routes[start, end] = route
  return list(stops.values()), list(directed_routes.values())
