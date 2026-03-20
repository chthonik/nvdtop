"""Rich-based terminal display for nvdtop."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box

from .containers import ContainerStats, format_uptime
from .gpu import GpuInfo
from .system import SystemStats


def format_bytes_short(b: int | float) -> str:
    """Compact human-readable byte string (e.g. '4.8G', '229M')."""
    if b < 0:
        b = 0
    for unit in ("B", "K", "M", "G", "T"):
        if abs(b) < 1024:
            if unit == "B":
                return f"{int(b)}{unit}"
            return f"{b:.1f}{unit}" if b < 10 else f"{b:.0f}{unit}"
        b /= 1024
    return f"{b:.0f}P"


def format_mib(mib: int) -> str:
    if mib < 1024:
        return f"{mib}M"
    return f"{mib / 1024:.1f}G"


def fraction_bar(used: float, total: float, width: int = 20) -> Text:
    """Create a colored fraction bar like [=========>          ] 45%."""
    if total <= 0:
        return Text("[" + " " * width + "]  N/A", style="dim")

    pct = min(used / total, 1.0)
    filled = int(pct * width)

    if pct < 0.5:
        color = "green"
    elif pct < 0.8:
        color = "yellow"
    else:
        color = "red"

    bar = Text("[")
    bar.append("=" * filled, style=f"bold {color}")
    if filled < width:
        bar.append(">", style=color)
        bar.append(" " * (width - filled - 1))
    bar.append("]")
    bar.append(f" {pct * 100:5.1f}%", style=f"bold {color}")
    return bar


def _pct_color(pct: float) -> str:
    if pct < 50:
        return "green"
    if pct < 80:
        return "yellow"
    return "red"


def _short_status(status: str, health: str | None) -> str:
    """Compact status string."""
    symbols = {
        "running": "UP",
        "exited": "EXIT",
        "paused": "PAUSE",
        "restarting": "RESTART",
        "created": "NEW",
        "dead": "DEAD",
    }
    s = symbols.get(status, status.upper())
    if health and health != "none":
        h = {"healthy": "+", "unhealthy": "!", "starting": "~"}.get(health, "?")
        s = f"{s}({h})"
    return s


def status_style(status: str, health: str | None) -> str:
    if health == "unhealthy":
        return "bold red"
    if status == "running":
        return "bold green" if health != "starting" else "bold yellow"
    if status == "exited":
        return "dim red"
    if status == "paused":
        return "bold yellow"
    return "dim"


def build_system_panel(sys_stats: SystemStats) -> Panel:
    """Build a summary panel for host system resources."""
    lines = Text()
    # CPU
    lines.append(f"  CPU:  ")
    lines.append_text(fraction_bar(sys_stats.cpu_usage_pct, 100, width=25))
    lines.append(f"  ({sys_stats.cpu_count} cores, load {sys_stats.load_1:.1f}/{sys_stats.load_5:.1f}/{sys_stats.load_15:.1f})")
    lines.append("\n")
    # RAM
    lines.append(f"  RAM:  {format_bytes_short(sys_stats.mem_used_bytes)} / {format_bytes_short(sys_stats.mem_total_bytes)}  ")
    lines.append_text(fraction_bar(sys_stats.mem_used_bytes, sys_stats.mem_total_bytes, width=25))
    lines.append("\n")
    # Swap
    if sys_stats.swap_total_bytes > 0:
        lines.append(f"  Swap: {format_bytes_short(sys_stats.swap_used_bytes)} / {format_bytes_short(sys_stats.swap_total_bytes)}  ")
        lines.append_text(fraction_bar(sys_stats.swap_used_bytes, sys_stats.swap_total_bytes, width=25))
    else:
        lines.append("  Swap: disabled", style="dim")

    return Panel(
        lines,
        title="[bold]System Host[/bold]",
        border_style="bright_magenta",
        expand=True,
    )


def build_gpu_panels(gpus: list[GpuInfo]) -> list[Panel]:
    """Build summary panels for each GPU."""
    panels = []
    for gpu in gpus:
        lines = Text()
        lines.append(f"  VRAM: {format_mib(gpu.used_memory_mib)} / {format_mib(gpu.total_memory_mib)}  ")
        lines.append_text(fraction_bar(gpu.used_memory_mib, gpu.total_memory_mib, width=25))
        lines.append("\n")
        if gpu.utilization_pct is not None:
            lines.append(f"  Util: ")
            lines.append_text(fraction_bar(gpu.utilization_pct, 100, width=25))
            lines.append("\n")
        if gpu.temperature_c is not None:
            temp = gpu.temperature_c
            tc = "green" if temp < 60 else "yellow" if temp < 80 else "red"
            lines.append(f"  Temp: ")
            lines.append(f"{temp}°C", style=f"bold {tc}")
            lines.append("\n")
        lines.append(f"  Processes: {len(gpu.processes)}")

        panels.append(Panel(
            lines,
            title=f"[bold]GPU {gpu.index}: {gpu.name}[/bold]",
            border_style="bright_blue",
            expand=True,
        ))
    return panels


def _build_row(
    c: ContainerStats,
    gpus: list[GpuInfo],
    total_gpu_mem: int,
    show_all_columns: bool,
) -> list:
    """Build a single table row for a container."""
    # Status cell
    st_text = _short_status(c.status, c.health)
    status_cell = Text(st_text, style=status_style(c.status, c.health))

    # Uptime cell
    uptime_text = Text(format_uptime(c.started_at), style="dim" if c.status != "running" else "")

    # Restarts cell
    if c.restart_count > 0:
        restart_text = Text(str(c.restart_count), style="bold red" if c.restart_count >= 3 else "yellow")
    else:
        restart_text = Text("0", style="dim")

    # CPU cell
    if c.status == "running":
        cpu_val = f"{c.cpu_usage_pct:.1f}%"
        color = _pct_color(c.cpu_usage_pct)
        cpu_text = Text(cpu_val, style=f"bold {color}" if c.cpu_usage_pct > 50 else "")
    else:
        cpu_text = Text("-", style="dim")

    # Memory cells
    if c.status == "running" and c.mem_limit_bytes > 0:
        mem_frac = f"{format_bytes_short(c.mem_used_bytes)}/{format_bytes_short(c.mem_limit_bytes)}"
        mem_frac_text = Text(mem_frac)
        pct = c.mem_pct
        color = _pct_color(pct)
        mem_pct_text = Text(f"{pct:.1f}%", style=f"bold {color}")
    else:
        mem_frac_text = Text("-", style="dim")
        mem_pct_text = Text("-", style="dim")

    row: list = [c.name]
    if show_all_columns:
        row.append(c.image)
    row.extend([status_cell, uptime_text, restart_text, cpu_text, mem_frac_text, mem_pct_text])

    # GPU/VRAM cells
    if gpus:
        if c.gpu_mem_used_mib > 0 and total_gpu_mem > 0:
            vram_frac = f"{format_mib(c.gpu_mem_used_mib)}/{format_mib(total_gpu_mem)}"
            vram_pct = c.gpu_mem_used_mib / total_gpu_mem * 100
            color = _pct_color(vram_pct)
            row.append(Text(vram_frac))
            row.append(Text(f"{vram_pct:.1f}%", style=f"bold {color}"))
            # GPU index
            if c.gpu_indices:
                idx_str = ",".join(str(i) for i in sorted(set(c.gpu_indices)))
                row.append(Text(idx_str))
            else:
                row.append(Text("-", style="dim"))
        else:
            row.append(Text("-", style="dim"))
            row.append(Text("-", style="dim"))
            row.append(Text("-", style="dim"))

    if show_all_columns:
        if c.status == "running":
            row.append(f"{format_bytes_short(c.net_rx_bytes)}/{format_bytes_short(c.net_tx_bytes)}")
            row.append(f"{format_bytes_short(c.blk_read_bytes)}/{format_bytes_short(c.blk_write_bytes)}")
            row.append(str(c.pids))
        else:
            row.extend([Text("-", style="dim")] * 3)

    return row


def _sort_containers(containers: list[ContainerStats], sort_by: str) -> list[ContainerStats]:
    """Sort containers by the given field."""
    if sort_by == "gpu":
        return sorted(containers, key=lambda c: c.gpu_mem_used_mib, reverse=True)
    if sort_by == "cpu":
        return sorted(containers, key=lambda c: c.cpu_usage_pct, reverse=True)
    if sort_by == "mem":
        return sorted(containers, key=lambda c: c.mem_used_bytes, reverse=True)
    if sort_by == "name":
        return sorted(containers, key=lambda c: c.name)
    return containers


def build_container_table(
    containers: list[ContainerStats],
    gpus: list[GpuInfo],
    sort_by: str = "gpu",
    show_all_columns: bool = False,
) -> Table:
    """Build the main container resource table."""
    total_gpu_mem = sum(g.total_memory_mib for g in gpus) if gpus else 0

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        title="[bold]Docker Container Resources[/bold]",
        title_style="bold white",
        expand=True,
        padding=(0, 1),
    )

    table.add_column("Container", style="bold", no_wrap=True, ratio=3)
    if show_all_columns:
        table.add_column("Image", no_wrap=True, style="dim", ratio=3)
    table.add_column("Status", no_wrap=True, justify="center", ratio=1)
    table.add_column("Uptime", no_wrap=True, justify="right", ratio=1)
    table.add_column("R", no_wrap=True, justify="right", ratio=0)  # Restart count
    table.add_column("CPU", justify="right", no_wrap=True, ratio=1)
    table.add_column("Mem Used/Lim", justify="right", no_wrap=True, ratio=2)
    table.add_column("Mem%", justify="right", no_wrap=True, ratio=1)
    if gpus:
        table.add_column("VRAM Used/Tot", justify="right", no_wrap=True, ratio=2)
        table.add_column("VRAM%", justify="right", no_wrap=True, ratio=1)
        table.add_column("GPU#", justify="right", no_wrap=True, ratio=0)
    if show_all_columns:
        table.add_column("Net I/O", justify="right", no_wrap=True, ratio=2)
        table.add_column("Blk I/O", justify="right", no_wrap=True, ratio=2)
        table.add_column("PIDs", justify="right", ratio=1)

    # Group by compose project
    projects: dict[str | None, list[ContainerStats]] = {}
    for c in containers:
        projects.setdefault(c.compose_project, []).append(c)

    # Sort project groups: named projects first (alphabetically), then standalone (None)
    sorted_keys = sorted(
        (k for k in projects if k is not None),
    ) + ([None] if None in projects else [])

    first_group = True
    for project in sorted_keys:
        group = _sort_containers(projects[project], sort_by)

        if not first_group:
            # Add a visual separator between groups
            table.add_row()

        if project is not None:
            # Add project header row
            table.add_row(
                Text(f"[{project}]", style="bold magenta italic"),
                *[""] * (len(table.columns) - 1),
            )

        for c in group:
            row = _build_row(c, gpus, total_gpu_mem, show_all_columns)
            table.add_row(*row)

        first_group = False

    return table


def render(
    console: Console,
    containers: list[ContainerStats],
    gpus: list[GpuInfo],
    sys_stats: SystemStats | None = None,
    sort_by: str = "gpu",
    show_all: bool = False,
) -> None:
    """Render the full display to the console."""
    console.clear()

    # System host panel
    if sys_stats:
        console.print(build_system_panel(sys_stats))
        console.print()

    # GPU panels
    if gpus:
        gpu_panels = build_gpu_panels(gpus)
        console.print(Columns(gpu_panels, equal=True, expand=True))
        console.print()

    # Container table
    table = build_container_table(containers, gpus, sort_by=sort_by, show_all_columns=show_all)
    console.print(table)

    # Summary line
    running = sum(1 for c in containers if c.status == "running")
    total = len(containers)
    console.print(
        f"\n [dim]{running} running / {total} total containers[/dim]",
        highlight=False,
    )
