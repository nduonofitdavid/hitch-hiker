"""Create the FastAPI app, manage routing startup, and expose health checks."""

from contextlib import asynccontextmanager
import logging
from time import perf_counter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlmodel import Session
from sqlalchemy import text
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.rlimiter import limiter
from app.api.main import api_router
from app.api.lib.sage_engine import initialize_routing, reset_routing, loader
from app.core.db import engine

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
  """Initialize routing at startup and release graphs and database connections on exit."""
  app.state.routing_ready = False
  try:
    if settings.ROUTING_STARTUP_ENABLED:
      await run_in_threadpool(settings.ensure_data_files)
      await run_in_threadpool(initialize_routing)
      app.state.routing_ready = True
      logger.info("Routing initialized with %d active stops", len(loader.snapshot().stops))
    yield
  finally:
    app.state.routing_ready = False
    reset_routing()
    engine.dispose()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(
  CORSMiddleware,
  allow_origins=[settings.FRONTEND_HOST],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(api_router, prefix=settings.API_V1_STR)

@app.middleware("http")
async def log_request(request, call_next):
  """Log response status and duration using route templates that omit request values."""
  started = perf_counter()
  response = await call_next(request)
  route = request.scope.get("route")
  # Log route templates, excluding tokens, email path values and query strings.
  logger.info("%s %s status=%d duration_ms=%.1f", request.method, getattr(route, "path", "unmatched"), response.status_code, (perf_counter() - started) * 1000)
  return response

@app.get("/health/live", include_in_schema=False)
def liveness():
  """Report that the HTTP process can respond without checking external services."""
  return {"status": "ok"}

@app.get("/health/ready", include_in_schema=False)
def readiness():
  """Report readiness only when routing is initialized and database checks succeed."""
  if not getattr(app.state, "routing_ready", False):
    return JSONResponse(status_code=503, content={"status": "unavailable"})
  try:
    with Session(engine) as session:
      session.execute(text("SELECT 1"))
      loader.ensure_current(session)
  except Exception as exc:
    logger.warning("Readiness failed (%s)", type(exc).__name__)
    return JSONResponse(status_code=503, content={"status": "unavailable"})
  return {"status": "ok"}
