"""Pydantic schemas for all collector JSON outputs with dynamic registry."""
from __future__ import annotations

from typing import Annotated, Any, Union, Literal
from pydantic import BaseModel, Field, ConfigDict

# ── Schema registry ───────────────────────────────────────────────────────────

_COLLECTOR_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_collector_schema(name: str):
    """Decorator that registers a collector's Pydantic schema dynamically.

    Each collector calls this decorator in ``schemas.py`` to register its schema.
    The registry is used by ``validate_knowledge_json()`` so that no manual
    modification of a Union type is required.
    """
    def decorator(schema_class: type[BaseModel]) -> type[BaseModel]:
        _COLLECTOR_SCHEMA_REGISTRY[name] = schema_class
        return schema_class
    return decorator


def validate_knowledge_json(data: dict[str, Any]) -> BaseModel | dict[str, Any]:
    """Validate a knowledge JSON against the appropriate registered schema.

    Args:
        data: Parsed JSON dictionary with a ``source`` field.

    Returns:
        Validated schema instance, or the raw dict if no schema is registered.

    Raises:
        ValidationError: If Pydantic validation fails.
    """
    source = data.get("source", "unknown")
    schema_cls = _COLLECTOR_SCHEMA_REGISTRY.get(source)
    if schema_cls is None:
        return data
    return schema_cls.model_validate(data)


# ── Common fields ─────────────────────────────────────────────────────────────

class CollectorCapabilitiesSchema(BaseModel):
    """Capabilities describing the runtime context of the collector."""
    supported_platforms: list[str] = Field(default_factory=list)
    min_confidence: float = 1.0
    known_inconsistencies: list[str] = Field(default_factory=list)


