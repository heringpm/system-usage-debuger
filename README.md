# perfmon

A dependency-free Linux performance monitor focused on **headroom**: for
each resource (CPU, memory, network, disk, PCIe), it tells you how much
capacity is still available vs. how much is being used, so you can see
whether a client or storage server is actually maxed out or still has
room to push harder. Works the same way on storage servers and clients,
so you can run it on both sides of a test and see which one is the
bottleneck.

Pure Python 3 standard library — no `pip install` required, no agents to
deploy. Reads directly from `/proc` and `/sys`.

## What it monitors

| Resource | Source | Metrics |
|---|---|---|
| CPU | `/proc/stat`, `/proc/loadavg`, `/proc/pressure/cpu` | per-core + aggregate busy/idle %, headroom %, load average, PSI stall % |
| Memory | `/proc/meminfo`, `/proc/pressure/memory` | used/available/swap, headroom %, PSI stall % |
| Network | `/proc/net/dev`, `/sys/class/net/*/speed` | per-NIC throughput (Mb/s, pps), errors/drops, **utilization vs negotiated link speed**, headroom % |
| RDMA | `/sys/class/infiniband/*/ports/*/` | per-HCA-port throughput (Mb/s, pps), errors/discards, utilization vs negotiated link rate, headroom %. Needed because RoCE/InfiniBand verbs traffic bypasses the normal netdev stack and never shows up under "Network" |
| Disk | `/proc/diskstats` | per-device utilization %, IOPS, throughput, queue depth, latency, headroom % |
| Scheduler | `/proc/stat` | running/blocked thread counts, context-switch rate; flags a hot single core (high busy% while aggregate CPU is low) -- catches lock contention/serialization that aggregate CPU% hides |
| Top processes | `/proc/<pid>/stat` | top CPU-consuming processes, split into user (`utime`) vs. system/kernel (`stime`) time, as % of one core (same convention as `top`) -- shows *which* process is behind a CPU bottleneck and whether it's doing real computation or fighting the kernel (syscalls, page faults, lock contention) |
| NUMA | `/sys/devices/system/node/node*/numastat` | per-node local vs. remote (cross-socket) memory-access rate; high "remote %" explains high thread/load activity with low actual throughput, since remote NUMA access is slower than local |
| Memory bandwidth (optional, `--mem-bandwidth`) | `perf stat` uncore counters | actual DRAM read/write throughput (MB/s). The one thing with no `/proc`/`/sys` source at all -- requires the external `perf` binary and root/CAP_PERFMON. Supports Intel (`uncore_imc`) and AMD Zen2+/EPYC (`amd_umc`). Off by default; degrades to a visible "unavailable" status line if unsupported |
| PCIe | `/sys/bus/pci/devices/*` | current vs max negotiated link speed/width per device, flags degraded links (e.g. a NIC or NVMe drive running below its rated capability) |

"Headroom %" is 100% when a resource is idle and 0% when it's fully
saturated — low headroom is the thing to watch for.

## Status thresholds

Every headroom metric is turned into `OK` / `WARN` / `CRIT` using
configurable thresholds (`perfmon/thresholds.py`):

| Metric | WARN below | CRIT below |
|---|---|---|
| CPU headroom | 20% | 5% |
| Memory headroom | 15% | 5% |
| Network headroom | 20% | 5% |
| RDMA headroom | 20% | 5% |
| Disk headroom | 20% | 5% |

| Metric (higher is worse) | WARN above | CRIT above |
|---|---|---|
| NUMA remote-access % | 20% | 50% |
| Threads blocked | 1 | 8 |

PSI stall % and swap used % use the opposite direction (higher is
worse): WARN at 10%/10%, CRIT at 25%/50%. A degraded PCIe link always
reports WARN. The overall status shown is the worst of all individual
findings.

## Installation

```bash
cd perf-monitor
pip install -e .          # installs the `perfmon` command
# or just run it in place without installing:
python3 -m perfmon.cli
```

## Usage

### Local monitoring

```bash
perfmon                                   # live curses dashboard, 2s interval
perfmon --mode text                       # plain-text view (good over SSH/logs, no curses)
perfmon --mode json                       # one JSON object per sample to stdout
perfmon --once                            # take a single sample, print it, exit
perfmon --csv run.csv                     # also append every sample to a CSV file
perfmon --duration 60 --csv run.csv --mode text   # 60s unattended capture
perfmon --warn-headroom 30 --crit-headroom 10     # tune thresholds
```

