import json
from typing import List, Dict, Any
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from langchain_core.tools import tool


from app.models import Snapshot, ComplianceResult, Battery, DiskVolume, Memory, AuditLog
from app.core.database import engine


def get_latest_snapshot_subquery(company_id: str):
    """Helper to generate a subquery that fetches the latest collected_at timestamp per device."""
    return (
        select(
            Snapshot.device_id, 
            func.max(Snapshot.collected_at).label('latest_time')
        )
        .where(Snapshot.company_id == company_id)
        .group_by(Snapshot.device_id)
        .subquery()
    )


@tool
def check_fleet_compliance(company_id: str, severity: str = None) -> str:
    """
    Checks the fleet for devices failing security or OS compliance checks.
    Use this to answer questions about non-compliant devices, missing screen locks, or outdated OS versions.
    
    Args:
        company_id: The ID of the company/tenant.
        severity: Optional filter for severity level (e.g., 'high', 'medium').
    """
    latest_subq = get_latest_snapshot_subquery(company_id)
    
    stmt = (
        select(Snapshot, ComplianceResult)
        .join(latest_subq, 
             (Snapshot.device_id == latest_subq.c.device_id) & 
             (Snapshot.collected_at == latest_subq.c.latest_time))
        .join(Snapshot.compliance_results)
        .where(Snapshot.company_id == company_id)
        .where(ComplianceResult.status == 'fail')
    )
    
    if severity:
        stmt = stmt.where(ComplianceResult.severity == severity.lower())

    results = []
    with Session(engine) as session:
        for snapshot, compliance in session.execute(stmt):
            results.append({
                "device_id": snapshot.device_id,
                "employee_id": snapshot.employee_id,
                "failing_check": compliance.check_id,
                "severity": compliance.severity,
                "evidence_snapshot_id": snapshot.id,
                "collected_at": snapshot.collected_at.isoformat()
            })
            
    return json.dumps(results) if results else "No compliance failures found."


@tool
def analyze_battery_degradation(company_id: str, cycle_threshold: int = 500) -> str:
    """
    Identifies devices with batteries approaching end-of-life based on high cycle counts.
    Use this to detect trends or propose hardware replacements.
    
    Args:
        company_id: The ID of the company/tenant.
        cycle_threshold: The cycle count limit to flag. Defaults to 500.
    """
    latest_subq = get_latest_snapshot_subquery(company_id)
    
    stmt = (
        select(Snapshot, Battery)
        .join(latest_subq, 
             (Snapshot.device_id == latest_subq.c.device_id) & 
             (Snapshot.collected_at == latest_subq.c.latest_time))
        .join(Snapshot.battery)
        .where(Snapshot.company_id == company_id)
        .where(Battery.cycle_count >= cycle_threshold)
    )

    results = []
    with Session(engine) as session:
        for snapshot, battery in session.execute(stmt):
            results.append({
                "device_id": snapshot.device_id,
                "cycle_count": battery.cycle_count,
                "condition": battery.condition,
                "capacity": battery.full_charge_capacity,
                "evidence_snapshot_id": snapshot.id,
                "collected_at": snapshot.collected_at.isoformat()
            })
            
    return json.dumps(results) if results else "No batteries exceed the cycle threshold."


@tool
def get_low_disk_space_devices(company_id: str, min_available_gb: int = 50) -> str:
    """
    Finds devices that are consistently low on storage space.
    
    Args:
        company_id: The ID of the company/tenant.
        min_available_gb: The threshold in Gigabytes. Devices with less than this available space are returned.
    """
    # Convert GB to Bytes for the query
    min_bytes = min_available_gb * 1024 * 1024 * 1024
    latest_subq = get_latest_snapshot_subquery(company_id)
    
    stmt = (
        select(Snapshot, DiskVolume)
        .join(latest_subq, 
             (Snapshot.device_id == latest_subq.c.device_id) & 
             (Snapshot.collected_at == latest_subq.c.latest_time))
        .join(Snapshot.disk_volumes)
        .where(Snapshot.company_id == company_id)
        .where(DiskVolume.available_bytes < min_bytes)
    )

    results = []
    with Session(engine) as session:
        for snapshot, disk in session.execute(stmt):
            # Convert bytes back to GB for LLM readability
            available_gb = round(disk.available_bytes / (1024**3), 2)
            results.append({
                "device_id": snapshot.device_id,
                "volume_name": disk.volume_name,
                "available_gb": available_gb,
                "evidence_snapshot_id": snapshot.id,
                "collected_at": snapshot.collected_at.isoformat()
            })
            
    return json.dumps(results) if results else f"No devices have less than {min_available_gb}GB of free space."


