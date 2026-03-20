"""Query Docker for container resource stats."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import docker
from docker.errors import APIError, NotFound


@dataclass
class ContainerStats:
    container_id: str
    short_id: str
    name: str
    image: str
    status: str  # running, exited, paused, restarting, created, dead
    health: str | None  # healthy, unhealthy, starting, none
    # Metadata
    compose_project: str | None = None
    restart_count: int = 0
    started_at: datetime | None = None
    # CPU
    cpu_usage_pct: float = 0.0
    cpu_count: int = 0
    # Memory
    mem_used_bytes: int = 0
    mem_limit_bytes: int = 0
    mem_pct: float = 0.0
    # Network
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0
    # Block I/O
    blk_read_bytes: int = 0
    blk_write_bytes: int = 0
    # PIDs
    pids: int = 0
    # GPU (filled in later)
    gpu_procs: list = field(default_factory=list)
    gpu_mem_used_mib: int = 0
    gpu_indices: list[int] = field(default_factory=list)


def list_containers(
    client: docker.DockerClient,
    status_filter: str | None = None,
) -> list[ContainerStats]:
    """List containers with optional status filter. Returns basic info without stats."""
    filters = {}
    if status_filter:
        # Support comma-separated statuses
        statuses = [s.strip() for s in status_filter.split(",")]
        filters["status"] = statuses

    containers = client.containers.list(all=True, filters=filters)
    results = []
    for c in containers:
        health = None
        state = c.attrs.get("State", {})
        health_obj = state.get("Health")
        if health_obj:
            health = health_obj.get("Status")

        # Compose project from labels
        labels = c.labels or {}
        compose_project = labels.get("com.docker.compose.project")

        # Restart count
        restart_count = c.attrs.get("RestartCount", 0)

        # Started at
        started_at = _parse_docker_time(state.get("StartedAt"))

        results.append(ContainerStats(
            container_id=c.id,
            short_id=c.short_id,
            name=c.name,
            image=_image_tag(c),
            status=c.status,
            health=health,
            compose_project=compose_project,
            restart_count=restart_count,
            started_at=started_at,
        ))
    return results


def fetch_live_stats(client: docker.DockerClient, cs: ContainerStats) -> None:
    """Populate a ContainerStats with live resource usage. Only works for running containers."""
    if cs.status != "running":
        return
    try:
        container = client.containers.get(cs.container_id)
        stats = container.stats(stream=False)
    except (NotFound, APIError):
        return

    _parse_cpu(stats, cs)
    _parse_memory(stats, cs)
    _parse_network(stats, cs)
    _parse_blkio(stats, cs)
    cs.pids = stats.get("pids_stats", {}).get("current", 0) or 0


def _parse_cpu(stats: dict, cs: ContainerStats) -> None:
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})
    cpu_delta = (cpu.get("cpu_usage", {}).get("total_usage", 0)
                 - precpu.get("cpu_usage", {}).get("total_usage", 0))
    sys_delta = (cpu.get("system_cpu_usage", 0)
                 - precpu.get("system_cpu_usage", 0))
    online = cpu.get("online_cpus", 1) or 1
    cs.cpu_count = online
    if sys_delta > 0:
        cs.cpu_usage_pct = (cpu_delta / sys_delta) * online * 100.0


def _parse_memory(stats: dict, cs: ContainerStats) -> None:
    mem = stats.get("memory_stats", {})
    cs.mem_used_bytes = mem.get("usage", 0) - mem.get("stats", {}).get("cache", 0)
    cs.mem_limit_bytes = mem.get("limit", 0)
    if cs.mem_limit_bytes > 0:
        cs.mem_pct = cs.mem_used_bytes / cs.mem_limit_bytes * 100.0


def _parse_network(stats: dict, cs: ContainerStats) -> None:
    nets = stats.get("networks", {})
    for iface_stats in nets.values():
        cs.net_rx_bytes += iface_stats.get("rx_bytes", 0)
        cs.net_tx_bytes += iface_stats.get("tx_bytes", 0)


def _parse_blkio(stats: dict, cs: ContainerStats) -> None:
    blkio = stats.get("blkio_stats", {})
    for entry in blkio.get("io_service_bytes_recursive", []) or []:
        op = entry.get("op", "").lower()
        if op == "read":
            cs.blk_read_bytes += entry.get("value", 0)
        elif op == "write":
            cs.blk_write_bytes += entry.get("value", 0)


def _image_tag(c) -> str:
    tags = c.image.tags if c.image else []
    if tags:
        return tags[0]
    return c.attrs.get("Config", {}).get("Image", "unknown")


def _parse_docker_time(s: str | None) -> datetime | None:
    """Parse Docker's ISO 8601 timestamp."""
    if not s or s.startswith("0001"):
        return None
    try:
        # Docker uses format like 2024-01-15T10:30:00.123456789Z
        # Truncate nanoseconds to microseconds
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def format_uptime(started_at: datetime | None) -> str:
    """Format uptime as a compact human-readable string."""
    if started_at is None:
        return "-"
    delta = datetime.now(timezone.utc) - started_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "-"

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
