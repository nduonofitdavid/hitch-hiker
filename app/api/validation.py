"""Shared validation and transaction boundaries for catalogue mutations."""
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import City, Country, Route, RouteIn, Stops, TransportMode, Place, PlaceType, ContributorRecord, FlagStop, FlagRoute, FlagPlace, Records, AdminAction

FLAG_TARGETS = {
  "stop": (FlagStop, Stops, "stop_id"),
  "route": (FlagRoute, Route, "route_id"),
  "place": (FlagPlace, Place, "place_id"),
}
CONTRIBUTION_TARGETS = {name: target for name, (_, target, _) in FLAG_TARGETS.items()}

def validate_geography(session: Session, city_id, country_id) -> tuple[City, Country]:
  """Lock the city and verify that it belongs to the supplied country."""
  city = session.exec(select(City).where(City.id == city_id).with_for_update()).first()
  country = session.get(Country, country_id)
  if city is None or country is None:
    raise HTTPException(status_code=404, detail="The city or country was not found")
  if city.country != country.id:
    raise HTTPException(status_code=400, detail="The city does not belong to this country")
  return city, country

def validate_place(session: Session, payload) -> None:
  """Validate a place's locked geography and optional place-type reference."""
  validate_geography(session, payload.city, payload.country)
  if payload.place_type is not None and session.get(PlaceType, payload.place_type) is None:
    raise HTTPException(status_code=400, detail="Place type not found")

def city_has_pending_edits(session: Session, city_id) -> bool:
  """Find canonical place proposals referencing a city, including reviewed history."""
  return session.exec(select(ContributorRecord.id).where(
    ContributorRecord.pending_place_id.is_not(None),
    ContributorRecord.proposed_changes["city"].as_string() == str(city_id),
  ).limit(1)).first() is not None

def stage_audit(session: Session, admin, message: str, action: AdminAction=AdminAction.EDITED) -> None:
  """Stage an administrative audit entry without committing its transaction."""
  with session.no_autoflush:
    session.add(Records(action=action, performed_by=admin.id, message=message))

def validate_route(session: Session, payload: RouteIn) -> list[TransportMode]:
  """Lock active route endpoints and resolve the unique requested transport modes."""
  if payload.start == payload.end:
    raise HTTPException(status_code=400, detail="Start and end stops must be different")
  
  stops = session.exec(select(Stops).where(Stops.id.in_([payload.start, payload.end])).order_by(Stops.id).with_for_update()).all()
  if len(stops) != 2 or any(not stop.is_active for stop in stops):
    raise HTTPException(status_code=400, detail="Both route endpoints must be active stops")
  if not payload.transport_mode or len(set(payload.transport_mode)) != len(payload.transport_mode):
    raise HTTPException(status_code=400, detail="Provide at least one unique transport mode")
  
  modes = session.exec(select(TransportMode).where(TransportMode.id.in_(payload.transport_mode))).all()
  if len(modes) != len(payload.transport_mode):
    raise HTTPException(status_code=400, detail="A transport mode provided does not exist")
  return modes

def commit_changes(session: Session, graph_changed: bool=False) -> None:
  """Commit mutations and an optional graph revision, rolling back failures."""
  try:
    if graph_changed:
      from app.api.lib.sage_engine import bump_graph_revision
      bump_graph_revision(session)
    session.commit()
  except IntegrityError:
    session.rollback()
    raise HTTPException(status_code=409, detail="The change conflicts with existing data")
  except Exception:
    session.rollback()
    raise