class CommonSchema(BaseModel):
    """Shared fields present in every collector JSON output."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    schema_version: str = Field(pattern=r"^\d+\.\d+$")
    collector_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    server_uuid: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    timestamp_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    source: str
    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    dependencies: list[str] = Field(default_factory=list)
    inconsistencies_detected: list[str] = Field(default_factory=list)


# ── CPU ───────────────────────────────────────────────────────────────────────

class CPUContent(BaseModel):
    model: str
    architecture: str
    cores_physical: int
    cores_logical: int
    threads_per_core: float
    frequency_mhz: float | None = None
    frequency_max_mhz: float | None = None
    cpu_flags: list[str] = Field(default_factory=list)
    load_average_1m: float
    load_average_5m: float
    load_average_15m: float
    usage_percent: float | None = None
    temperature_celsius: float | None = None


@register_collector_schema("cpu")
class CPUCollectorSchema(CommonSchema):
    """Schema for the CPU collector."""
    source: Literal["cpu"] = "cpu"
    content: CPUContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── System ────────────────────────────────────────────────────────────────────

class SystemContent(BaseModel):
    hostname: str
    domain: str | None = None
    os_name: str
    os_version: str
    kernel: str
    kernel_release: str
    uptime_seconds: int
    architecture: str


@register_collector_schema("system")
class SystemCollectorSchema(CommonSchema):
    """Schema for the system/hostname collector."""
    source: Literal["system"] = "system"
    content: SystemContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── RAM ───────────────────────────────────────────────────────────────────────

class RAMContent(BaseModel):
    total_bytes: int
    available_bytes: int
    used_bytes: int
    free_bytes: int
    buffers_bytes: int
    cached_bytes: int
    swap_total_bytes: int
    swap_used_bytes: int
    swap_free_bytes: int
    usage_percent: float


@register_collector_schema("ram")
class RAMCollectorSchema(CommonSchema):
    """Schema for the RAM/memory collector."""
    source: Literal["ram"] = "ram"  # Correction : Doit correspondre à la clé d'enregistrement
    content: RAMContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── Storage ───────────────────────────────────────────────────────────────────

class StorageMount(BaseModel):
    device: str
    mountpoint: str
    fstype: str
    size_bytes: int
    used_bytes: int
    available_bytes: int
    usage_percent: float


class StorageContent(BaseModel):
    mounts: list[StorageMount] = Field(default_factory=list)
    total_devices: int = 0


@register_collector_schema("storage")
class StorageCollectorSchema(CommonSchema):
    """Schema for the storage/df collector."""
    source: Literal["storage"] = "storage"
    content: StorageContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── Network ───────────────────────────────────────────────────────────────────

class NetworkInterfaceIP(BaseModel):
    address: str
    netmask: str | None = None
    broadcast: str | None = None


class NetworkInterface(BaseModel):
    name: str
    type: str
    state: str
    mtu: int
    mac_address: str | None = None
    ipv4: list[NetworkInterfaceIP] = Field(default_factory=list)
    ipv6: list[NetworkInterfaceIP] = Field(default_factory=list)
    speed_mbps: int | None = None
    duplex: str | None = None


class NetworkRoute(BaseModel):
    destination: str
    gateway: str | None = None
    interface: str | None = None
    metric: int | None = None


class ListeningPort(BaseModel):
    protocol: str
    local_address: str
    port: int
    process: str | None = None


class NetworkContent(BaseModel):
    hostname: str
    domain: str | None = None
    interfaces: list[NetworkInterface] = Field(default_factory=list)
    routes: list[NetworkRoute] = Field(default_factory=list)
    listening_ports: list[ListeningPort] = Field(default_factory=list)
    established_connections: int = 0
    dns_servers: list[str] = Field(default_factory=list)


@register_collector_schema("network")
class NetworkCollectorSchema(CommonSchema):
    """Schema for the network collector."""
    source: Literal["network"] = "network"
    content: NetworkContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── Open Ports ────────────────────────────────────────────────────────────────

class OpenPortEntry(BaseModel):
    protocol: Literal["tcp", "udp"]
    local_address: str
    port: int
    process: str | None = None
    pid: int | None = None


class PortsContent(BaseModel):
    open_ports: list[OpenPortEntry] = Field(default_factory=list)
    total_open_ports: int = 0


@register_collector_schema("ports")
class PortsCollectorSchema(CommonSchema):
    """Schema for the listening ports collector."""
    source: Literal["ports"] = "ports"
    content: PortsContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── Docker ────────────────────────────────────────────────────────────────────

class ContainerState(BaseModel):
    status: str
    running: bool
    paused: bool
    restarting: bool
    exit_code: int
    started_at: str | None = None
    finished_at: str | None = None


class ContainerPort(BaseModel):
    private_port: int
    public_port: int | None = None
    protocol: str
    type: str = "host"


class ContainerResourceLimits(BaseModel):
    cpu_shares: int | None = None
    memory_limit_bytes: int | None = None
    memory_reservation_bytes: int | None = None


class Container(BaseModel):
    id: str
    short_id: str
    name: str
    image: str
    status: str
    created: str
    state: ContainerState
    ports: list[ContainerPort] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    networks: list[str] = Field(default_factory=list)
    mounts: list[dict] = Field(default_factory=list)
    resource_limits: ContainerResourceLimits | None = None


class DockerNetwork(BaseModel):
    name: str
    driver: str
    scope: str
    internal: bool = False


class DockerVolume(BaseModel):
    name: str
    driver: str
    mountpoint: str | None = None


class DockerContent(BaseModel):
    docker_version: str | None = None
    api_version: str | None = None
    os: str | None = None
    kernel_version: str | None = None
    server_uuid: str | None = None
    containers_total: int = 0
    containers_running: int = 0
    containers_paused: int = 0
    containers_stopped: int = 0
    images_count: int = 0
    memory_limit_enabled: bool = False
    swap_limit_enabled: bool = False
    cgroup_driver: str | None = None
    runtime: str | None = None
    containers: list[Container] = Field(default_factory=list)
    networks: list[DockerNetwork] = Field(default_factory=list)
    volumes: list[DockerVolume] = Field(default_factory=list)


@register_collector_schema("docker")
class DockerCollectorSchema(CommonSchema):
    """Schema for the Docker collector."""
    source: Literal["docker"] = "docker"
    content: DockerContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── Systemd services ──────────────────────────────────────────────────────────

class SystemdService(BaseModel):
    name: str
    load: str
    active: str
    sub: str
    description: str | None = None


class SystemdServicesContent(BaseModel):
    total_units: int = 0
    running: int = 0
    failed: int = 0
    services: list[SystemdService] = Field(default_factory=list)


@register_collector_schema("systemd_services")
class SystemdServicesCollectorSchema(CommonSchema):
    """Schema for the systemd services collector."""
    source: Literal["systemd_services"] = "systemd_services"
    content: SystemdServicesContent
    capabilities: CollectorCapabilitiesSchema | None = None


# ── APT/Packages ──────────────────────────────────────────────────────────────

class APTPackageSchema(BaseModel):
    name: str = Field(..., description="Name of the debian package")
    version: str = Field(..., description="Installed package version")
    architecture: str = Field(..., description="Target CPU architecture")
    size_bytes: int = Field(..., description="Approximate installed size on disk in bytes")
    summary: str = Field(..., description="Short summary description of the package")


class APTContent(BaseModel):
    packages: list[APTPackageSchema] = Field(default_factory=list)
    total_packages: int = 0


@register_collector_schema("apt")
class APTCollectorSchema(CommonSchema):
    """Schema for the APT/DPKG installed packages collector."""
    source: Literal["apt"] = "apt"
    content: APTContent
    capabilities: CollectorCapabilitiesSchema | None = None
