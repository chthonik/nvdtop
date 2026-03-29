"""CLI entry point for nvdtop."""

from __future__ import annotations

import sys
import time
import signal
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import docker
from rich.console import Console
from rich.live import Live

from .containers import ContainerStats, list_containers, fetch_live_stats, format_uptime
from .gpu import query_nvidia_smi, map_gpu_to_containers, GpuInfo
from .display import render, build_gpu_panels, build_container_table, build_system_panel
from .system import query_system_stats
from rich.columns import Columns
from rich.text import Text


def _gather(
    client: docker.DockerClient,
    status_filter: str | None,
    name_filter: str | None,
) -> tuple[list[ContainerStats], list[GpuInfo]]:
    """Gather all container and GPU data."""
    containers = list_containers(client, status_filter=status_filter)

    # Name filter
    if name_filter:
        pattern = name_filter.lower()
        containers = [c for c in containers if pattern in c.name.lower()]

    # Fetch live stats for running containers in parallel
    running = [cs for cs in containers if cs.status == "running"]
    with ThreadPoolExecutor(max_workers=min(len(running), 20)) as pool:
        pool.map(lambda cs: fetch_live_stats(client, cs), running)

    # GPU data
    gpus, gpu_procs = query_nvidia_smi()
    if gpu_procs:
        gpu_map = map_gpu_to_containers(gpu_procs)
        for cs in containers:
            # Match by full or short container ID
            for cid, procs in gpu_map.items():
                if cs.container_id.startswith(cid) or cid.startswith(cs.container_id[:12]):
                    cs.gpu_procs = procs
                    cs.gpu_mem_used_mib = sum(p.used_memory_mib for p in procs)
                    cs.gpu_indices = list(sorted(set(p.gpu_index for p in procs)))
                    break

    return containers, gpus


@click.command()
@click.option(
    "-s", "--status",
    default=None,
    help="Filter by container status: running, exited, paused, restarting, created, dead. "
         "Comma-separated for multiple (e.g. 'running,exited').",
)
@click.option(
    "-n", "--name",
    default=None,
    help="Filter containers by name (substring match).",
)
@click.option(
    "--sort",
    type=click.Choice(["gpu", "cpu", "mem", "name"], case_sensitive=False),
    default="gpu",
    help="Sort containers by field.",
)
@click.option(
    "-a", "--all-columns",
    is_flag=True,
    default=False,
    help="Show all columns including network I/O, block I/O, and PIDs.",
)
@click.option(
    "-w", "--watch",
    default=0,
    type=float,
    help="Refresh interval in seconds. 0 = one-shot (default).",
)
@click.option(
    "--no-gpu",
    is_flag=True,
    default=False,
    help="Skip GPU/VRAM queries.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON instead of a table.",
)
def main(
    status: str | None,
    name: str | None,
    sort: str,
    all_columns: bool,
    watch: float,
    no_gpu: bool,
    as_json: bool,
) -> None:
    """nvdtop - Docker container resource monitor with GPU/VRAM tracking.

    Shows CPU, memory, and GPU VRAM usage per container as fractions and
    percentages. Similar to glances but focused on Docker workloads.
    """
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        click.echo(f"Error: Cannot connect to Docker daemon: {e}", err=True)
        click.echo("Make sure Docker is running and you have permission to access it.", err=True)
        sys.exit(1)

    console = Console()

    if as_json:
        _output_json(client, status, name, no_gpu)
        return

    if watch > 0:
        _watch_loop(console, client, status, name, sort, all_columns, watch, no_gpu)
    else:
        containers, gpus = _gather(client, status, name)
        if no_gpu:
            gpus = []
        sys_stats = query_system_stats()
        render(console, containers, gpus, sys_stats=sys_stats, sort_by=sort, show_all=all_columns)


def _watch_loop(
    console: Console,
    client: docker.DockerClient,
    status: str | None,
    name: str | None,
    sort: str,
    all_columns: bool,
    interval: float,
    no_gpu: bool,
) -> None:
    """Live-updating display loop."""
    stop = False

    def on_signal(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while not stop:
            try:
                containers, gpus = _gather(client, status, name)
                if no_gpu:
                    gpus = []

                from rich.console import Group
                sys_stats = query_system_stats()
                parts = []
                parts.append(build_system_panel(sys_stats))
                parts.append(Text())
                if gpus:
                    parts.append(Columns(build_gpu_panels(gpus), equal=True, expand=True))
                    parts.append(Text())
                parts.append(build_container_table(
                    containers, gpus, sort_by=sort, show_all_columns=all_columns,
                ))
                running = sum(1 for c in containers if c.status == "running")
                total = len(containers)
                parts.append(Text(
                    f"\n {running} running / {total} total containers  |  "
                    f"Refresh: {interval}s  |  Ctrl+C to quit",
                    style="dim",
                ))
                live.update(Group(*parts))
                time.sleep(interval)
            except docker.errors.APIError:
                time.sleep(interval)


def _output_json(
    client: docker.DockerClient,
    status: str | None,
    name: str | None,
    no_gpu: bool,
) -> None:
    """Output data as JSON."""
    import json

    containers, gpus = _gather(client, status, name)
    if no_gpu:
        gpus = []
    sys_stats = query_system_stats()

    data = {
        "system": {
            "cpu_count": sys_stats.cpu_count,
            "cpu_usage_pct": round(sys_stats.cpu_usage_pct, 2),
            "mem_total_bytes": sys_stats.mem_total_bytes,
            "mem_used_bytes": sys_stats.mem_used_bytes,
            "mem_pct": round(sys_stats.mem_pct, 2),
            "swap_total_bytes": sys_stats.swap_total_bytes,
            "swap_used_bytes": sys_stats.swap_used_bytes,
            "swap_pct": round(sys_stats.swap_pct, 2),
            "load_1": sys_stats.load_1,
            "load_5": sys_stats.load_5,
            "load_15": sys_stats.load_15,
        },
        "gpus": [
            {
                "index": g.index,
                "name": g.name,
                "total_memory_mib": g.total_memory_mib,
                "used_memory_mib": g.used_memory_mib,
                "free_memory_mib": g.free_memory_mib,
                "temperature_c": g.temperature_c,
                "utilization_pct": g.utilization_pct,
            }
            for g in gpus
        ],
        "containers": [
            {
                "name": c.name,
                "id": c.short_id,
                "image": c.image,
                "status": c.status,
                "health": c.health,
                "compose_project": c.compose_project,
                "restart_count": c.restart_count,
                "ports": c.ports,
                "uptime": format_uptime(c.started_at),
                "cpu_pct": round(c.cpu_usage_pct, 2),
                "mem_used_bytes": c.mem_used_bytes,
                "mem_limit_bytes": c.mem_limit_bytes,
                "mem_pct": round(c.mem_pct, 2),
                "gpu_mem_used_mib": c.gpu_mem_used_mib,
                "gpu_indices": c.gpu_indices,
                "net_rx_bytes": c.net_rx_bytes,
                "net_tx_bytes": c.net_tx_bytes,
                "blk_read_bytes": c.blk_read_bytes,
                "blk_write_bytes": c.blk_write_bytes,
                "pids": c.pids,
            }
            for c in containers
        ],
    }
    click.echo(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
