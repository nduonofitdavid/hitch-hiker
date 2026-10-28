# Hitch Hiker

A Python/FastAPI backend for finding transport routes through an unfamiliar city. It models local stops and connections so a traveller can see where to change transport, which modes to use, and the associated fares and travel times. The current data setup targets Abuja, Nigeria.

## How it works

- **Transport routing:** a graph of stops and directed routes stored in PostgreSQL. The API uses Dijkstra's algorithm to minimize recorded fare or travel time; the engine also includes optional A* support.
- **Road routing:** a local OpenStreetMap extract is parsed into a NetworkX graph. Coordinates are snapped to road nodes, and road paths provide route geometry and estimated car journeys.
- **Trip records:** responses contain ordered route segments and a trip ID. Trips can be marked complete and queried alongside user statistics.

The API also includes JWT authentication, password recovery, place lookups, administration endpoints, and contributor submissions and flags.

## Setup

Requires Python and PostgreSQL. The repository does not pin a Python version; use one compatible with `requirements.txt`.

```sh
git clone https://github.com/nduonofitdavid/hitch-hiker.git
cd hitch-hiker
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Create `app/.env` with these required settings:

| Purpose | Settings |
| --- | --- |
| Application | `PROJECT_NAME`, `SECRET_KEY` |
| PostgreSQL connection | `DATABASE_URL`, e.g. `postgresql+psycopg://user:password@localhost/hitch_hiker` |
| Initial administrator | `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD` |
| Routing and fare estimates | `OPTIMISTIC_SPEED`, `STANDARD_PRICE`, `BASE_PRICE` |
| Road types and weights | `ROUTABLE`, `ROAD_PENALTY` |
| Supabase Storage | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `BUCKET_STR` |

`ROUTABLE` and `ROAD_PENALTY` are Python-literal strings, for example `{'primary', 'residential'}` and `{'primary': 1.0, 'residential': 1.2}`. Include a penalty for each configured road type.

For automatic downloads, the storage bucket must contain `abuja.osm.pbf`, `data.json` (stops), and `route.json` (routes). Missing files download when configuration loads, normally into `/tmp/hhiker/`. To use existing local files, set `OSM_DATA_PATH`, `STOPS_PATH`, and `ROUTES_PATH`; the Supabase settings remain required. SMTP settings and `EMAILS_FROM_EMAIL` enable email delivery.

Create the database, then run migrations and initialize the administrator from the repository root:

```sh
PYTHONPATH=app alembic -c app/alembic.ini upgrade head
python -m app.initial_data
```

Transit-data seeding is currently manual: `app.core.db.add_nodes` imports the stop/route JSON files, but its invocation is commented out in `init_db`. Initialization creates the administrator only. Load transit records before starting the API; migrations and schema compatibility also need validation on a fresh database.

```sh
uvicorn app.main:app --reload
```

## API usage

Open `http://127.0.0.1:8000/docs` for interactive requests. Routes use the default `/api/v1` prefix. Log in through `/auth/login/access-token` with an OAuth2 password form (`username` is the email address), then authorize requests with the returned bearer token.

| Endpoint | Purpose |
| --- | --- |
| `POST /trip/` | Stop-to-stop routing; query parameters: `start_id`, `end_id` (UUIDs), `optimize_by` (`0` price, `1` time). |
| `POST /trip/default` | Road routing; JSON fields: `start_latitude`, `start_longitude`, `stop_latitude`, `stop_longitude`. |
| `POST /trip/complete/{trip_id}` | Mark a saved trip complete. |
| `GET /ye/stops`, `/ye/city`, `/ye/country`, `/places/` | Look up transport stops, locations, and places. |
| `GET /user/trips/summary`, `/user/stats` | Retrieve trip history and statistics. |

Paths in the table are relative to `/api/v1`. Account endpoints live under `/auth`, management under `/admin`, and contributions under `/contribute`.

## Source layout

- `app/api/router/` — HTTP endpoints.
- `app/api/lib/` — transport and road graphs, geometry, and fare/time helpers.
- `app/models.py`, `app/crud.py` — database models and account operations.
- `app/core/` — settings, database initialization, authentication, and rate limiting.
- `app/alembic/`, `app/email-templates/` — database migrations and email templates.

## Current limitations

This is a prototype. Transit routing currently requires known stops; automatic connections from arbitrary coordinates to transit stops are unfinished. Coverage depends on supplied data, and fare/time estimates do not use live traffic or pricing. There is no bundled frontend or automated test suite.

Some admin, contributor, routing, and statistics workflows need fixes. Public sign-up currently defaults to superuser access, and contributor approval is incomplete; these need correction before public deployment.

Licensed under the **MIT LICENSE**; see the repository's [LICENSE](https://github.com/nduonofitdavid/hitch-hiker/blob/HEAD/LICENSE).