CLI flags:
- `--interval SECONDS` — seconds between samples (default: 2)
- `--duration SECONDS` — stop after N seconds (default: run until Ctrl-C / `q`)
- `--mode {live,text,json}` — `live` = curses dashboard (default, supports `\u2191`/`\u2193`/PgUp/PgDn/Home/End to scroll when a sample has more lines than fit on screen), `text` = plain ANSI text, `json` = one JSON object per line
- `--no-clear` — (`--mode text` only) don't clear the screen between samples; each sample prints below the last so your terminal's normal scrollback shows history instead of being erased every interval
- `--csv PATH` — append every sample to this CSV file (auto-migrates the header if new devices/NICs appear over time)
- `--once` — take a single sample and exit
- `--warn-headroom` / `--crit-headroom` — override the default WARN/CRIT headroom % thresholds for cpu/mem/net/disk
- `--mem-bandwidth` — also show real DRAM read/write bandwidth (MB/s) via `perf stat` uncore counters. Supports Intel and AMD Zen2+/EPYC, needs the `perf` binary plus root or `CAP_PERFMON` (uncore PMUs are system-wide), and adds ~0.2s per sample. Local monitoring only (not supported with `--host`). If unsupported on the machine, shows a visible "unavailable" line instead of failing

### Remote monitoring over SSH

You can point `perfmon` at one or more remote hosts without installing
anything there — it bundles all of its collector logic into a single
self-contained script and pipes it to `ssh <host> python3 -`.
Requirements on the remote side: just `python3` and passwordless
(key-based) SSH access.

```bash
perfmon --host storage01                          # monitor one remote host
perfmon --host storage01 --host client01           # monitor several at once (repeat --host)
perfmon --host 10.0.0.5 --mode text --interval 1    # text mode works remotely too
perfmon --host storage01 --ssh-arg=-i --ssh-arg=/path/to/key   # custom SSH identity file
perfmon --host storage01 --remote-python python3.11  # use a specific remote interpreter
```

Notes:
- `--host` is repeatable; when given, every mode (`live`/`text`/`json`/`--once`) shows a per-host section with its own `OK`/`WARN`/`CRIT` status, plus an overall status across all hosts.
- `--ssh-arg` is repeatable and forwards extra arguments straight to `ssh` (e.g. identity file, port, jump host). If the value itself looks like a flag (starts with `-`), pass it with `=`, e.g. `--ssh-arg=-i --ssh-arg=/path/to/key`, otherwise argparse will misread it.
- A host shows `waiting for data...` (WARN) until its first sample arrives, and reports CRIT with the SSH/connection error if it can't connect or the session dies.
- You can mix local use and remote use freely — just omit `--host` to monitor the machine `perfmon` runs on.

## How it works internally

- `perfmon/collectors/*.py` — one module per resource; each exposes `read()` (grab a raw snapshot from `/proc`/`/sys`) and `compute(prev, curr, dt)` (turn two snapshots + elapsed time into rates/utilization/headroom).
- `perfmon/sampler.py` — `Sampler` orchestrates all collectors locally, keeping previous snapshots and computing deltas each call to `.sample()`.
- `perfmon/thresholds.py` — `evaluate()` turns a sample into OK/WARN/CRIT findings; `evaluate_multi()` does the same across multiple hosts.
- `perfmon/render.py` — shared line-building logic (`build_lines`, `build_multi_lines`) used by both UIs so local and remote/single and multi-host views stay visually consistent.
- `perfmon/text_ui.py` / `perfmon/curses_ui.py` — the two live views; both auto-detect single-host vs. multi-host samplers.
- `perfmon/csvlog.py` — flattens a sample dict into a wide CSV row and appends it, rewriting the header (and backfilling blanks) if a new device/NIC/column shows up mid-run.
- `perfmon/bundler.py` — uses `inspect.getsource()` on the collector modules to generate a single standalone script, so the remote logic can never drift out of sync with the local code.
- `perfmon/remote.py` — `RemoteSampler` runs one SSH session per host, piping in the bundle and reading one JSON sample per line from stdout in a background thread; `MultiHostSampler` fans this out across several hosts and merges their latest samples.
- `perfmon/cli.py` — argument parsing and wiring of the above into the `perfmon` command.

## Requirements

- Linux (reads `/proc` and `/sys`; PSI requires a kernel with `CONFIG_PSI` enabled, common on modern distros)
- Python 3.8+, no external dependencies
- For `--host`: passwordless SSH access and `python3` on the remote machine
