"""
Shared fixtures for the Rayda Fleet Copilot test suite.

All tests use an isolated in-memory SQLite database — no connection to the
real telemetry.db and no LLM calls are made unless explicitly tested.
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    Base, Snapshot, OS, Battery, Memory, DiskVolume,
    ComplianceResult, AuditLog
)

# ---------------------------------------------------------------------------
# In-memory database engine shared across the test session
# ---------------------------------------------------------------------------
TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def engine():
    """Create all tables once for the entire test session."""
    eng = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture(autouse=True)
def db_session(engine, monkeypatch):
    """
    Provide a clean database session for each test.
    All data inserted during a test is rolled back afterwards.
    Also patches app.core.database.engine so tools use the test DB.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    # Patch the engine used by tools and agent so they hit the test DB
    import app.core.database as db_module
    monkeypatch.setattr(db_module, "engine", engine)
    import app.tools as tools_module
    monkeypatch.setattr(tools_module, "engine", engine)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------
COMPANY_A = "acme-001"
COMPANY_B = "globex-009"

BASE_TIME = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_snapshot(
    session: Session,
    device_id: str = "device-1",
    company_id: str = COMPANY_A,
    collected_at: datetime = BASE_TIME,
    employee_id: str = "emp-1",
) -> Snapshot:
    snap = Snapshot(
        device_id=device_id,
        company_id=company_id,
        employee_id=employee_id,
        collected_at=collected_at,
        agent_version="1.0.0",
    )
    session.add(snap)
    session.flush()  # get snap.id without committing
    return snap


def make_battery(session: Session, snapshot: Snapshot, cycle_count: int = 400, condition: str = "Normal") -> Battery:
    bat = Battery(
        snapshot_id=snapshot.id,
        battery_present=True,
        charging_status="Discharging",
        percentage=80,
        condition=condition,
        cycle_count=cycle_count,
        full_charge_capacity=5000,
    )
    session.add(bat)
    session.flush()
    return bat


def make_memory(session: Session, snapshot: Snapshot, used_bytes: int, total_bytes: int) -> Memory:
    mem = Memory(
        snapshot_id=snapshot.id,
        ram_bytes=total_bytes,
        total_memory_bytes=total_bytes,
        used_memory_bytes=used_bytes,
        free_memory_bytes=total_bytes - used_bytes,
        page_size_bytes=4096,
    )
    session.add(mem)
    session.flush()
    return mem


def make_disk(session: Session, snapshot: Snapshot, available_bytes: int, size_bytes: int = 500 * 1024**3) -> DiskVolume:
    disk = DiskVolume(
        snapshot_id=snapshot.id,
        volume_name="Macintosh HD",
        file_system="apfs",
        mount_point="/",
        size_bytes=size_bytes,
        available_bytes=available_bytes,
        encrypted=True,
    )
    session.add(disk)
    session.flush()
    return disk


def make_compliance(session: Session, snapshot: Snapshot, check_id: str, status: str, severity: str) -> ComplianceResult:
    cr = ComplianceResult(
        snapshot_id=snapshot.id,
        check_id=check_id,
        status=status,
        severity=severity,
    )
    session.add(cr)
    session.flush()
    return cr
