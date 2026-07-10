from typing import Optional, List, Dict, Any
from datetime import datetime, date

from sqlalchemy import Integer, String, DateTime, Float, ForeignKey, BigInteger, Date, JSON
from sqlalchemy.orm import mapped_column, Mapped, relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = 'snapshots'
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(index=True)
    company_id: Mapped[str] = mapped_column(index=True) # Tenant isolation
    employee_id: Mapped[Optional[str]]
    collected_at: Mapped[datetime] = mapped_column(index=True)
    agent_version: Mapped[Optional[str]]
    
    # 1:1 Relationships (SQLAlchemy infers uselist=False from the scalar type hint)
    os: Mapped[Optional["OS"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    device_identity: Mapped[Optional["DeviceIdentity"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    memory: Mapped[Optional["Memory"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    battery: Mapped[Optional["Battery"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    
    # 1:Many Relationships (SQLAlchemy infers uselist=True from the List type hint)
    disk_volumes: Mapped[List["DiskVolume"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    network_interfaces: Mapped[List["NetworkInterface"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    installed_software: Mapped[List["InstalledSoftware"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    compliance_results: Mapped[List["ComplianceResult"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")

# ---------------------------------------------------------
# 1:1 RELATIONSHIP TABLES
# ---------------------------------------------------------
class OS(Base):
    __tablename__ = 'os_info'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), unique=True)
    
    platform: Mapped[Optional[str]]
    product_name: Mapped[Optional[str]]
    product_version: Mapped[Optional[str]]
    build_version: Mapped[Optional[str]]
    architecture: Mapped[Optional[str]]
    kernel_name: Mapped[Optional[str]]
    kernel_release: Mapped[Optional[str]]
    hostname: Mapped[Optional[str]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="os")


class DeviceIdentity(Base):
    __tablename__ = 'device_identities'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), unique=True)
    
    serial_number: Mapped[Optional[str]] = mapped_column(index=True)
    model_name: Mapped[Optional[str]]
    model_identifier: Mapped[Optional[str]]
    processor: Mapped[Optional[str]]
    hardware_uuid: Mapped[Optional[str]]
    total_memory: Mapped[Optional[str]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="device_identity")


class Memory(Base):
    __tablename__ = 'memory_stats'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), unique=True)
    
    ram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    used_memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    free_memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    page_size_bytes: Mapped[Optional[int]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="memory")


class Battery(Base):
    __tablename__ = 'battery_stats'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), unique=True)
    
    battery_present: Mapped[Optional[bool]]
    charging_status: Mapped[Optional[str]]
    percentage: Mapped[Optional[int]]
    condition: Mapped[Optional[str]]
    cycle_count: Mapped[Optional[int]]
    full_charge_capacity: Mapped[Optional[int]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="battery")


# ---------------------------------------------------------
# 1:MANY RELATIONSHIP TABLES
# ---------------------------------------------------------
class DiskVolume(Base):
    __tablename__ = 'disk_volumes'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), index=True)
    
    volume_name: Mapped[Optional[str]]
    file_system: Mapped[Optional[str]]
    mount_point: Mapped[Optional[str]]
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    available_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    encrypted: Mapped[Optional[bool]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="disk_volumes")


class NetworkInterface(Base):
    __tablename__ = 'network_interfaces'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), index=True)
    
    address: Mapped[Optional[str]]
    family: Mapped[Optional[str]]
    interface_name: Mapped[Optional[str]]
    internal: Mapped[Optional[bool]]
    mac: Mapped[Optional[str]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="network_interfaces")


class InstalledSoftware(Base):
    __tablename__ = 'installed_software'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), index=True)
    
    name: Mapped[Optional[str]] = mapped_column(index=True)
    version: Mapped[Optional[str]]
    publisher: Mapped[Optional[str]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="installed_software")


class ComplianceResult(Base):
    __tablename__ = 'compliance_results'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('snapshots.id', ondelete="CASCADE"), index=True)
    
    check_id: Mapped[Optional[str]] = mapped_column(index=True)
    status: Mapped[Optional[str]] = mapped_column(index=True)
    severity: Mapped[Optional[str]]
    
    snapshot: Mapped["Snapshot"] = relationship(back_populates="compliance_results")


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(index=True)
    company_id: Mapped[str] = mapped_column(index=True)
    action: Mapped[str]
    
    proposal_details: Mapped[Dict[str, Any]] = mapped_column(JSON)
    
    human_decision: Mapped[str]    