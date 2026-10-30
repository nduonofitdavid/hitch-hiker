from datetime import datetime
import uuid
from fastapi import APIRouter, Query, Request
from sqlmodel import select

from app.api.deps import SessionDep, CurrentUser
from app.models import TripStore, UserStats, UserStatsOut
from app.core.rlimiter import limiter

router = APIRouter(prefix="/user", tags=["users", "user"])

@router.get("/trips/summary")
@limiter.limit("5/minute")
def get_trips(request: Request, session: SessionDep, user: CurrentUser, start_date: datetime|None = None, end_date: datetime|None=None, country: uuid.UUID|None=None, city: uuid.UUID|None=None, offset: int=0, limit: int = Query(default=100, le=100)):
  """Get information about a users trips"""
  query = select(TripStore).where(TripStore.user_id == user.id)
  if start_date:
    query = query.where(TripStore.carried_out_at >= start_date)
  if end_date:
    query = query.where(TripStore.carried_out_at <= end_date)
  if country:
    query = query.where(TripStore.country == country)
  if city:
    query = query.where(TripStore.city == city)
  results = session.exec(query.offset(offset).limit(limit)).all()
  return results

@router.get("/stats", response_model=UserStatsOut)
@limiter.limit("5/minute")
def get_user_stats(request: Request, session: SessionDep, user: CurrentUser):
  """Get stats about a user, like distance covered, most visited city, etc."""
  stats = session.exec(select(UserStats).where(UserStats.user_id == user.id)).first()
  if not stats:
    return {"no_of_trips": 0, "distance_covered": 0, "total_spent": 0, "total_time": 0, "average_time": 0}
  return {"no_of_trips": stats.no_of_trips, "distance_covered": stats.distance_covered, "total_spent": stats.total_spent, "total_time": stats.total_time, "average_time": stats.total_time / stats.no_of_trips}