@tool
def propose_remediation_action(company_id: str, device_id: str, action_type: str, reason: str, **kwargs) -> str:
    """
    Initiates a state-changing operational action for IT administrators.
    MUST be called when proposing to fix an issue discovered in telemetry data.
    
    Args:
        company_id: The ID of the company/tenant.
        device_id: The target device ID.
        action_type: Must be one of: 'create_upgrade_order', 'open_remediation_ticket', 'notify_employee', 'flag_device_for_replacement'.
        reason: Detailed justification for the action citing specific telemetry evidence.
    """
    valid_actions = ['create_upgrade_order', 'open_remediation_ticket', 'notify_employee', 'flag_device_for_replacement']
    
    if action_type not in valid_actions:
        return json.dumps({"error": f"Invalid action_type. Must be one of {valid_actions}"})

    # In a real system, this would write to a 'pending_actions' table or an external API.
    # For LangGraph, this returns a structured payload that the graph state will catch 
    # to trigger an interrupt_before (Human-in-the-Loop) pause.
    proposal = {
        "status": "PENDING_HUMAN_APPROVAL",
        "company_id": company_id,
        "device_id": device_id,
        "action": action_type,
        "justification": reason,
        "additional_params": kwargs
    }
    
    return json.dumps(proposal)


@tool
def analyze_ram_constraints_over_time(company_id: str, constraint_threshold_percent: float = 0.90) -> str:
    """
    Identifies devices that are consistently constrained by RAM over time.
    Flags devices where memory usage exceeds the threshold (default 90%) in more than 50% of their historical snapshots.
    
    Args:
        company_id: The ID of the company/tenant.
        constraint_threshold_percent: The usage percentage to consider "constrained" (e.g. 0.90 for 90%).
    """
    # Query all snapshots for the company to calculate historical trends
    stmt = (
        select(Snapshot, Memory)
        .join(Snapshot.memory)
        .where(Snapshot.company_id == company_id)
        .order_by(Snapshot.device_id, Snapshot.collected_at)
    )

    device_stats = {}
    with Session(engine) as session:
        for snapshot, memory in session.execute(stmt):
            device_id = snapshot.device_id
            if device_id not in device_stats:
                device_stats[device_id] = {"total_snapshots": 0, "constrained_snapshots": 0}
            
            device_stats[device_id]["total_snapshots"] += 1
            
            # Guard against missing or zero memory data
            if not memory.total_memory_bytes or not memory.used_memory_bytes:
                continue

            # Calculate memory usage percentage
            usage_ratio = memory.used_memory_bytes / memory.total_memory_bytes
            if usage_ratio >= constraint_threshold_percent:
                device_stats[device_id]["constrained_snapshots"] += 1

    # Filter for devices that are constrained in > 50% of their recorded history
    results = []
    for device_id, stats in device_stats.items():
        if stats["total_snapshots"] > 0:
            constrained_ratio = stats["constrained_snapshots"] / stats["total_snapshots"]
            if constrained_ratio > 0.50:
                results.append({
                    "device_id": device_id,
                    "total_snapshots_analyzed": stats["total_snapshots"],
                    "times_constrained": stats["constrained_snapshots"],
                    "insight": f"Device exceeded {constraint_threshold_percent*100}% RAM usage in {constrained_ratio*100:.1f}% of historical snapshots."
                })
                
    return json.dumps(results) if results else "No devices show consistent RAM constraints over time."


@tool
def get_recent_audit_logs(company_id: str, limit: int = 5, decision_filter: str = None) -> str:
    """
    Retrieves recent administrative actions and audit logs for the tenant,
    ordered by most recent first.

    To get the N most recent actions regardless of outcome, call with just
    company_id and limit — do NOT set decision_filter.
    Only set decision_filter when the user explicitly wants only 'approved'
    or only 'rejected' entries.

    Use this to answer questions about the history of actions taken on the fleet,
    or what the last N approved/rejected decisions were.

    Args:
        company_id: The ID of the company/tenant.
        limit: The maximum number of recent logs to retrieve. Defaults to 5.
        decision_filter: Optional. Set to 'approved' or 'rejected' ONLY when
                         the user explicitly asks to filter by decision outcome.
                         Leave unset to retrieve the most recent actions of any kind.
    """
    stmt = (
        select(AuditLog)
        .where(AuditLog.company_id == company_id)
        .order_by(AuditLog.timestamp.desc())
    )
    
    if decision_filter:
        stmt = stmt.where(AuditLog.human_decision == decision_filter.lower())
        
    stmt = stmt.limit(limit)

    results = []
    with Session(engine) as session:
        for log in session.scalars(stmt):
            results.append({
                "log_id": log.id,
                "timestamp": log.timestamp.isoformat(),
                "action_type": log.action,
                "decision": log.human_decision,
                "target_details": log.proposal_details 
            })
            
    return json.dumps(results) if results else "No recent audit logs found matching the criteria."


