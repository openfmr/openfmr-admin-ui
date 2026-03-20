# =============================================================================
# OpenFMR Admin UI — FastAPI Application Entry Point
# =============================================================================
# This module bootstraps the FastAPI app, mounts static files, configures
# Jinja2 templates, and defines the three core routes:
#
#   GET  /                         — Dashboard listing pending conflicts
#   GET  /conflict/{module}/{id}   — Conflict resolution diff screen
#   POST /resolve/{module}/{id}    — API endpoint to submit a resolution
#
# Supported modules: cr (Client Registry), hfr (Health Facility Registry),
#                    hwr (Health Worker Registry).
# =============================================================================

import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.database import (
    VALID_MODULES,
    fetch_pending_conflicts,
    fetch_conflict_by_id,
    resolve_conflict,
    close_pools,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("openfmr.admin")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# ---------------------------------------------------------------------------
# Lifespan — graceful shutdown of DB pools
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage application startup / shutdown lifecycle."""
    logger.info("OpenFMR Admin UI starting up …")
    yield
    logger.info("OpenFMR Admin UI shutting down — closing DB pools …")
    await close_pools()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="OpenFMR Admin UI",
    description="Data Steward Admin interface for the OpenFMR Health Information Exchange.",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static assets (CSS, JS)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 template engine
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ResolutionPayload(BaseModel):
    """
    JSON payload sent by the frontend when a data steward resolves a conflict.

    Attributes
    ----------
    decision : str
        One of ``"accept_master"``, ``"keep_local"``, or ``"merge"``.
    merged_resource : dict | None
        Required when *decision* is ``"merge"`` — the manually‑merged
        FHIR resource constructed by the steward.
    """
    decision: str
    merged_resource: dict | None = None


# ---------------------------------------------------------------------------
# Custom Jinja2 filter for pretty-printing JSON inside templates
# ---------------------------------------------------------------------------

def _json_pretty(value) -> str:
    """Jinja2 filter: render a Python object as indented JSON."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return json.dumps(value, indent=2, ensure_ascii=False)


templates.env.filters["json_pretty"] = _json_pretty


# ===================================================================
# Route 1 — Dashboard
# ===================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Render the main dashboard showing all pending conflicts from the
    CR, HFR, and HWR staging databases.
    """
    try:
        conflicts = await fetch_pending_conflicts()
    except Exception as exc:
        logger.error("Failed to load dashboard data: %s", exc)
        conflicts = []

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "conflicts": conflicts,
            "total": len(conflicts),
        },
    )


# ===================================================================
# Route 2 — Conflict Resolution Screen
# ===================================================================

@app.get("/conflict/{module}/{conflict_id}", response_class=HTMLResponse)
async def conflict_detail(request: Request, module: str, conflict_id: str):
    """
    Render the side‑by‑side diff / resolution screen for a specific conflict.
    """
    # Validate module parameter
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail=f"Invalid module. Use one of {VALID_MODULES}.")

    try:
        conflict = await fetch_conflict_by_id(module, conflict_id)
    except ConnectionError as exc:
        logger.error("DB connection error: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    if conflict is None:
        raise HTTPException(status_code=404, detail="Conflict not found.")

    # Serialise JSON columns for safe embedding in the template
    local_state_json = json.dumps(
        conflict.get("local_state", {}), indent=2, ensure_ascii=False
    )
    incoming_master_json = json.dumps(
        conflict.get("incoming", {}), indent=2, ensure_ascii=False
    )

    return templates.TemplateResponse(
        "resolution.html",
        {
            "request": request,
            "conflict": conflict,
            "local_state_json": local_state_json,
            "incoming_master_json": incoming_master_json,
            "module": module,
            "conflict_id": conflict_id,
        },
    )


# ===================================================================
# Route 3 — Resolve Conflict (API)
# ===================================================================

@app.post("/resolve/{module}/{conflict_id}")
async def resolve(module: str, conflict_id: str, payload: ResolutionPayload):
    """
    Process the steward's resolution decision:
      • **accept_master** — adopt the incoming master record.
      • **keep_local** — retain the current local record.
      • **merge** — use the manually merged resource provided by the steward.

    After updating the staging DB the endpoint simulates forwarding the
    finalised resource to the respective local HAPI FHIR server.
    """
    # Validate module
    if module not in VALID_MODULES:
        raise HTTPException(status_code=400, detail=f"Invalid module. Use one of {VALID_MODULES}.")

    # Validate decision value
    valid_decisions = ("accept_master", "keep_local", "merge")
    if payload.decision not in valid_decisions:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision '{payload.decision}'. Must be one of {valid_decisions}.",
        )

    # If merging, the steward must supply the merged resource
    if payload.decision == "merge" and not payload.merged_resource:
        raise HTTPException(
            status_code=422,
            detail="A 'merged_resource' is required when decision is 'merge'.",
        )

    # ------------------------------------------------------------------
    # 1. Mark the conflict as resolved in the staging database
    # ------------------------------------------------------------------
    try:
        updated = await resolve_conflict(module, conflict_id)
    except ConnectionError as exc:
        logger.error("DB connection error while resolving: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable.") from exc

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Conflict not found or already resolved.",
        )

    # ------------------------------------------------------------------
    # 2. Determine the final FHIR resource to forward
    # ------------------------------------------------------------------
    if payload.decision == "accept_master":
        # In a full implementation we would fetch conflict.incoming and use it.
        final_resource = {"note": "Incoming master record accepted."}
    elif payload.decision == "keep_local":
        final_resource = {"note": "Local record retained — no update sent."}
    else:
        final_resource = payload.merged_resource  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 3. Simulate forwarding to the local HAPI FHIR server
    # ------------------------------------------------------------------
    # In production this would be an HTTP PUT/POST to the HAPI FHIR endpoint
    # for the appropriate module.
    _fhir_targets = {
        "cr":  "http://cr-hapi-fhir:8080/fhir",
        "hfr": "http://hfr-hapi-fhir:8080/fhir",
        "hwr": "http://hwr-hapi-fhir:8080/fhir",
    }
    fhir_target = _fhir_targets[module]
    logger.info(
        "Simulated forward of resolved resource to %s — decision: %s",
        fhir_target,
        payload.decision,
    )

    return JSONResponse(
        content={
            "status": "success",
            "message": f"Conflict {conflict_id} resolved ({payload.decision}).",
            "forwarded_to": fhir_target,
            "final_resource": final_resource,
        }
    )
