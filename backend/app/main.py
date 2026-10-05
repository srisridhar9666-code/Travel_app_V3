"""
FastAPI application entrypoint.

Wires configuration, the database session, CORS for the Vite dev server, the
health probes, and the Phase 1 routers.
"""
import logging
from contextlib import asynccontextmanager
from functools import cache

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.concurrency import run_in_threadpool

from app.config import BACKEND_DIR, get_settings
from app.core import clock
from app.core.hardening import SecurityHeadersMiddleware, check_startup
from app.core.logging import RequestIdMiddleware, configure as configure_logging
from app.database import SessionLocal, engine
from app.routers import analytics as analytics_router
from app.routers import audit as audit_router
from app.routers import auth as auth_router
from app.routers import departments as departments_router
from app.routers import id_proofs as id_proofs_router
from app.routers import insights as insights_router
from app.routers import internal as internal_router
from app.routers import invoices as invoices_router
from app.routers import locations as locations_router
from app.routers import notifications as notifications_router
from app.routers import projects as projects_router
from app.routers import requests as requests_router
from app.routers import tickets as tickets_router
from app.routers import team as team_router
from app.routers import users as users_router
from app.routers import vendors as vendors_router
from app.services import email as email_service
from app.services import gemini, scheduler
from app.services import locations as location_service
from app.services.seed import ensure_bootstrap_admin, ensure_other_project

settings = get_settings()

configure_logging(json_output=settings.json_logs, level=settings.log_level)
logger = logging.getLogger("travel_ops")

#: Bump with any change the web app depends on - a new route, or a new field it
#: reads - and bump API_VERSION in web/src/lib/api.ts to match. The web app
#: compares the two through /health and tells an admin when this process is
#: older than the page calling it. The usual cause is an API that was not
#: restarted after an update, which otherwise shows up as "Not Found", "Method
#: Not Allowed" and pages with missing numbers.
API_VERSION = "0.17.0"


@cache
def _wanted_heads() -> frozenset[str]:
    """The migration heads this code expects. Read from disk once: the files
    cannot change under a running process without a restart mattering anyway."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def schema_behind() -> str | None:
    """Say so when the database has not been migrated to match this code.

    A new column in a model makes every query on that table fail until
    `alembic upgrade head` has run, and the error a person sees ("Unknown
    column") does not say what to do. This does: in the log at start-up, and
    to admins in the web app through /health.
    """
    from alembic.runtime.migration import MigrationContext

    wanted = _wanted_heads()
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current == wanted:
        return None
    return (
        f"the database is at {', '.join(sorted(current)) or 'no version'} but this code "
        f"needs {', '.join(sorted(wanted))}. Stop the API and, in backend/, run: "
        "python -m alembic upgrade head (with the venv's python: "
        ".venv\\Scripts\\python.exe on Windows, .venv/bin/python elsewhere)"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything else: refuse to start in production with the development
    # defaults this repository ships. A misconfigured deploy that boots anyway
    # looks healthy and has a published admin password.
    check_startup(settings)

    settings.upload_path.mkdir(parents=True, exist_ok=True)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    zone = clock.zone_problem()
    if zone:
        logger.warning(zone)

    try:
        behind = schema_behind()
    except Exception:   # unreachable database: the steps below report it
        behind = None
    if behind:
        logger.error("Database needs migrating: %s", behind)

    # Schema comes from Alembic, never from create_all. If the tables are not
    # there yet the app should say so loudly rather than invent them.
    #
    # Each step commits on its own. They used to share one transaction, so a
    # missing `locations` table also rolled back the "Other" campaign and the
    # form lost both. The two reference-data steps are repeated on first use
    # as well (see `ensure_seeded` and the projects list), because nothing
    # re-runs this block after a migration lands on a server already running.
    for step, run in (
        ("bootstrap admin", lambda db: ensure_bootstrap_admin(db)),
        ("'Other' campaign", lambda db: ensure_other_project(db, settings.default_tenant)),
        ("place list", lambda db: location_service.seed(db, settings.default_tenant)),
        # After the place list, which it reads. Fills only empty states.
        ("request states", lambda db: location_service.backfill_request_states(db, settings.default_tenant)),
    ):
        db = SessionLocal()
        try:
            run(db)
            db.commit()
        except Exception:
            logger.exception(
                "Startup step failed: %s. Have migrations been run? "
                "(cd backend && alembic upgrade head)",
                step,
            )
            db.rollback()
        finally:
            db.close()

    email_service.log_configuration()

    scheduler.start()

    yield

    await scheduler.stop()
    engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=API_VERSION,
    description="Field logistics, travel requests and accommodation for ground staff.",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware, settings=settings)

# Added last, so it runs first: every other middleware and handler then sees a
# request id already set.
app.add_middleware(RequestIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#: MySQL's "Unknown column" and "Table doesn't exist" - what every query meets
#: when the code is newer than the database.
_SCHEMA_ERRORS = frozenset({1054, 1146})

MIGRATE_MESSAGE = (
    "The database has not been migrated to match this version of the app, so this "
    "cannot load yet. An administrator needs to stop the API, run the migrations "
    "(alembic upgrade head) and start it again."
)


@app.exception_handler(DBAPIError)
async def database_behind(request: Request, exc: DBAPIError):
    """Say "migrate the database" instead of a bare 500.

    A new column the database does not have yet fails every query that reads
    its table - including the one behind every signed-in request - and the web
    app could only say "Could not refresh this page" on every panel. Only when
    the migration check confirms the database is behind; anything else is a
    real error and is re-raised for the usual 500 and traceback.
    """
    code = exc.orig.args[0] if exc.orig is not None and exc.orig.args else None
    if code in _SCHEMA_ERRORS:
        try:
            behind = await run_in_threadpool(schema_behind)
        except Exception:
            behind = None
        if behind:
            logger.error("%s %s failed because %s", request.method, request.url.path, behind)
            return JSONResponse(status_code=503, content={"detail": MIGRATE_MESSAGE})
    raise exc


app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(team_router.router)
app.include_router(departments_router.router)
app.include_router(projects_router.router)
app.include_router(requests_router.router)
app.include_router(tickets_router.router)
app.include_router(notifications_router.router)
app.include_router(analytics_router.router)
app.include_router(insights_router.router)
app.include_router(id_proofs_router.router)
app.include_router(audit_router.router)
app.include_router(vendors_router.router)
app.include_router(invoices_router.router)
app.include_router(locations_router.router)
app.include_router(internal_router.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness plus a real database round-trip, the code's version, and
    whether the database still needs `alembic upgrade head`."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        logger.exception("Database health check failed")
        database = f"error: {type(exc).__name__}"

    migrations_pending = False
    if database == "ok":
        try:
            migrations_pending = schema_behind() is not None
        except Exception:
            logger.exception("Could not read the database's migration version")

    return {
        "status": "ok" if database == "ok" else "degraded",
        "app": settings.app_name,
        "version": API_VERSION,
        "environment": settings.environment,
        "database": database,
        "migrations_pending": migrations_pending,
    }


@app.get("/health/gemini", tags=["health"])
def health_gemini() -> dict:
    """Confirms the service account actually reaches the extraction model."""
    return gemini.ping()


@app.get("/health/email", tags=["health"])
def health_email() -> dict:
    """Authenticates against SMTP without sending anything.

    A wrong app password should surface on the dashboard, not at the moment a
    traveller is waiting for a booking confirmation.
    """
    return email_service.check()
