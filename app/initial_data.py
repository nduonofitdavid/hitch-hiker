"""Command-line initialization of the administrator, routing downloads, and transit seed imports."""

import logging
import argparse
from sqlmodel import Session

from app.core.db import engine, init_db, add_nodes
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init(*, seed_transit: bool = False, download_data: bool = False, stop_file: str | None = None, route_file: str | None = None, country_name: str = 'Nigeria', city_name: str = 'Abuja') -> None:
  """Initialize the administrator and explicitly requested downloads or transit imports."""
  if download_data:
    settings.ensure_data_files(include_seed=True)
  with Session(engine) as session:
    init_db(session)
  if seed_transit:
    with Session(engine) as session:
      add_nodes(session, stop_file or settings.STOPS_PATH, route_file or settings.ROUTES_PATH, country_name=country_name, city_name=city_name)

def main(argv: list[str] | None = None) -> None:
  """Parse initialization options and run the requested provisioning steps."""
  parser = argparse.ArgumentParser(description='Initialize the administrator and optionally import validated transit data.')
  parser.add_argument('--seed-transit', action='store_true', help='Import stops and Car routes in one transaction; identical imports are safe to repeat.')
  parser.add_argument('--download-data', action='store_true', help='Explicitly download missing map and seed files before initialization.')
  parser.add_argument('--stops', help='Stop JSON file; defaults to STOPS_PATH.')
  parser.add_argument('--routes', help='Route JSON file; defaults to ROUTES_PATH.')
  parser.add_argument('--country', default='Nigeria', help='Country for imported stops.')
  parser.add_argument('--city', default='Abuja', help='City for imported stops.')
  args = parser.parse_args(argv)
  if not args.seed_transit and (args.stops or args.routes):
    parser.error('--stops and --routes require --seed-transit')
  logger.info("Creating initial data")
  init(seed_transit=args.seed_transit, download_data=args.download_data, stop_file=args.stops, route_file=args.routes, country_name=args.country, city_name=args.city)
  logger.info("Initial data created")

if __name__ == "__main__":
  main()
