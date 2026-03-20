# nvdtop

A terminal-based resource monitor that maps GPU VRAM, CPU, and memory usage to individual Docker containers. Think `htop` meets `nvidia-smi`, but focused on showing exactly how much each container is consuming.

## Features

- **Per-container VRAM tracking** — maps `nvidia-smi` GPU processes to Docker containers via cgroup PID inspection
- **GPU summary panel** — VRAM usage bar, GPU utilization, temperature with color coding
- **System host panel** — CPU usage, RAM, swap, load average with visual bars
- **CPU & memory per container** — shown as fractions and percentages (e.g. `545M/15G 3.5%`)
- **Status filtering** — filter by `running`, `exited`, `unhealthy`, `paused`, etc.
- **Name filtering** — filter containers by name substring
- **Live mode** — auto-refreshing display with configurable interval
- **Color-coded thresholds** — green (<50%), yellow (50-80%), red (>80%)
- **JSON output** — for scripting, piping, and integration with other tools
- **Fast** — parallel stats fetching across all containers (~2-3s for 20+ containers)

## Install

```bash
git clone https://github.com/chthonik/nvdtop.git
cd nvdtop
pip install .
```

> **Tip:** For development, use `pip install -e .` (editable mode). This requires pip 21.3+ — run `pip install --upgrade pip` first if you get a `build_editable` error.

### Requirements

- Python 3.9+
- Docker (running, with access permissions)
- NVIDIA GPU drivers with `nvidia-smi` (optional — works without GPU, just skips VRAM tracking)

## Usage

```bash
# Show all containers with GPU and system info
nvdtop

# Live refresh every 2 seconds
nvdtop -w 2

# Only running containers
nvdtop -s running

# Only exited and unhealthy containers
nvdtop -s exited

# Filter by name
nvdtop -n supabase

# Sort by CPU usage (default sort is by GPU VRAM)
nvdtop --sort cpu

# Sort by memory
nvdtop --sort mem

# Show all columns (adds network I/O, block I/O, PIDs)
nvdtop -a

# Skip GPU queries (faster if no NVIDIA GPU)
nvdtop --no-gpu

# JSON output for scripting
nvdtop --json

# Combine filters
nvdtop -s running -n openclaw --sort gpu -w 5
```

## Options

| Flag | Description |
|---|---|
| `-s, --status` | Filter by status: `running`, `exited`, `paused`, `restarting`, `created`, `dead` (comma-separated) |
| `-n, --name` | Filter by container name (substring match) |
| `--sort` | Sort by `gpu`, `cpu`, `mem`, or `name` (default: `gpu`) |
| `-a, --all-columns` | Show network I/O, block I/O, and PIDs |
| `-w, --watch` | Live refresh interval in seconds (0 = one-shot) |
| `--no-gpu` | Skip nvidia-smi queries |
| `--json` | Output as JSON |

## Status Indicators

| Symbol | Meaning |
|---|---|
| `UP` | Running |
| `UP(+)` | Running, healthy |
| `UP(!)` | Running, unhealthy |
| `UP(~)` | Running, health check starting |
| `EXIT` | Exited |
| `EXIT(!)` | Exited, unhealthy |
| `PAUSE` | Paused |
| `RESTART` | Restarting |

## How GPU Mapping Works

nvdtop queries `nvidia-smi` for all GPU processes and their PIDs, then reads `/proc/<pid>/cgroup` on the host to resolve each PID back to a Docker container ID. This means:

- It must run on the **host**, not inside a container
- It needs read access to `/proc/*/cgroup` (usually available to any user)
- It works with both **cgroup v1** and **cgroup v2**
- Multi-GPU setups are supported — VRAM is aggregated per container across all GPUs

## JSON Output

The `--json` flag outputs structured data for integration with other tools:

```bash
# Pipe to jq to find containers using GPU
nvdtop --json | jq '.containers[] | select(.gpu_mem_used_mib > 0) | {name, gpu_mem_used_mib}'

# Monitor VRAM usage over time
watch -n 5 'nvdtop --json | jq ".gpus[0].used_memory_mib"'
```

Output structure:

```json
{
  "system": {
    "cpu_count": 12,
    "cpu_usage_pct": 34.2,
    "mem_total_bytes": 16924844032,
    "mem_used_bytes": 11847049216,
    "mem_pct": 70.0,
    "swap_total_bytes": 4294967296,
    "swap_used_bytes": 62914560,
    "swap_pct": 1.5,
    "load_1": 0.8,
    "load_5": 0.5,
    "load_15": 1.0
  },
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA GeForce RTX 5060 Ti",
      "total_memory_mib": 16384,
      "used_memory_mib": 9420,
      "free_memory_mib": 6964,
      "temperature_c": 32,
      "utilization_pct": 0
    }
  ],
  "containers": [
    {
      "name": "openclaw-llama",
      "id": "1e8cf336d088",
      "image": "ghcr.io/ggml-org/llama.cpp:server-cuda",
      "status": "running",
      "health": "unhealthy",
      "cpu_pct": 0.03,
      "mem_used_bytes": 2254857728,
      "mem_limit_bytes": 15765958656,
      "mem_pct": 14.3,
      "gpu_mem_used_mib": 9216,
      "net_rx_bytes": 868352,
      "net_tx_bytes": 524288,
      "blk_read_bytes": 4718592000,
      "blk_write_bytes": 1073741824,
      "pids": 17
    }
  ]
}
```

## Compared to Other Tools

| Tool | System Stats | Docker-Aware | Per-Container VRAM | CLI | Live Mode |
|---|---|---|---|---|---|
| **nvdtop** | Yes | Yes | Yes | Yes | Yes |
| glances | Yes | Partial | No | Yes | Yes |
| nvtop/nvitop | GPU only | No | No | Yes | Yes |
| ctop/lazydocker | No | Yes | No | Yes | Yes |
| nvidia-docker-stats | No | Yes | Yes | Yes | No |
| docker stats | No | Yes | No | Yes | Yes |

## Project Structure

```
nvdtop/
├── pyproject.toml
└── src/nvdtop/
    ├── cli.py          # Click CLI, watch loop, JSON output
    ├── containers.py   # Docker SDK stats collection
    ├── display.py      # Rich table/panel rendering
    ├── gpu.py          # nvidia-smi XML parsing, PID-to-container mapping
    └── system.py       # Host CPU/RAM/swap stats from /proc
```

## License

MIT
