"""
Unit tests for all tools in app/tools.py.

These tests use the in-memory SQLite DB from conftest.py — no LLM calls,
no real telemetry.db, fully deterministic.
"""
import json
import pytest
from datetime import datetime, timezone, timedelta

from tests.conftest import (
    COMPANY_A, COMPANY_B, BASE_TIME,
    make_snapshot, make_battery, make_memory, make_disk, make_compliance,
)
from app.tools import (
    check_fleet_compliance,
    analyze_battery_degradation,
    get_low_disk_space_devices,
    analyze_ram_constraints_over_time,
    analyze_compliance_drift,
    propose_remediation_action,
    get_recent_audit_logs,
)
from app.models import AuditLog
from sqlalchemy.orm import Session
from app.core.database import engine as real_engine  # patched by conftest


# ===========================================================================
# check_fleet_compliance
# ===========================================================================

class TestCheckFleetCompliance:

    def test_returns_failing_devices(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-1")
        make_compliance(db_session, snap, "screen_lock", "fail", "high")
        db_session.commit()

        result = json.loads(check_fleet_compliance.invoke({"company_id": COMPANY_A}))

        assert len(result) == 1
        assert result[0]["device_id"] == "dev-1"
        assert result[0]["failing_check"] == "screen_lock"
        assert result[0]["severity"] == "high"

    def test_excludes_passing_devices(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-pass")
        make_compliance(db_session, snap, "screen_lock", "pass", "high")
        db_session.commit()

        result = check_fleet_compliance.invoke({"company_id": COMPANY_A})
        assert result == "No compliance failures found."

    def test_severity_filter_high_only(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-2")
        make_compliance(db_session, snap, "check_a", "fail", "high")
        make_compliance(db_session, snap, "check_b", "fail", "medium")
        db_session.commit()

        result = json.loads(check_fleet_compliance.invoke({"company_id": COMPANY_A, "severity": "high"}))

        assert len(result) == 1
        assert result[0]["severity"] == "high"

    def test_only_returns_latest_snapshot_per_device(self, db_session):
        # Two snapshots for same device — only the latest should be used
        old_time = BASE_TIME
        new_time = BASE_TIME + timedelta(days=1)

        snap_old = make_snapshot(db_session, device_id="dev-3", collected_at=old_time)
        snap_new = make_snapshot(db_session, device_id="dev-3", collected_at=new_time)

        # Old snapshot has a failure; new snapshot is clean
        make_compliance(db_session, snap_old, "old_check", "fail", "high")
        make_compliance(db_session, snap_new, "old_check", "pass", "high")
        db_session.commit()

        result = check_fleet_compliance.invoke({"company_id": COMPANY_A})
        assert result == "No compliance failures found."

    def test_tenant_isolation(self, db_session):
        # Insert a failing device under COMPANY_B
        snap = make_snapshot(db_session, device_id="dev-other", company_id=COMPANY_B)
        make_compliance(db_session, snap, "screen_lock", "fail", "high")
        db_session.commit()

        # Querying COMPANY_A should return nothing
        result = check_fleet_compliance.invoke({"company_id": COMPANY_A})
        assert result == "No compliance failures found."

    def test_returns_evidence_fields(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-evidence")
        make_compliance(db_session, snap, "firewall", "fail", "medium")
        snap_id = snap.id  # capture before session state changes
        db_session.commit()

        result = json.loads(check_fleet_compliance.invoke({"company_id": COMPANY_A}))

        assert "evidence_snapshot_id" in result[0]
        assert "collected_at" in result[0]
        assert result[0]["evidence_snapshot_id"] == snap_id


# ===========================================================================
# analyze_battery_degradation
# ===========================================================================

class TestAnalyzeBatteryDegradation:

    def test_flags_high_cycle_count(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-bat")
        make_battery(db_session, snap, cycle_count=600)
        db_session.commit()

        result = json.loads(analyze_battery_degradation.invoke({"company_id": COMPANY_A, "cycle_threshold": 500}))

        assert len(result) == 1
        assert result[0]["device_id"] == "dev-bat"
        assert result[0]["cycle_count"] == 600

    def test_does_not_flag_below_threshold(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-healthy-bat")
        make_battery(db_session, snap, cycle_count=300)
        db_session.commit()

        result = analyze_battery_degradation.invoke({"company_id": COMPANY_A, "cycle_threshold": 500})
        assert result == "No batteries exceed the cycle threshold."

    def test_custom_threshold(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-custom")
        make_battery(db_session, snap, cycle_count=350)
        db_session.commit()

        # With threshold of 300, this device should be flagged
        result = json.loads(analyze_battery_degradation.invoke({"company_id": COMPANY_A, "cycle_threshold": 300}))
        assert len(result) == 1

        # With threshold of 400, it should not be flagged
        result2 = analyze_battery_degradation.invoke({"company_id": COMPANY_A, "cycle_threshold": 400})
        assert result2 == "No batteries exceed the cycle threshold."

    def test_returns_evidence_fields(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-bat-ev")
        make_battery(db_session, snap, cycle_count=550)
        db_session.commit()

        result = json.loads(analyze_battery_degradation.invoke({"company_id": COMPANY_A}))
        assert "evidence_snapshot_id" in result[0]
        assert "collected_at" in result[0]

    def test_tenant_isolation(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-other-bat", company_id=COMPANY_B)
        make_battery(db_session, snap, cycle_count=700)
        db_session.commit()

        result = analyze_battery_degradation.invoke({"company_id": COMPANY_A})
        assert result == "No batteries exceed the cycle threshold."


# ===========================================================================
# get_low_disk_space_devices
# ===========================================================================

class TestGetLowDiskSpaceDevices:

    def test_flags_device_below_threshold(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-disk")
        make_disk(db_session, snap, available_bytes=20 * 1024**3)  # 20 GB
        db_session.commit()

        result = json.loads(get_low_disk_space_devices.invoke({"company_id": COMPANY_A, "min_available_gb": 50}))

        assert len(result) == 1
        assert result[0]["device_id"] == "dev-disk"
        assert result[0]["available_gb"] == 20.0

    def test_does_not_flag_above_threshold(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-plenty")
        make_disk(db_session, snap, available_bytes=100 * 1024**3)  # 100 GB
        db_session.commit()

        result = get_low_disk_space_devices.invoke({"company_id": COMPANY_A, "min_available_gb": 50})
        assert "No devices" in result

    def test_gb_conversion_is_correct(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-convert")
        # Exactly 30 GB available
        make_disk(db_session, snap, available_bytes=30 * 1024**3)
        db_session.commit()

        result = json.loads(get_low_disk_space_devices.invoke({"company_id": COMPANY_A, "min_available_gb": 50}))
        assert result[0]["available_gb"] == 30.0

    def test_tenant_isolation(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-disk-other", company_id=COMPANY_B)
        make_disk(db_session, snap, available_bytes=5 * 1024**3)
        db_session.commit()

        result = get_low_disk_space_devices.invoke({"company_id": COMPANY_A, "min_available_gb": 50})
        assert "No devices" in result


# ===========================================================================
# analyze_ram_constraints_over_time
# ===========================================================================

class TestAnalyzeRamConstraintsOverTime:

    def test_flags_consistently_constrained_device(self, db_session):
        # 3 snapshots, all above 90% RAM usage
        for i in range(3):
            snap = make_snapshot(db_session, device_id="dev-ram", collected_at=BASE_TIME + timedelta(days=i))
            make_memory(db_session, snap, used_bytes=9_500_000_000, total_bytes=10_000_000_000)
        db_session.commit()

        result = json.loads(analyze_ram_constraints_over_time.invoke({"company_id": COMPANY_A}))

        assert len(result) == 1
        assert result[0]["device_id"] == "dev-ram"
        assert result[0]["times_constrained"] == 3

    def test_does_not_flag_occasionally_constrained_device(self, db_session):
        # 4 snapshots — only 1 constrained (25% < 50% threshold)
        for i in range(4):
            snap = make_snapshot(db_session, device_id="dev-ram-ok", collected_at=BASE_TIME + timedelta(days=i))
            used = 9_500_000_000 if i == 0 else 5_000_000_000  # only first is constrained
            make_memory(db_session, snap, used_bytes=used, total_bytes=10_000_000_000)
        db_session.commit()

        result = analyze_ram_constraints_over_time.invoke({"company_id": COMPANY_A})
        assert result == "No devices show consistent RAM constraints over time."

    def test_handles_none_memory_values_without_crashing(self, db_session):
        # Memory record with None values — should not raise ZeroDivisionError
        snap = make_snapshot(db_session, device_id="dev-ram-null")
        from app.models import Memory
        mem = Memory(snapshot_id=snap.id, total_memory_bytes=None, used_memory_bytes=None)
        db_session.add(mem)
        db_session.commit()

        # Should not raise
        result = analyze_ram_constraints_over_time.invoke({"company_id": COMPANY_A})
        assert result == "No devices show consistent RAM constraints over time."

    def test_handles_zero_total_memory_without_crashing(self, db_session):
        snap = make_snapshot(db_session, device_id="dev-ram-zero")
        make_memory(db_session, snap, used_bytes=0, total_bytes=0)
        db_session.commit()

        result = analyze_ram_constraints_over_time.invoke({"company_id": COMPANY_A})
        assert result == "No devices show consistent RAM constraints over time."

    def test_tenant_isolation(self, db_session):
        for i in range(3):
            snap = make_snapshot(db_session, device_id="dev-ram-other", company_id=COMPANY_B,
                                 collected_at=BASE_TIME + timedelta(days=i))
            make_memory(db_session, snap, used_bytes=9_500_000_000, total_bytes=10_000_000_000)
        db_session.commit()

        result = analyze_ram_constraints_over_time.invoke({"company_id": COMPANY_A})
        assert result == "No devices show consistent RAM constraints over time."


# ===========================================================================
# propose_remediation_action
# ===========================================================================

class TestProposeRemediationAction:

    def test_valid_action_types_return_proposal(self):
        valid_actions = [
            "create_upgrade_order",
            "open_remediation_ticket",
            "notify_employee",
            "flag_device_for_replacement",
        ]
        for action_type in valid_actions:
            result = json.loads(propose_remediation_action.invoke({
                "company_id": COMPANY_A,
                "device_id": "dev-1",
                "action_type": action_type,
                "reason": "Test reason with telemetry evidence.",
            }))
            assert result["status"] == "PENDING_HUMAN_APPROVAL"
            assert result["action"] == action_type
            assert result["device_id"] == "dev-1"

    def test_invalid_action_type_returns_error(self):
        result = json.loads(propose_remediation_action.invoke({
            "company_id": COMPANY_A,
            "device_id": "dev-1",
            "action_type": "delete_all_devices",  # not valid
            "reason": "Some reason.",
        }))
        assert "error" in result

    def test_proposal_includes_justification(self):
        result = json.loads(propose_remediation_action.invoke({
            "company_id": COMPANY_A,
            "device_id": "dev-1",
            "action_type": "create_upgrade_order",
            "reason": "Battery cycle count is 620, exceeding the 500 threshold (snapshot_id=42).",
        }))
        assert "Battery cycle count" in result["justification"]


# ===========================================================================
# get_recent_audit_logs
# ===========================================================================

class TestGetRecentAuditLogs:

    def _insert_log(self, session, company_id, action, decision, offset_days=0):
        from app.models import AuditLog
        log = AuditLog(
            timestamp=BASE_TIME + timedelta(days=offset_days),
            company_id=company_id,
            action=action,
            proposal_details={"device_id": "dev-1", "reason": "test"},
            human_decision=decision,
        )
        session.add(log)
        session.flush()
        return log

    def test_returns_logs_for_company(self, db_session):
        self._insert_log(db_session, COMPANY_A, "create_upgrade_order", "approved")
        db_session.commit()

        result = json.loads(get_recent_audit_logs.invoke({"company_id": COMPANY_A}))
        assert len(result) == 1
        assert result[0]["action_type"] == "create_upgrade_order"
        assert result[0]["decision"] == "approved"

    def test_limit_is_respected(self, db_session):
        for i in range(10):
            self._insert_log(db_session, COMPANY_A, "notify_employee", "approved", offset_days=i)
        db_session.commit()

        result = json.loads(get_recent_audit_logs.invoke({"company_id": COMPANY_A, "limit": 3}))
        assert len(result) == 3

    def test_decision_filter(self, db_session):
        self._insert_log(db_session, COMPANY_A, "create_upgrade_order", "approved", offset_days=0)
        self._insert_log(db_session, COMPANY_A, "open_remediation_ticket", "rejected", offset_days=1)
        db_session.commit()

        result = json.loads(get_recent_audit_logs.invoke({"company_id": COMPANY_A, "decision_filter": "rejected"}))
        assert len(result) == 1
        assert result[0]["decision"] == "rejected"

    def test_tenant_isolation(self, db_session):
        self._insert_log(db_session, COMPANY_B, "create_upgrade_order", "approved")
        db_session.commit()

        result = get_recent_audit_logs.invoke({"company_id": COMPANY_A})
        assert result == "No recent audit logs found matching the criteria."

    def test_returns_evidence_fields(self, db_session):
        self._insert_log(db_session, COMPANY_A, "flag_device_for_replacement", "approved")
        db_session.commit()

        result = json.loads(get_recent_audit_logs.invoke({"company_id": COMPANY_A}))
        assert "log_id" in result[0]
        assert "timestamp" in result[0]
        assert "target_details" in result[0]


# ===========================================================================
# analyze_compliance_drift
# ===========================================================================

class TestAnalyzeComplianceDrift:

    def test_detects_drifting_worse(self, db_session):
        # Early snapshots: mostly passing. Recent snapshots: mostly failing.
        for i in range(6):
            snap = make_snapshot(db_session, device_id="dev-drift",
                                 collected_at=BASE_TIME + timedelta(days=i))
            status = "pass" if i < 3 else "fail"  # first 3 pass, last 3 fail
            make_compliance(db_session, snap, "screen_lock", status, "high")
        db_session.commit()

        result = json.loads(analyze_compliance_drift.invoke({"company_id": COMPANY_A}))

        assert len(result) == 1
        assert result[0]["trend"] == "drifting_worse"
        assert result[0]["check_id"] == "screen_lock"
        assert result[0]["total_snapshots_analyzed"] == 6

    def test_detects_improving(self, db_session):
        # Early snapshots: mostly failing. Recent: mostly passing.
        for i in range(6):
            snap = make_snapshot(db_session, device_id="dev-improve",
                                 collected_at=BASE_TIME + timedelta(days=i))
            status = "fail" if i < 3 else "pass"
            make_compliance(db_session, snap, "firewall", status, "medium")
        db_session.commit()

        result = json.loads(analyze_compliance_drift.invoke({"company_id": COMPANY_A}))

        assert any(r["trend"] == "improving" for r in result)

    def test_detects_persistently_failing(self, db_session):
        for i in range(4):
            snap = make_snapshot(db_session, device_id="dev-persistent",
                                 collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap, "disk_encryption", "fail", "high")
        db_session.commit()

        result = json.loads(analyze_compliance_drift.invoke({"company_id": COMPANY_A}))

        assert result[0]["trend"] == "persistently_failing"
        assert result[0]["total_failures"] == 4

    def test_consistently_passing_is_excluded(self, db_session):
        # A device passing all checks should not appear in results
        for i in range(4):
            snap = make_snapshot(db_session, device_id="dev-clean",
                                 collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap, "screen_lock", "pass", "high")
        db_session.commit()

        result = analyze_compliance_drift.invoke({"company_id": COMPANY_A})
        assert "consistently passing" in result.lower() or result == json.dumps([]) or \
               "No compliance drift" in result

    def test_check_id_filter(self, db_session):
        # Two different checks — only one should appear when filtered
        for i in range(4):
            snap = make_snapshot(db_session, device_id="dev-filter",
                                 collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap, "screen_lock", "fail", "high")
            make_compliance(db_session, snap, "firewall", "fail", "medium")
        db_session.commit()

        result = json.loads(analyze_compliance_drift.invoke({
            "company_id": COMPANY_A,
            "check_id": "screen_lock"
        }))

        assert all(r["check_id"] == "screen_lock" for r in result)

    def test_requires_at_least_two_snapshots(self, db_session):
        # Only one snapshot — can't compute drift
        snap = make_snapshot(db_session, device_id="dev-single")
        make_compliance(db_session, snap, "screen_lock", "fail", "high")
        db_session.commit()

        result = analyze_compliance_drift.invoke({"company_id": COMPANY_A})
        assert "No compliance drift" in result

    def test_tenant_isolation(self, db_session):
        for i in range(4):
            snap = make_snapshot(db_session, device_id="dev-other-company",
                                 company_id=COMPANY_B,
                                 collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap, "screen_lock", "fail", "high")
        db_session.commit()

        result = analyze_compliance_drift.invoke({"company_id": COMPANY_A})
        assert "No compliance drift" in result

    def test_drifting_worse_sorted_first(self, db_session):
        # Create one drifting device and one persistently failing device
        for i in range(6):
            snap_drift = make_snapshot(db_session, device_id="dev-drift-sort",
                                       collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap_drift, "screen_lock",
                            "pass" if i < 3 else "fail", "high")

            snap_persist = make_snapshot(db_session, device_id="dev-persist-sort",
                                         collected_at=BASE_TIME + timedelta(days=i))
            make_compliance(db_session, snap_persist, "firewall", "fail", "high")
        db_session.commit()

        result = json.loads(analyze_compliance_drift.invoke({"company_id": COMPANY_A}))

        trends = [r["trend"] for r in result]
        # drifting_worse must appear before persistently_failing
        assert trends.index("drifting_worse") < trends.index("persistently_failing")
