"""Database tables and API schemas for accounts, transport, trips, places, and contributions."""

import uuid
from datetime import UTC, datetime
from dataclasses import dataclass
from sqlmodel import SQLModel, Field, UniqueConstraint, Relationship
from pydantic import EmailStr, Field as ValidationField, StringConstraints, field_validator, model_validator
from sqlalchemy import DateTime, JSON, Text, CheckConstraint, Numeric, Index, func
from typing import Annotated, Any, Self

from enum import IntEnum

def get_datetime_utc() -> datetime:
  """Return the current timezone-aware UTC timestamp."""
  return datetime.now(UTC)

Latitude = Annotated[float, ValidationField(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, ValidationField(ge=-180, le=180, allow_inf_nan=False)]
Money = Annotated[float, ValidationField(ge=0, lt=1e12, allow_inf_nan=False, multiple_of=0.01)]
Duration = Annotated[float, ValidationField(gt=0, lt=1e308, allow_inf_nan=False)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class UserBase(SQLModel):
  email: EmailStr = Field(unique=True, index=True, max_length=255)
  is_active: bool = True
  is_superuser: bool = False
  full_name: str | None = Field(default=None, max_length=255)

class UserCreate(UserBase):
  password: str = Field(min_length=8, max_length=128)

class UserRegister(SQLModel):
  email: EmailStr = Field(max_length=255)
  password: str = Field(min_length=8, max_length=128)
  full_name: str | None = Field(default=None, max_length=255)

class UserUpdate(SQLModel):
  email: EmailStr | None = Field(default=None, max_length=255)
  is_active: bool | None = None
  is_superuser: bool | None = None
  full_name: str | None = Field(default=None, max_length=255)
  password: str | None = Field(default=None, min_length=8, max_length=128)

class PlacesUserVisitedLink(SQLModel, table=True):
  places_id: uuid.UUID | None = Field(default=None, foreign_key="place.id", primary_key=True)
  user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", primary_key=True)

class User(UserBase, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  hashed_password: str
  token_version: int = Field(default=0)
  places_visited: list["Place"] = Relationship(back_populates="visited_users", link_model=PlacesUserVisitedLink)
  created_at: datetime | None = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

class UserPublic(UserBase):
  id: uuid.UUID
  created_at: datetime | None = None

Index("ix_user_email_normalized", func.lower(User.__table__.c.email), unique=True)

class UsersPublic(SQLModel):
  data: list[UserPublic]
  count: int

class Message(SQLModel):
  message: str

class Country(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: Name = Field(max_length=128, unique=True, index=True)

class CityBase(SQLModel):
  name: Name = Field(max_length=128)
  country: uuid.UUID

class City(CityBase, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=128, index=True)
  country: uuid.UUID = Field(foreign_key="country.id", index=True)
  __table_args__ = (UniqueConstraint("name", "country", name="uq_city_name_country"),)

class StopsIn(SQLModel):
  name: Name = Field(max_length=128)
  latitude: Latitude
  longitude: Longitude
  city: uuid.UUID
  country: uuid.UUID

class StopsOut(SQLModel):
  id: uuid.UUID
  name: str = Field(max_length=128)
  city: uuid.UUID
  country: uuid.UUID

class Stops(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=128)
  latitude: Latitude
  longitude: Longitude
  opposite: uuid.UUID | None = Field(default=None, foreign_key="stops.id", index=True)
  is_active: bool = Field(default=True)
  city: uuid.UUID = Field(foreign_key="city.id")
  country: uuid.UUID = Field(foreign_key="country.id")
  contributed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")

  def get_coords(self) -> tuple[float, float]:
    """Return this stored stop's coordinates as latitude and longitude."""
    return self.latitude, self.longitude

  __table_args__ = (
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_stops_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_stops_longitude"),
    UniqueConstraint(
      "latitude",
      "longitude",
      "city",
      "country",
      name="Unique location constraint"
    ),
  )

@dataclass(slots=True, frozen=True)
class CachedStop:
  id: uuid.UUID
  name: str
  latitude: float
  longitude: float
  opposite: uuid.UUID | None
  city: uuid.UUID
  country: uuid.UUID

  def get_coords(self) -> tuple[float, float]:
    """Return this cached stop's coordinates as latitude and longitude."""
    return self.latitude, self.longitude

class TransportModeRouteLink(SQLModel, table=True):
  transport_mode_id: uuid.UUID | None = Field(default=None, foreign_key="transportmode.id", primary_key=True)
  route_id: uuid.UUID | None = Field(default=None, foreign_key="route.id", primary_key=True)
  
class TransportMode(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=50, unique=True)
  routes: list["Route"] = Relationship(back_populates="transport_modes", link_model=TransportModeRouteLink)

class Coordinates(SQLModel):
  latitude: Latitude
  longitude: Longitude

class RouteIn(SQLModel):
  start: uuid.UUID
  end: uuid.UUID
  time: Duration
  price: Money
  transport_mode: list[uuid.UUID] = Field(min_length=1)

  @field_validator("transport_mode")
  @classmethod
  def unique_modes(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
    """Reject repeated transport mode IDs in a route submission."""
    if len(value) != len(set(value)):
      raise ValueError("Transport modes must be unique")
    return value

  @model_validator(mode="after")
  def distinct_stops(self) -> Self:
    """Reject a route whose start and end refer to the same stop."""
    if self.start == self.end:
      raise ValueError("Start and end stops must be different")
    return self

# think about how to sort this stuff in the context of train can be faster than car, and have a different price
class Route(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  start: uuid.UUID = Field(foreign_key="stops.id")
  end: uuid.UUID = Field(foreign_key="stops.id")
  time: Duration
  price: Money = Field(sa_type=Numeric(14, 2, asdecimal=False))
  is_active: bool = Field(default=True)
  contributed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)
  transport_modes: list["TransportMode"] = Relationship(back_populates="routes", link_model=TransportModeRouteLink)
  __table_args__ = (
    CheckConstraint("start <> \"end\"", name="ck_route_distinct_stops"),
    CheckConstraint("time > 0 AND time < 1e308", name="ck_route_time"),
    CheckConstraint("price >= 0 AND price < 1e12", name="ck_route_price"),
  )

@dataclass(slots=True, frozen=True)
class CachedRoute:
  id: uuid.UUID
  start: uuid.UUID
  end: uuid.UUID
  time: float
  price: float
  transport_modes: tuple[str, ...]

class TransportModeOut(SQLModel):
  id: uuid.UUID
  name: str

class RouteOut(SQLModel):
  id: uuid.UUID
  start: uuid.UUID
  end: uuid.UUID
  time: float
  price: float
  is_active: bool
  contributed_by: uuid.UUID | None = None
  transport_modes: list[TransportModeOut]

class AdminAction(IntEnum):
  CREATED=0
  DELETED=1
  EDITED=2
  DISABLED=3

class OptimizerWeight(IntEnum):
  PRICE=0
  TIME=1

class Records(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  action: AdminAction 
  performed_by: uuid.UUID = Field(foreign_key="user.id")
  performed_at: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore
  message: str = Field(sa_type=Text)

# Trip stuff
class TripIn(SQLModel):
  start_latitude: Latitude
  start_longitude: Longitude
  stop_latitude: Latitude
  stop_longitude: Longitude

class RouteTripOut(SQLModel):
  time: float
  price: float
  transport_modes: list[str] | None

class StopTripOut(SQLModel):
  name: str
  latitude: float
  longitude: float

class RouteBlock(SQLModel):
  route_position: int
  start_node: StopTripOut | None=None
  stop_node: StopTripOut | None=None
  route: RouteTripOut | None=None
  route_geometry: list[tuple[float, float]]
  route_id: uuid.UUID | None = None
  distance: float | None = None
  geometry_source: str = "road"

class TripOut(SQLModel):
  route_count: int
  route_blocks: list[RouteBlock]
  trip_id: uuid.UUID

class TripStatus(IntEnum):
  PENDING=0
  COMPLETED=1

class TripStore(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
  distance: float
  price: Money = Field(sa_type=Numeric(14, 2, asdecimal=False))
  time: float = Field(default=0.0)
  start_latitude: float
  start_longitude: float
  stop_latitude: float
  stop_longitude: float
  country: uuid.UUID | None = Field(default=None, foreign_key="country.id", index=True)
  city: uuid.UUID | None = Field(default=None, foreign_key="city.id", index=True)
  trip_status: TripStatus = Field(default=TripStatus.PENDING)
  carried_out_at: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore
  completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
  itinerary: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
  optimize_by: OptimizerWeight | None = None
  distance_estimated: bool = False

  __table_args__ = (
    CheckConstraint("distance >= 0 AND distance < 1e308", name="ck_tripstore_distance"),
    CheckConstraint("price >= 0 AND price < 1e12", name="ck_tripstore_price"),
    CheckConstraint("time >= 0 AND time < 1e308", name="ck_tripstore_time"),
  )

class UserStats(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  user_id: uuid.UUID = Field(foreign_key="user.id", index=True, unique=True)
  no_of_trips: int
  distance_covered: float
  total_spent: Money = Field(sa_type=Numeric(14, 2, asdecimal=False))
  total_time: float = Field(default=0.0)
  __table_args__ = (
    CheckConstraint("no_of_trips >= 0 AND distance_covered >= 0 AND distance_covered < 1e308 AND total_spent >= 0 AND total_spent < 1e12 AND total_time >= 0 AND total_time < 1e308", name="ck_userstats_nonnegative"),
  )

class GraphRevision(SQLModel, table=True):
  id: int = Field(default=1, primary_key=True)
  version: int = 0

class UserStatsOut(SQLModel):
  no_of_trips: int
  distance_covered: float
  total_spent: float
  total_time: float
  average_time: float

# contributor
class Contributor(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  user_id: uuid.UUID = Field(foreign_key="user.id", unique=True, index=True)
  is_enabled: bool = False
  date_joined: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore
  approved_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)

class ContributionStatus(IntEnum):
  ACCEPTED=0
  PENDING=1
  DENIED=2

class ContributorAction(IntEnum):
  CREATE=0
  UPDATE=2
  REQUEST_DELETION=3

class ContributorRecord(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  contributor_id: uuid.UUID = Field(foreign_key="contributor.id", index=True)
  action_type: ContributorAction
  contribution_status: ContributionStatus
  description: str = Field(sa_type=Text)
  reviewed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)
  target_type: str | None = Field(default=None, max_length=16)
  target_id: uuid.UUID | None = Field(default=None, index=True)
  proposed_changes: dict[str, Any] | None = Field(default=None, sa_type=JSON)
  pending_place_id: uuid.UUID | None = Field(default=None, unique=True, index=True)
  original_data: dict[str, Any] | None = Field(default=None, sa_type=JSON)
  submitted_at: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True))
  reviewed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))

class FlagStatus(IntEnum):
  PENDING=0
  RESOLVED=1

class FlagPlace(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  flagged_by: uuid.UUID = Field(foreign_key="user.id", index=True)
  reason: str = Field(min_length=15, max_length=128)
  place_id: uuid.UUID = Field(foreign_key="place.id", index=True)
  status: FlagStatus = Field(default=FlagStatus.PENDING)
  date_flagged: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

class FlagStop(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  flagged_by: uuid.UUID = Field(foreign_key="user.id", index=True)
  reason: str = Field(min_length=15, max_length=128)
  stop_id: uuid.UUID = Field(foreign_key="stops.id", index=True)
  status: FlagStatus = Field(default=FlagStatus.PENDING)
  date_flagged: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

class FlagRoute(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  flagged_by: uuid.UUID = Field(foreign_key="user.id", index=True)
  reason: str = Field(min_length=15, max_length=128)
  route_id: uuid.UUID = Field(foreign_key="route.id", index=True)
  status: FlagStatus = Field(default=FlagStatus.PENDING)
  date_flagged: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

# places
class PlaceType(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=50, index=True)
  description: str = Field(min_length=25, max_length=128)

class PlaceInUser(SQLModel):
  name: Name = Field(max_length=128)
  latitude: Latitude
  longitude: Longitude
  description: str = Field(min_length=50, max_length=256)
  place_type: uuid.UUID | None = None
  city: uuid.UUID
  country: uuid.UUID

class PlaceInAdmin(PlaceInUser):
  is_active: bool

class Place(PlaceInAdmin, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  place_type: uuid.UUID | None = Field(default=None, foreign_key="placetype.id")
  city: uuid.UUID = Field(foreign_key="city.id", index=True)
  country: uuid.UUID = Field(foreign_key="country.id", index=True)
  visited_users: list[User] = Relationship(back_populates="places_visited", link_model=PlacesUserVisitedLink)
  created_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)

  __table_args__ = (
    CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_place_latitude"),
    CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_place_longitude"),
    UniqueConstraint(
      "name",
      "latitude",
      "longitude",
      "city",
      "country",
      name="Unique place constraint"
    ),
  )

class PendingPlaceOut(PlaceInUser):
  id: uuid.UUID
  place_id: uuid.UUID
  record_id: uuid.UUID
  submitted_by: uuid.UUID | None
  status: ContributionStatus
  submitted_at: datetime
  reviewed_at: datetime | None

  @classmethod
  def from_record(cls, record: ContributorRecord, submitted_by: uuid.UUID) -> Self:
    """Project a canonical contribution into the existing pending-edit response shape."""
    return cls.model_validate({
      **(record.proposed_changes or {}),
      "id": record.pending_place_id,
      "place_id": record.target_id,
      "record_id": record.id,
      "submitted_by": submitted_by,
      "status": record.contribution_status,
      "submitted_at": record.submitted_at,
      "reviewed_at": record.reviewed_at,
    })

class ReviewDecision(SQLModel):
  accept: bool

class ContributorActivation(SQLModel):
  is_enabled: bool

class LookupName(SQLModel):
  name: Name = Field(max_length=50)

class PlaceTypeIn(LookupName):
  description: str = Field(min_length=25, max_length=128)

# auth
class Token(SQLModel):
  access_token: str
  token_type: str = "bearer"

class TokenPayload(SQLModel):
  sub: str | None = None

class NewPassword(SQLModel):
  token: str
  new_password: str = Field(min_length=8, max_length=128)
