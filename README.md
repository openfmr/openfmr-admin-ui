# OpenFMR Admin UI

**Data Steward Admin Interface** for the [OpenFMR](https://github.com/openfmr) modular Health Information Exchange (HIE).

This lightweight web application lets data stewards review and resolve FHIR resource conflicts that arise when the Client Registry (CR), Health Facility Registry (HFR), and Health Worker Registry (HWR) sync workers detect discrepancies between local and master records.

---

## Architecture

| Layer | Technology |
|---|---|
| Backend | **FastAPI** (Python 3.11) |
| Templates | **Jinja2** |
| Styling | **Bootstrap 5** + plain CSS |
| Interactivity | **Vanilla JavaScript** + **jsdiff** |
| Database | **asyncpg** → PostgreSQL (CR, HFR & HWR staging DBs) |

> **No React, no heavy frontend frameworks.** This is a single, server-rendered application.

---

## File Structure

```
openfmr-admin-ui/
├── docker-compose.yml      # Service definition + openfmr_global_net
├── Dockerfile              # Python 3.11-slim image
├── .env.example            # Required environment variables
├── requirements.txt        # Python dependencies
├── README.md
└── app/
    ├── main.py             # FastAPI routes & app setup
    ├── database.py         # asyncpg access layer (CR + HFR + HWR)
    ├── static/
    │   ├── style.css       # Diff colours, layout polish
    │   └── app.js          # jsdiff rendering, resolution API calls
    └── templates/
        ├── base.html       # Shared layout (Bootstrap 5 CDN)
        ├── dashboard.html  # Pending‑conflicts table
        └── resolution.html # Side‑by‑side diff + action buttons
```

---

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your CR, HFR, and HWR staging database connection strings.
```

### 2. Run with Docker Compose

```bash
# Ensure the openfmr_global_net network exists (created by openfmr-core)
docker compose up -d --build
```

The UI will be available at **http://localhost:8000**.

### 3. Run locally (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set env vars
export CR_STAGING_DB_URL="postgresql://user:pass@localhost:5432/cr_staging"
export HFR_STAGING_DB_URL="postgresql://user:pass@localhost:5432/hfr_staging"
export HWR_STAGING_DB_URL="postgresql://user:pass@localhost:5432/hwr_staging"

uvicorn app.main:app --reload --port 8000
```

---

## Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Dashboard — lists all pending conflicts |
| `GET` | `/conflict/{module}/{id}` | Resolution screen with side‑by‑side diff |
| `POST` | `/resolve/{module}/{id}` | API — submit resolution decision |

### Resolution Decisions

The `POST /resolve` endpoint accepts a JSON body:

```json
{
  "decision": "keep_local | accept_master | merge",
  "merged_resource": { ... }
}
```

- **`keep_local`** — retain the current local record.
- **`accept_master`** — adopt the incoming master record.
- **`merge`** — use the steward's manually‑merged resource (`merged_resource` required).

---

## Database Schema (Expected)

The **CR** and **HFR** staging databases must have a `conflicts` table:

```sql
CREATE TABLE conflicts (
    id            UUID PRIMARY KEY,
    resource_type TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    local_state   JSONB NOT NULL,
    incoming      JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);
```

The **HWR** staging database uses a `worker_conflicts` table:

```sql
CREATE TABLE worker_conflicts (
    conflict_id         UUID PRIMARY KEY,
    resource_type       VARCHAR(64) NOT NULL,
    identifier_system   TEXT NOT NULL,
    identifier_value    TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    local_state_json    JSONB NOT NULL,
    incoming_state_json JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

> Column differences are handled internally via SQL aliases — the UI treats all modules identically.

---

## License

Part of the OpenFMR project. See the root repository for licence details.
