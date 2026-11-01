"""Load and validate application settings and provision missing routing data files."""

from pathlib import Path
from pydantic import computed_field, PostgresDsn, field_validator, EmailStr, model_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode
from typing import Self, Annotated
import ast
import json
import math
import os
import tempfile
import fcntl
import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = os.environ.get("HHIKER_ENV_FILE", str(BASE_DIR / '.env')) or None

class Settings(BaseSettings):
  model_config = SettingsConfigDict(
    env_file = ENV_FILE,
    env_ignore_empty=True,
    extra="ignore"
  )

  API_V1_STR: str = "/api/v1"
  SECRET_KEY: str 
  ACCESS_TOKEN_EXPIRES_MINUTES: int = Field(default=60 * 24 * 8, gt=0)
  FRONTEND_HOST: str = "http://localhost:5173"
  PROJECT_NAME: str
  DATABASE_URL: PostgresDsn
  OPTIMISTIC_SPEED: float = Field(gt=0, allow_inf_nan=False)
  STANDARD_PRICE: float = Field(ge=0, allow_inf_nan=False)
  BASE_PRICE: float = Field(ge=0, allow_inf_nan=False)
  MAX_SNAP_DISTANCE_M: float = Field(default=1000, gt=0, allow_inf_nan=False)
  ROUTABLE: Annotated[set[str], NoDecode]
  ROAD_PENALTY: Annotated[dict[str, float], NoDecode]
  SUPABASE_URL: str | None = None
  SUPABASE_SERVICE_ROLE_KEY: str | None = None
  BUCKET_STR: str | None = None
  RATE_LIMIT_STORAGE_URI: str = "memory://"
  ROUTING_STARTUP_ENABLED: bool = True
  OSM_DATA_PATH: str | None = None
  STOPS_PATH: str | None = None
  ROUTES_PATH: str | None = None

  @model_validator(mode="after")
  def set_data_paths(self) -> Self:
    """Fill unset map and seed paths with defaults under /tmp/hhiker."""
    data_dir = Path("/tmp/hhiker")
    if self.OSM_DATA_PATH is None:
      self.OSM_DATA_PATH = str(data_dir / "abuja.osm.pbf")
    if self.STOPS_PATH is None:
      self.STOPS_PATH = str(data_dir / "data.json")
    if self.ROUTES_PATH is None:
      self.ROUTES_PATH = str(data_dir / "routes.json")
    return self

  @field_validator("ROUTABLE", "ROAD_PENALTY", mode="before")
  @classmethod
  def parse_routing_configuration(cls, value):
    """Parse road types and penalties from JSON or legacy Python literals."""
    if isinstance(value, str):
      try:
        return json.loads(value)
      except json.JSONDecodeError:
        try:
          return ast.literal_eval(value)
        except (SyntaxError, ValueError):
          raise ValueError("Routing configuration must use valid JSON or Python literals") from None
    return value

  @model_validator(mode="after")
  def validate_routing_configuration(self) -> Self:
    """Reject incomplete road penalties, invalid costs, and conflicting SMTP modes."""
    if not self.ROUTABLE or any(not road.strip() for road in self.ROUTABLE):
      raise ValueError("ROUTABLE must contain road types")
    if self.ROUTABLE - self.ROAD_PENALTY.keys():
      raise ValueError("ROAD_PENALTY must include every ROUTABLE road type")
    if any(not math.isfinite(value) or value <= 0 for value in self.ROAD_PENALTY.values()):
      raise ValueError("Road penalties must be positive finite numbers")
    if self.SMTP_TLS and self.SMTP_SSL:
      raise ValueError("Choose SMTP_TLS or SMTP_SSL, not both")
    return self

  def _download_file(self, remote_path: str, local_path: str) -> None:
    """Download a missing data file under a process lock and publish it atomically."""
    if not all((self.SUPABASE_URL, self.SUPABASE_SERVICE_ROLE_KEY, self.BUCKET_STR)):
      raise ValueError("Supabase storage settings are required to download missing data")
    url = f"{self.SUPABASE_URL}/storage/v1/object/{self.BUCKET_STR}/{remote_path}"
    headers = {"apikey": self.SUPABASE_SERVICE_ROLE_KEY,"Authorization": f"Bearer {self.SUPABASE_SERVICE_ROLE_KEY}"}
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Serialize downloads across processes; readers only see complete files.
    with destination.with_suffix(destination.suffix + ".lock").open("a") as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      if destination.is_file() and destination.stat().st_size:
        return
      temporary = None
      try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
          temporary = Path(file.name)
          with httpx.Client(timeout=120) as client:
            with client.stream("GET", url, headers=headers) as response:
              response.raise_for_status()
              for chunk in response.iter_bytes():
                file.write(chunk)
          file.flush()
          os.fsync(file.fileno())
        if not temporary.stat().st_size:
          raise ValueError("Downloaded data file is empty")
        temporary.replace(destination)
      finally:
        if temporary is not None:
          temporary.unlink(missing_ok=True)

  def ensure_data_files(self, *, include_seed: bool = False) -> None:
    """Download missing or empty map files and, optionally, transit seed files."""
    files = [("abuja.osm.pbf", self.OSM_DATA_PATH)]
    if include_seed:
      files.extend([("data.json", self.STOPS_PATH), ("route.json", self.ROUTES_PATH)])
    for remote, local in files:
      if not Path(local).is_file() or not Path(local).stat().st_size:
        self._download_file(remote, local)

  @field_validator("DATABASE_URL", mode="before")
  @classmethod
  def _use_psycopg_driver(cls, value: str | PostgresDsn) -> str:
    """Normalize PostgreSQL URL aliases to use the psycopg driver."""
    database_url = str(value)
    for scheme in ("postgres://", "postgresql://"):
      if database_url.startswith(scheme):
        return database_url.replace(scheme, "postgresql+psycopg://", 1)
    return database_url

  SMTP_TLS: bool = True
  SMTP_SSL: bool = False
  SMTP_PORT: int = 587
  SMTP_HOST: str | None = None
  SMTP_USER: str | None = None
  SMTP_PASSWORD: str | None = None
  EMAILS_FROM_EMAIL: EmailStr | None = None
  EMAILS_FROM_NAME: str | None = None

  @model_validator(mode="after")
  def _set_default_emails_from(self) -> Self:
    """Use the project name when no email sender name is configured."""
    if not self.EMAILS_FROM_NAME:
      self.EMAILS_FROM_NAME = self.PROJECT_NAME
    return self

  EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = Field(default=48, gt=0)

  @computed_field
  @property
  def emails_enabled(self) -> bool:
    """Report whether an SMTP host and sender email are configured."""
    return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)
  
  EMAIL_TEST_USER: EmailStr = "test@example.com"
  FIRST_SUPERUSER: EmailStr
  FIRST_SUPERUSER_PASSWORD: str

settings = Settings() # type: ignore
