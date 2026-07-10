import json
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import sqlite_url
from app.models import OS, Base, Battery, ComplianceResult, DeviceIdentity, DiskVolume, InstalledSoftware, Memory, NetworkInterface, Snapshot 


def load_telemetry_data(json_file_path: str, db_url: str):
    """
    Reads the telemetry JSON file and populates the normalized database.
    """
    # # Setup database engine and create tables
    engine = create_engine(db_url)
    # Base.metadata.drop_all(engine) # Optional: clears DB on rerun for fresh state
    # Base.metadata.create_all(engine)
    
    print(f"Loading data from {json_file_path} into {db_url}...")

    with open(json_file_path, 'r') as f:
        data = [json.loads(line) for line in f if line.strip()]

    # Use a context manager for the session
    with Session(engine) as session:
        for item in data:
            # Parse timestamp safely
            collected_at = datetime.strptime(item['collected_at'], "%Y-%m-%dT%H:%M:%SZ")
            
            # --- Build 1:1 Relationships ---
            os_data = item.get('os', {})
            os_record = OS(
                platform=os_data.get('platform'),
                product_name=os_data.get('product_name'),
                product_version=os_data.get('product_version'),
                build_version=os_data.get('build_version'),
                architecture=os_data.get('architecture'),
                kernel_name=os_data.get('kernel_name'),
                kernel_release=os_data.get('kernel_release'),
                hostname=os_data.get('hostname')
            )

            id_data = item.get('device_identity', {})
            identity_record = DeviceIdentity(
                serial_number=id_data.get('serial_number'),
                model_name=id_data.get('model_name'),
                model_identifier=id_data.get('model_identifier'),
                processor=id_data.get('processor'),
                hardware_uuid=id_data.get('hardware_uuid'),
                total_memory=id_data.get('total_memory')
            )

            mem_data = item.get('memory', {})
            memory_record = Memory(
                ram_bytes=mem_data.get('ram_bytes'),
                total_memory_bytes=mem_data.get('total_memory_bytes'),
                used_memory_bytes=mem_data.get('used_memory_bytes'),
                free_memory_bytes=mem_data.get('free_memory_bytes'),
                page_size_bytes=mem_data.get('page_size_bytes')
            )

            battery_record = None
            if 'battery' in item:
                bat_data = item['battery']
                battery_record = Battery(
                    battery_present=bat_data.get('battery_present'),
                    charging_status=bat_data.get('charging_status'),
                    percentage=bat_data.get('percentage'),
                    condition=bat_data.get('condition'),
                    cycle_count=bat_data.get('cycle_count'),
                    full_charge_capacity=bat_data.get('full_charge_capacity')
                )

            # --- Build 1:Many Relationships (Lists) ---
            disk_records = [
                DiskVolume(
                    volume_name=dv.get('volume_name'),
                    file_system=dv.get('file_system'),
                    mount_point=dv.get('mount_point'),
                    size_bytes=dv.get('size_bytes'),
                    available_bytes=dv.get('available_bytes'),
                    encrypted=dv.get('encrypted')
                ) for dv in item.get('disk_volumes', [])
            ]

            network_records = [
                NetworkInterface(
                    address=net.get('address'),
                    family=net.get('family'),
                    interface_name=net.get('interface_name'),
                    internal=net.get('internal'),
                    mac=net.get('mac')
                ) for net in item.get('network', [])
            ]

            software_records = [
                InstalledSoftware(
                    name=sw.get('name'),
                    version=sw.get('version'),
                    publisher=sw.get('publisher')
                ) for sw in item.get('installed_software', [])
            ]

            compliance_records = [
                ComplianceResult(
                    check_id=cr.get('check_id'),
                    status=cr.get('status'),
                    severity=cr.get('severity')
                ) for cr in item.get('compliance_results', [])
            ]

            # --- Assemble the Core Snapshot ---
            snapshot = Snapshot(
                device_id=item.get('device_id'),
                company_id=item.get('company_id'),
                employee_id=item.get('employee_id'),
                collected_at=collected_at,
                agent_version=item.get('agent_version'),
                
                # Attach all relationships directly in memory
                os=os_record,
                device_identity=identity_record,
                memory=memory_record,
                battery=battery_record,
                disk_volumes=disk_records,
                network_interfaces=network_records,
                installed_software=software_records,
                compliance_results=compliance_records
            )
            
            # Add to session
            session.add(snapshot)
            
        # Commit all snapshots and their relationships to the database
        session.commit()
        print("Data parsing and database insertion complete.")

# If you want to run this file directly to test the ingestion:
if __name__ == "__main__":
    import os
    data_path = os.path.join(os.path.dirname(__file__), 'device-telemetry-dataset.ndjson')
    load_telemetry_data(data_path, db_url=sqlite_url)