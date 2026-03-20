"""Host-level system resource stats from /proc."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class SystemStats:
    # CPU
    cpu_count: int = 0
    cpu_usage_pct: float = 0.0
    # Memory
    mem_total_bytes: int = 0
    mem_used_bytes: int = 0
    mem_pct: float = 0.0
    # Swap
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0
    swap_pct: float = 0.0
    # Load average
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0


# Store previous CPU readings for delta calculation
_prev_cpu: tuple[int, int] | None = None


def query_system_stats() -> SystemStats:
    """Read system stats from /proc."""
    stats = SystemStats()
    stats.cpu_count = os.cpu_count() or 1
    _read_cpu(stats)
    _read_memory(stats)
    _read_loadavg(stats)
    return stats


def _read_cpu(stats: SystemStats) -> None:
    global _prev_cpu
    try:
        with open("/proc/stat") as f:
            line = f.readline()
    except OSError:
        return

    # cpu  user nice system idle iowait irq softirq steal
    parts = line.split()
    if parts[0] != "cpu":
        return

    values = [int(v) for v in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    total = sum(values)

    if _prev_cpu is not None:
        prev_idle, prev_total = _prev_cpu
        d_total = total - prev_total
        d_idle = idle - prev_idle
        if d_total > 0:
            stats.cpu_usage_pct = (1.0 - d_idle / d_total) * 100.0

    _prev_cpu = (idle, total)


def _read_memory(stats: SystemStats) -> None:
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
    except OSError:
        return

    info = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            info[key] = int(parts[1]) * 1024  # kB to bytes

    stats.mem_total_bytes = info.get("MemTotal", 0)
    mem_avail = info.get("MemAvailable", 0)
    stats.mem_used_bytes = stats.mem_total_bytes - mem_avail
    if stats.mem_total_bytes > 0:
        stats.mem_pct = stats.mem_used_bytes / stats.mem_total_bytes * 100.0

    stats.swap_total_bytes = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    stats.swap_used_bytes = stats.swap_total_bytes - swap_free
    if stats.swap_total_bytes > 0:
        stats.swap_pct = stats.swap_used_bytes / stats.swap_total_bytes * 100.0


def _read_loadavg(stats: SystemStats) -> None:
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
    except OSError:
        return

    if len(parts) >= 3:
        stats.load_1 = float(parts[0])
        stats.load_5 = float(parts[1])
        stats.load_15 = float(parts[2])
