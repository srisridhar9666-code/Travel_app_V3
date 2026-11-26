"""
Shared test setup.

Two things are enforced globally here.

**No test may send real email.** Delivery goes through a module-level transport,
and `.env` on a developer machine has real Gmail credentials in it, so a test
that happened to use an allowlisted address would actually post to someone's
inbox. The outbox is installed for the whole session rather than trusted to each
test remembering.

**Tests run against MySQL, not SQLite.** They used to use an in-memory SQLite
database, which was fast and wrong: the worst defect this project has had was
MySQL's `DATETIME` silently truncating the microseconds the audit hash chain
commits to, and no amount of SQLite testing could have found it. The engines
differ in exactly the places that matter here - fractional seconds, JSON
columns, string collation, `SELECT ... FOR UPDATE` - so the tests use the engine
the product actually runs on.

The cost is that a MySQL server must be reachable. The schema is built once per
session and each test runs inside a transaction that is rolled back afterwards,
so the suite stays quick despite the round trips.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base
from app.services import email

#: Appended to the configured database name. The guard below refuses to run
#: against anything not ending in this, because `create_all` plus a rollback
#: that did not quite work would otherwise be pointed at live data.
TEST_DB_SUFFIX = "_test"


def _test_url():
    """The configured database URL, pointed at a scratch database beside it.

    Derived rather than configured separately so it follows DATABASE_URL to
    whatever host, user and password the developer already set up.
    """
    url = make_url(get_settings().database_url)
    name = (url.database or "travel_ops").removesuffix(TEST_DB_SUFFIX)
    return url.set(database=f"{name}{TEST_DB_SUFFIX}")


@pytest.fixture(autouse=True, scope="session")
def _never_send_real_email():
    """Swap the SMTP transport for a list, for every test in the run."""
    with email.use_outbox() as outbox:
        yield outbox


@pytest.fixture
def outbox():
    """The captured messages, cleared before each test that looks at them."""
    box = email._outbox
    assert box is not None, "the session-wide outbox should be installed"
    box.clear()
    return box


@pytest.fixture(scope="session")
def engine():
    """One engine for the run, against a freshly built scratch database."""
    url = _test_url()

    if not (url.database or "").endswith(TEST_DB_SUFFIX):
        raise RuntimeError(
            f"Refusing to run tests against {url.database!r}: the test database "
            f"name must end in {TEST_DB_SUFFIX!r}."
        )

    # Connect without a database selected so the scratch one can be created.
    #
    # `database=""` rather than `database=None`: URL.set() reads None as "leave
    # this alone", so None would point the connection at the very database we
    # are about to create and fail with "Unknown database".
    server = create_engine(url.set(database=""), isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
    except Exception as exc:  # pragma: no cover - environment, not logic
        raise RuntimeError(
            f"Could not reach MySQL at {url.set(password=None).render_as_string()}. "
            "The suite needs a running server; check DATABASE_URL in backend/.env."
        ) from exc
    finally:
        server.dispose()

    test_engine = create_engine(url, pool_pre_ping=True)

    # Built from the models rather than by Alembic: faster, and migration
    # fidelity is checked separately by `alembic check` in CI.
    with test_engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        tables = conn.execute(text("SHOW TABLES;")).scalars().all()
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS `{t}`;"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        conn.commit()

    Base.metadata.create_all(test_engine)

    yield test_engine

    with test_engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
        tables = conn.execute(text("SHOW TABLES;")).scalars().all()
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS `{t}`;"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        conn.commit()
    test_engine.dispose()


@pytest.fixture
def db(engine):
    """A session whose writes are discarded when the test ends.

    The test runs inside an outer transaction that is always rolled back, and
    the session joins it by creating savepoints - so a `commit()` inside a test
    behaves normally and still leaves nothing behind. Far quicker than
    recreating the schema per test, and it means tests cannot leak state into
    each other through a shared database.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
