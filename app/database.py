# =============================================================================
# OpenFMR Admin UI — Database Access Layer
# =============================================================================
# Provides asynchronous functions (via asyncpg) for connecting to both the
# Client Registry (CR) and Health Facility Registry (HFR) staging databases.
#
# Each staging database is expected to contain a `conflicts` table with at
# least the following columns:
#   id            UUID PRIMARY KEY
#   resource_type TEXT            — e.g. "Patient" or "Location"
#   status        TEXT            — "pending" | "resolved"
#   local_state   JSONB           — the current local FHIR resource
#   incoming      JSONB           — the incoming master FHIR resource
#   created_at    TIMESTAMPTZ     — when the conflict was detected
#   resolved_at   TIMESTAMPTZ     — when the steward resolved it (nullable)
# =============================================================================

import os
import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger("openfmr.admin.database")

# ---------------------------------------------------------------------------
# Environment‑based connection strings
# ---------------------------------------------------------------------------
CR_STAGING_DB_URL: str = os.getenv("CR_STAGING_DB_URL", "")
HFR_STAGING_DB_URL: str = os.getenv("HFR_STAGING_DB_URL", "")


# ---------------------------------------------------------------------------
# Connection pool management
# ---------------------------------------------------------------------------
# We maintain one pool per staging database so connections are reused.
_pools: dict[str, asyncpg.Pool | None] = {
    "cr": None,
    "hfr": None,
}


async def _get_pool(module: str) -> asyncpg.Pool:
    """
    Return (and lazily create) the connection pool for the given module.

    Parameters
    ----------
    module : str
        Either ``"cr"`` (Client Registry) or ``"hfr"`` (Health Facility Registry).

    Raises
    ------
    ValueError
        If *module* is not recognised.
    ConnectionError
        If the corresponding database URL is not configured.
    """
    if module not in ("cr", "hfr"):
        raise ValueError(f"Unknown module: {module!r}. Expected 'cr' or 'hfr'.")

    dsn = CR_STAGING_DB_URL if module == "cr" else HFR_STAGING_DB_URL
    if not dsn:
        raise ConnectionError(
            f"Database URL for module '{module}' is not configured. "
            f"Set the {'CR_STAGING_DB_URL' if module == 'cr' else 'HFR_STAGING_DB_URL'} "
            f"environment variable."
        )

    if _pools[module] is None:
        try:
            _pools[module] = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
            logger.info("Connection pool created for module '%s'.", module)
        except Exception as exc:
            logger.error("Failed to create pool for '%s': %s", module, exc)
            raise ConnectionError(
                f"Could not connect to the {module.upper()} staging database."
            ) from exc

    return _pools[module]  # type: ignore[return-value]


async def close_pools() -> None:
    """Gracefully close all open connection pools (called on app shutdown)."""
    for key, pool in _pools.items():
        if pool is not None:
            await pool.close()
            _pools[key] = None
            logger.info("Connection pool closed for module '%s'.", key)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: asyncpg.Record, module: str) -> dict[str, Any]:
    """
    Convert an ``asyncpg.Record`` to a plain dictionary and attach the
    originating *module* label so the UI knows which registry the conflict
    belongs to.
    """
    data = dict(row)
    data["module"] = module

    # Ensure JSONB columns are serialisable Python dicts
    for col in ("local_state", "incoming"):
        if col in data and isinstance(data[col], str):
            try:
                data[col] = json.loads(data[col])
            except (json.JSONDecodeError, TypeError):
                pass

    # Convert datetimes to ISO strings for Jinja2 rendering
    for col in ("created_at", "resolved_at"):
        if col in data and isinstance(data[col], datetime):
            data[col] = data[col].isoformat()

    return data


async def fetch_pending_conflicts() -> list[dict[str, Any]]:
    """
    Fetch **all** pending (unresolved) conflicts from both staging databases
    and return them as a single aggregated list sorted by ``created_at``
    (newest first).

    If a database is unreachable the function logs a warning and continues
    with the other database so the UI remains partially functional.
    """
    conflicts: list[dict[str, Any]] = []

    for module in ("cr", "hfr"):
        try:
            pool = await _get_pool(module)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, resource_type, status, local_state, incoming, created_at
                    FROM conflicts
                    WHERE status = 'pending'
                    ORDER BY created_at DESC
                    """
                )
                conflicts.extend(_row_to_dict(row, module) for row in rows)
        except (ConnectionError, asyncpg.PostgresError) as exc:
            logger.warning(
                "Could not fetch conflicts from '%s' staging DB: %s", module, exc
            )

    # Re-sort the merged list by created_at descending
    conflicts.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return conflicts


async def fetch_conflict_by_id(module: str, conflict_id: str) -> dict[str, Any] | None:
    """
    Retrieve a single conflict record by its *conflict_id* from the
    staging database identified by *module*.

    Returns
    -------
    dict or None
        The conflict as a dictionary, or ``None`` if not found.
    """
    pool = await _get_pool(module)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, resource_type, status, local_state, incoming, created_at, resolved_at
            FROM conflicts
            WHERE id = $1
            """,
            conflict_id,
        )
    if row is None:
        return None
    return _row_to_dict(row, module)


async def resolve_conflict(module: str, conflict_id: str) -> bool:
    """
    Mark the conflict identified by *conflict_id* as **resolved** in the
    corresponding staging database.

    Returns
    -------
    bool
        ``True`` if a row was updated, ``False`` if the ID was not found.
    """
    pool = await _get_pool(module)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE conflicts
            SET status = 'resolved', resolved_at = NOW()
            WHERE id = $1 AND status = 'pending'
            """,
            conflict_id,
        )
    # asyncpg returns e.g. "UPDATE 1" — extract the count
    count = int(result.split()[-1])
    if count:
        logger.info("Conflict %s in '%s' marked as resolved.", conflict_id, module)
    return count > 0
