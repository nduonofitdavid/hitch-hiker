import uuid
from datetime import UTC, datetime
from dataclasses import dataclass
from sqlmodel import SQLModel, Field, UniqueConstraint, Relationship
from pydantic import EmailStr
from sqlalchemy import DateTime

from enum import IntEnum

def get_datetime_utc() -> datetime:
  return datetime.now(UTC)

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
  places_visited: list["Place"] = Relationship(back_populates="visited_users", link_model=PlacesUserVisitedLink)
  created_at: datetime | None = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

class UserPublic(UserBase):
  id: uuid.UUID
  created_at: datetime | None = None

class UsersPublic(SQLModel):
  data: list[UserPublic]
  count: int

class Message(SQLModel):
  message: str

class Country(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=128, unique=True, index=True)

class CityBase(SQLModel):
  name: str
  country: uuid.UUID

class City(CityBase, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=128, unique=True, index=True)
  country: uuid.UUID = Field(foreign_key="country.id", index=True)

class StopsIn(SQLModel):
  name: str = Field(max_length=128)
  latitude: float
  longitude: float
  city: uuid.UUID
  country: uuid.UUID

class StopsOut(SQLModel):
  id: uuid.UUID
  name: str = Field(max_length=128)
  city: uuid.UUID
  country: uuid.UUID

class Stops(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=128, unique=True)
  latitude: float
  longitude: float
  opposite: uuid.UUID | None = Field(default=None, foreign_key="stops.id", index=True)
  is_active: bool = Field(default=True)
  city: uuid.UUID = Field(foreign_key="city.id")
  country: uuid.UUID = Field(foreign_key="country.id")
  contributed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")

  def get_coords(self) -> tuple[float, float]:
    return self.latitude, self.longitude

  __table_args__ = (
    UniqueConstraint(
      "latitude",
      "longitude",
      "city",
      "country",
      name="Unique location constraint"
    ),
  )

@dataclass(slots=True)
class CachedStop:
  id: uuid.UUID
  name: str
  latitude: float
  longitude: float
  opposite: uuid.UUID | None
  city: uuid.UUID
  country: uuid.UUID

  def get_coords(self) -> tuple[float, float]:
    return self.latitude, self.longitude

@dataclass
class CoordTStopOut:
  rb: "RouteBlock"
  dist: float

class TransportModeRouteLink(SQLModel, table=True):
  transport_mode_id: uuid.UUID | None = Field(default=None, foreign_key="transportmode.id", primary_key=True)
  route_id: uuid.UUID | None = Field(default=None, foreign_key="route.id", primary_key=True)
  
class TransportMode(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=50, unique=True)
  routes: list["Route"] = Relationship(back_populates="transport_modes", link_model=TransportModeRouteLink)

class Coordinates(SQLModel):
  latitude: float
  longitude: float

class RouteIn(SQLModel):
  start: uuid.UUID
  end: uuid.UUID
  time: float = Field(gt=1.0)
  price: float = Field(gt=1.0)
  transport_mode: list[uuid.UUID]

# think about how to sort this stuff in the context of train can be faster than car, and have a different price
class Route(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  start: uuid.UUID = Field(foreign_key="stops.id")
  end: uuid.UUID = Field(foreign_key="stops.id")
  time: float = Field(gt=1.0)
  price: float = Field(gt=1.0)
  is_active: bool = Field(default=True)
  contributed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)
  transport_modes: list["TransportMode"] = Relationship(back_populates="routes", link_model=TransportModeRouteLink)

@dataclass(slots=True)
class CachedRoute:
  start: uuid.UUID
  end: uuid.UUID
  time: float
  price: float
  transport_modes: list[str]

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
  message: str = Field(max_length=256)

# Trip stuff
class TripIn(SQLModel):
  start_latitude: float
  start_longitude: float
  stop_latitude: float
  stop_longitude: float

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
  price: float
  time: float = Field(default=0.0)
  start_latitude: float
  start_longitude: float
  stop_latitude: float
  stop_longitude: float
  country: uuid.UUID | None = Field(default=None, foreign_key="country.id", index=True)
  city: uuid.UUID | None = Field(default=None, foreign_key="city.id", index=True)
  trip_status: TripStatus = Field(default=TripStatus.PENDING)
  carried_out_at: datetime = Field(default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)) # type: ignore

class UserStats(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  user_id: uuid.UUID = Field(foreign_key="user.id", index=True, unique=True)
  no_of_trips: int
  distance_covered: float
  total_spent: float
  total_time: float

class UserStatsOut(SQLModel):
  no_of_trips: int
  distance_covered: float
  total_spent: float
  total_time: float
  average_time: float

# contributor
class Contributor(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
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
  description: str = Field(min_length=15, max_length=128)
  reviewed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)

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

class ContributorPlaceUpdate(SQLModel):
  name: str = Field(max_length=128)
  place_type: uuid.UUID
  description: str = Field(min_length=15, max_length=128)

# places
class PlaceType(SQLModel, table=True):
  id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
  name: str = Field(max_length=50, index=True)
  description: str = Field(min_length=25, max_length=128)

class PlaceInUser(SQLModel):
  name: str = Field(max_length=128)
  latitude: float
  longitude: float
  description: str = Field(min_length=50, max_length=256)
  place_type: uuid.UUID | None
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
    UniqueConstraint(
      "name",
      "latitude",
      "longitude",
      "city",
      "country",
      name="Unique place constraint"
    ),
  )

# auth
class Token(SQLModel):
  access_token: str
  token_type: str = "bearer"

class TokenPayload(SQLModel):
  sub: str | None = None

class NewPassword(SQLModel):
  token: str
  new_password: str = Field(min_items=8, max_length=128)