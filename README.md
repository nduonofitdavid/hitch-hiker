# Hitch Hiker

A FastAPI backend for planning transport routes, estimating fares and travel times, and saving trips. The current data setup targets Abuja, Nigeria.

## Setup

Requires Python 3.11 or 3.12, PostgreSQL, and Linux.

```sh
git clone https://github.com/nduonofitdavid/hitch-hiker.git
cd hitch-hiker
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Create a PostgreSQL database and add `app/.env`, replacing the placeholder credentials and paths:

```dotenv
PROJECT_NAME="Hitch Hiker"
SECRET_KEY=replace-with-a-random-secret
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/hitch_hiker
FIRST_SUPERUSER=admin@example.com
FIRST_SUPERUSER_PASSWORD=replace-with-a-strong-password
OPTIMISTIC_SPEED=40
STANDARD_PRICE=100
BASE_PRICE=200
ROUTABLE=["primary", "residential"]
ROAD_PENALTY={"primary": 1.0, "residential": 1.2}
OSM_DATA_PATH=/absolute/path/to/abuja.osm.pbf
```

Supply a local OpenStreetMap `.osm.pbf` extract at `OSM_DATA_PATH`. Speed is in km/h; `STANDARD_PRICE` is the minimum positive fare and `BASE_PRICE` the fare per started 100 metres, in NGN. Adjust these values for your data.

Run migrations and create the administrator from the repository root:

```sh
PYTHONPATH=app python -m alembic -c app/alembic.ini upgrade head
python -m app.initial_data
```

For transit routing, import stop and route data:

```sh
python -m app.initial_data --seed-transit --stops /path/to/data.json --routes /path/to/route.json
```

Both seed files are JSON arrays. Stops require integer `id`, `name`, `latitude`, and `longitude`. Routes require `start` and `end` stop IDs, nonnegative `price` with at most two decimal places, positive `time`, and boolean `bi_directional`.

## Usage

```sh
uvicorn app.main:app --reload
```

Open [the API docs](http://127.0.0.1:8000/docs) and select **Authorize** using the administrator's email as the username and the configured password. API routes use the `/api/v1` prefix; the docs describe requests for routing, places, contributions, and trip history.

Distances are metres, durations minutes, and prices NGN.

Licensed under the [MIT License](LICENSE).
