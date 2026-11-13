# HiliAgents Trip Planner

English | [简体中文](README.md)

A multi-agent trip planning application built with HiliAgents. AMap MCP supplies
real points of interest (POIs), weather, and routes. Attraction, weather, and
hotel control agents analyze candidates in parallel; a planner agent drafts the
itinerary; and a deterministic optimizer checks places, opening hours, visit
durations, and travel time. The application includes asynchronous progress,
maps, accounts, versioned itinerary editing, sharing, and exports.

This repository contains the trip planner application only. The `hili_agents`
framework source must be obtained separately and **is not committed to this
GitHub repository**. A compatible framework checkout is required to run it.

## Features

1. **Plan a trip:** Choose a city, travel dates, transport mode, budget, and
   optional preferences. Without preferences, the planner can use popular POI
   candidates. Results include daily attractions, meals, accommodation, weather,
   estimated costs, and suggestions.
2. **Generate an executable itinerary:** AMap MCP retrieves POIs, weather, and
   routes. Three control agents analyze the candidates in parallel, and the
   planner agent produces a draft. The backend matches places against real POI
   candidates, then adjusts daily plans for visit duration, opening hours, and
   travel time. Missing or estimated routes are identified instead of being
   presented as verified routes.
3. **Follow progress and inspect results:** Celery runs generation in the
   background. The browser receives stage updates over SSE and can query or
   cancel a job. The result view shows daily timelines, map markers and routes,
   weather, costs, and fact sources. Map rendering requires a separate AMap
   browser API key.
4. **Save and revise:** Guests can save drafts; registered users can manage
   their trip history. Users can replace attractions with real POIs in the same
   city, move them between days, or lock them. Saving an edit recalculates the
   affected routes, schedule, and budget, keeps previous versions, and checks
   version numbers to prevent concurrent overwrites.
5. **Share and export:** Create expiring, revocable read-only links and collect
   visitor feedback. Export an itinerary as an image, PDF, or iCalendar file,
   or open directions in AMap.
6. **Operate and degrade gracefully:** Admin views expose job traces, external
   call counts and durations, cost budgets, rate limits, and circuit-breaker
   states. Images fall back from AMap POI photos to Wikimedia Commons,
   Unsplash, and a local placeholder.

The main flow is `request -> background job -> map facts -> multi-agent decisions
-> validation and optimization -> saved/displayed trip -> editing/sharing/export`.
Model-generated descriptions and estimated prices are distinguished from map
facts using `verified`, `estimated`, and `unknown` source labels.

### Main API endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /api/trip/jobs` | Submit an asynchronous job and receive its ID; accepts an optional `Idempotency-Key`. |
| `GET /api/trip/jobs/{job_id}` | Get job status and result. |
| `GET /api/trip/jobs/{job_id}/events` | Subscribe to SSE progress updates. |
| `DELETE /api/trip/jobs/{job_id}` | Request job cancellation. |
| `GET /api/map/poi`, `GET /api/map/weather`, `POST /api/map/route` | Query map facts. |
| `/api/trips`, `/api/auth` | Access trip versions, sharing, and account operations; see `/docs` for full schemas. |

## Tech stack

- Backend: Python 3.11, FastAPI, Pydantic, HiliAgents, Celery, Redis,
  PostgreSQL, SQLAlchemy, and Alembic.
- Frontend: Vue 3, TypeScript, Vite, Ant Design Vue, and the AMap JavaScript API.
- Operations and testing: Docker Compose, Nginx, Prometheus, Grafana, Pytest,
  Playwright, and k6.
- Map integration: a patched `amap-mcp-server` fork is included with its MIT
  license and upstream attribution.

## Repository layout

```text
backend/       FastAPI, agent orchestration, adapters, worker, migrations, tests
frontend/      Vue application and browser map
deploy/        Prometheus and Grafana configuration
scripts/       Backup, restore, rollback, and frontend bundle checks
tests/phase6/  Browser, deployment, performance, security, and live contract tests
docs/          Architecture, deployment, and technical project notes
compose.yaml   Local release-style deployment
```

## Quick start with Docker Compose

Prepare a **separate** HiliAgents checkout whose root contains `hili_agents/`.
From this repository's root, create your local environment file:

```powershell
Copy-Item .env.compose.example .env
```

Edit `.env`. Set `HILI_AGENTS_SOURCE` to the absolute path of that checkout, or
to a path relative to this repository. Also set a database password,
`AMAP_API_KEY`, `DASHSCOPE_API_KEY`, and the browser-side AMap keys. Docker
BuildKit receives the framework checkout as an additional build context; the
framework remains outside this Git repository.

```powershell
docker compose config --quiet
docker compose build
docker compose up -d
python tests/phase6/deployment/smoke_compose.py
```

Open `http://localhost:8080`. API documentation is available at
`http://127.0.0.1:8000/docs`. Database migration runs as a one-shot Compose
service. To enable the optional monitoring stack:

```powershell
docker compose --profile observability up -d
```

The framework version at `HILI_AGENTS_SOURCE` must provide the `MySimpleAgent`,
`HiliAgentsLLM`, `MCPTool`, and related exception interfaces used here. Cloning
this public repository alone does not obtain that external dependency. A
versioned, accessible framework distribution is needed for reproducible
deployment and backend CI.

## Local development and tests

With a Python 3.11 virtual environment, explicitly add the separate framework
checkout to the module search path:

```powershell
$env:PYTHONPATH = 'D:\path\to\separate-hili-agents'
python -m pip install --require-hashes -r backend/requirements.lock
python -m pip install --require-hashes -r backend/requirements-test.lock
Set-Location backend
python -m pytest
python -m uvicorn app.api.main:app --reload
```

Local execution also needs PostgreSQL, Redis, and API keys; use Compose for the
full stack. For frontend development, run `cd frontend`, `npm ci`, and
`npm run dev`. The production backend image uses the Python 3.11 Linux-specific
`backend/requirements-linux.lock`. Public GitHub Actions currently verify only
the frontend build; backend CI requires an accessible, pinned HiliAgents
distribution.

See the [Phase 6 test guide](tests/phase6/README.md) for other checks.

## Release and security notes

- Git ignores `.env`, backups, logs, reports, databases, and `node_modules`.
  Commit only `.env.compose.example`, never real credentials.
- `backend/vendor/amap-mcp-server/` contains a separate third-party fork with
  its [MIT license](backend/vendor/amap-mcp-server/LICENSE). If it is converted
  back to a Git submodule, update `.gitmodules` and CI checkout settings.
- Compose uses the local framework checkout as an extra build context. Its
  source is not added to this Git repository, but is included in the resulting
  backend image. Check the framework's license before publishing that image.
- A production deployment needs TLS, a domain, persistent volumes, secret
  management, and alerting. The current validation covers a local Compose
  deployment, not sustained public production traffic.

See the [deployment guide](docs/DEPLOYMENT.md) for release, backup, and
rollback procedures, and the [technical project review](docs/PROJECT_TECHNICAL_AND_RESUME.md)
for design decisions and metric definitions. Those documents are currently in
Chinese.

## Acknowledgments

- [HelloAgents](https://github.com/datawhalechina/Hello-Agents): reference for
  the original learning template.
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server): upstream
  AMap MCP server.
