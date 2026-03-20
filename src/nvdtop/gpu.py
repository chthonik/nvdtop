"""Query nvidia-smi for GPU and VRAM usage, mapping processes to containers."""

import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GpuProcess:
    pid: int
    gpu_index: int
    gpu_name: str
    used_memory_mib: int
    process_name: str


@dataclass
class GpuInfo:
    index: int
    name: str
    total_memory_mib: int
    used_memory_mib: int
    free_memory_mib: int
    temperature_c: int | None = None
    utilization_pct: int | None = None
    processes: list[GpuProcess] = field(default_factory=list)


def query_nvidia_smi() -> tuple[list[GpuInfo], list[GpuProcess]]:
    """Run nvidia-smi and parse XML output. Returns (gpus, all_processes)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "-q", "-x"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return [], []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [], []

    root = ET.fromstring(result.stdout)
    gpus: list[GpuInfo] = []
    all_processes: list[GpuProcess] = []

    for idx, gpu_elem in enumerate(root.findall("gpu")):
        name = _text(gpu_elem, "product_name", "Unknown GPU")
        fb = gpu_elem.find("fb_memory_usage")
        total = _parse_mib(fb, "total")
        used = _parse_mib(fb, "used")
        free = _parse_mib(fb, "free")

        temp_elem = gpu_elem.find("temperature")
        temp = _parse_int(_text(temp_elem, "gpu_temp", "")) if temp_elem is not None else None

        util_elem = gpu_elem.find("utilization")
        util = _parse_int(_text(util_elem, "gpu_util", "")) if util_elem is not None else None

        gpu = GpuInfo(
            index=idx, name=name,
            total_memory_mib=total, used_memory_mib=used, free_memory_mib=free,
            temperature_c=temp, utilization_pct=util,
        )

        procs_elem = gpu_elem.find("processes")
        if procs_elem is not None:
            for pi in procs_elem.findall("process_info"):
                pid = int(_text(pi, "pid", "0"))
                mem = _parse_mib_text(_text(pi, "used_memory", "0 MiB"))
                pname = _text(pi, "process_name", "")
                gp = GpuProcess(
                    pid=pid, gpu_index=idx, gpu_name=name,
                    used_memory_mib=mem, process_name=pname,
                )
                gpu.processes.append(gp)
                all_processes.append(gp)

        gpus.append(gpu)

    return gpus, all_processes


def pid_to_container_id(pid: int) -> str | None:
    """Resolve a host PID to a Docker container ID via cgroup."""
    cgroup_path = Path(f"/proc/{pid}/cgroup")
    if not cgroup_path.exists():
        return None
    try:
        text = cgroup_path.read_text()
    except (PermissionError, OSError):
        return None

    for line in text.splitlines():
        # cgroup v2: 0::/system.slice/docker-<id>.scope
        # cgroup v1: ...:/.../docker/<id>
        for marker in ("docker-", "docker/"):
            pos = line.find(marker)
            if pos != -1:
                cid = line[pos + len(marker):]
                cid = cid.split(".")[0].split("/")[0]
                if len(cid) >= 12:
                    return cid[:64]
    return None


def map_gpu_to_containers(
    processes: list[GpuProcess],
) -> dict[str, list[GpuProcess]]:
    """Map container IDs to their GPU processes."""
    mapping: dict[str, list[GpuProcess]] = {}
    for proc in processes:
        cid = pid_to_container_id(proc.pid)
        if cid:
            mapping.setdefault(cid, []).append(proc)
    return mapping


def _text(parent, tag, default=""):
    if parent is None:
        return default
    elem = parent.find(tag)
    return elem.text.strip() if elem is not None and elem.text else default


def _parse_mib(parent, tag):
    return _parse_mib_text(_text(parent, tag, "0 MiB"))


def _parse_mib_text(s: str) -> int:
    return _parse_int(s.replace("MiB", "").strip())


def _parse_int(s: str) -> int:
    s = s.replace("%", "").replace("C", "").strip()
    try:
        return int(s)
    except ValueError:
        return 0