@tool
def analyze_compliance_drift(company_id: str, check_id: str = None) -> str:
    """
    Analyzes compliance trends over time across all historical snapshots.
    Identifies devices that are drifting (more failures over time), improving,
    or persistently failing a compliance check.
    Use this to answer questions about compliance trends, worsening posture,
    or which devices have never passed a specific check.

    Args:
        company_id: The ID of the company/tenant.
        check_id: Optional. Filter to a specific compliance check (e.g. 'screen_lock', 'firewall').
                  If omitted, analyzes all checks.
    """
    stmt = (
        select(Snapshot, ComplianceResult)
        .join(Snapshot.compliance_results)
        .where(Snapshot.company_id == company_id)
        .order_by(Snapshot.device_id, ComplianceResult.check_id, Snapshot.collected_at)
    )

    if check_id:
        stmt = stmt.where(ComplianceResult.check_id == check_id)

    # Build per-device, per-check timeline: list of (collected_at, status)
    # Structure: { device_id: { check_id: [ (timestamp, status), ... ] } }
    timeline: dict = {}
    with Session(engine) as session:
        for snapshot, compliance in session.execute(stmt):
            dev = snapshot.device_id
            chk = compliance.check_id
            timeline.setdefault(dev, {}).setdefault(chk, []).append(
                (snapshot.collected_at, compliance.status)
            )

    results = []
    for device_id, checks in timeline.items():
        for chk, entries in checks.items():
            if len(entries) < 2:
                # Need at least 2 data points to detect drift
                continue

            # Sort chronologically. It should already be ordered, but be safe
            entries.sort(key=lambda x: x[0])
            total = len(entries)
            fail_count = sum(1 for _, s in entries if s == "fail")

            # Split history into first half and second half to detect trend direction
            mid = total // 2
            early_fails = sum(1 for _, s in entries[:mid] if s == "fail")
            recent_fails = sum(1 for _, s in entries[mid:] if s == "fail")

            early_rate = early_fails / mid if mid > 0 else 0
            recent_rate = recent_fails / (total - mid) if (total - mid) > 0 else 0

            # Determine trend
            if recent_rate > early_rate + 0.2:
                trend = "drifting_worse"
                insight = (
                    f"Compliance failure rate increased from {early_rate*100:.0f}% "
                    f"(early) to {recent_rate*100:.0f}% (recent). Posture is deteriorating."
                )
            elif early_rate > recent_rate + 0.2:
                trend = "improving"
                insight = (
                    f"Compliance failure rate decreased from {early_rate*100:.0f}% "
                    f"(early) to {recent_rate*100:.0f}% (recent). Posture is improving."
                )
            elif fail_count == total:
                trend = "persistently_failing"
                insight = f"Device has failed this check in all {total} recorded snapshots."
            elif fail_count == 0:
                trend = "consistently_passing"
                insight = f"Device has passed this check in all {total} recorded snapshots."
            else:
                trend = "stable_mixed"
                insight = (
                    f"Device fails this check in {fail_count}/{total} snapshots "
                    f"with no clear directional trend."
                )

            # Only surface actionable findings. Skip consistently passing
            if trend == "consistently_passing":
                continue

            results.append({
                "device_id": device_id,
                "check_id": chk,
                "trend": trend,
                "total_snapshots_analyzed": total,
                "total_failures": fail_count,
                "earliest_snapshot": entries[0][0].isoformat(),
                "latest_snapshot": entries[-1][0].isoformat(),
                "insight": insight,
            })

    if not results:
        return "No compliance drift detected. All devices are consistently passing their checks."

    # Sort by most actionable first: drifting_worse > persistently_failing > stable_mixed > improving
    priority = {"drifting_worse": 0, "persistently_failing": 1, "stable_mixed": 2, "improving": 3}
    results.sort(key=lambda r: priority.get(r["trend"], 99))

    return json.dumps(results)


fleet_tools = [
    check_fleet_compliance,
    analyze_battery_degradation,
    get_low_disk_space_devices,
    propose_remediation_action,
    analyze_ram_constraints_over_time,
    analyze_compliance_drift,
    get_recent_audit_logs
]