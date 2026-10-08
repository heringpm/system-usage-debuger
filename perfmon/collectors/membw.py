"""Optional DRAM memory-bandwidth collector, via `perf stat` uncore counters.

This is the one thing genuinely invisible to /proc and /sys: how much
data is actually moving in/out of the memory controllers (GB/s). A
system can show "high thread load, low network/disk throughput" because
every core is stuck waiting on DRAM (or a remote NUMA node) rather than
doing useful work -- but there is no /proc file for memory-channel
bandwidth. The kernel doesn't track it; only the CPU's uncore
performance-monitoring unit (PMU) does.

This collector shells out to `perf stat` for a short sampling window and
reads DRAM CAS (column address strobe) command counts, summed across all
memory controllers by perf's generic non-indexed alias. Each CAS count is
one 64-byte cache-line transfer, so bytes = count * 64. Two CPU vendors
are supported, with different event names:
  - Intel: `uncore_imc/cas_count_read/` and `uncore_imc/cas_count_write/`
  - AMD (Zen2+/EPYC): `amd_umc/umc_cas_cmd.rd/` and `amd_umc/umc_cas_cmd.wr/`

Opt-in only (NOT part of the default collector set) because it:
  - requires the external `perf` binary (breaks the "pure stdlib" design
    for everyone who doesn't explicitly ask for it)
  - usually requires root or CAP_PERFMON (uncore PMUs are system-wide)
  - blocks for a short window (default 0.2s) per sample to let perf
    count, adding minor latency to each sampling cycle

Degrades gracefully (returns an "unavailable" reason, never raises) if
`perf` is missing, the uncore events aren't found, or permission is
denied -- so enabling this flag on an unsupported box is a visible but
harmless status line rather than a crash.
"""
import csv
import io
import shutil
import subprocess

CACHE_LINE_BYTES = 64
WINDOW_S = 0.2

_events_cache = None  # None = not checked yet; "" = checked, unsupported; else "ev1,ev2"


def _discover_events():
    """Find the cas_count_read/write event names via `perf list`, if present."""
    global _events_cache
    if _events_cache is not None:
        return _events_cache
    if not shutil.which("perf"):
        _events_cache = ""
        return _events_cache
    try:
        out = subprocess.run(["perf", "list"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        _events_cache = ""
        return _events_cache
    read_ev = write_ev = None
    amd_read_ev = amd_write_ev = None
    for line in out.splitlines():
        name = line.strip().split()[0] if line.strip() else ""
        if name.endswith("cas_count_read/"):
            read_ev = name
        elif name.endswith("cas_count_write/"):
            write_ev = name
        elif name.endswith("umc_cas_cmd.rd/"):
            amd_read_ev = name
        elif name.endswith("umc_cas_cmd.wr/"):
            amd_write_ev = name
    if read_ev and write_ev:
        _events_cache = f"{read_ev},{write_ev}"
    elif amd_read_ev and amd_write_ev:
        _events_cache = f"{amd_read_ev},{amd_write_ev}"
    else:
        _events_cache = ""
    return _events_cache


def read():
    events = _discover_events()
    if not events:
        return {"available": False,
                "reason": "perf not installed or no supported DRAM bandwidth events found "
                           "(needs uncore_imc on Intel or amd_umc on AMD)"}
    try:
        proc = subprocess.run(
            ["perf", "stat", "-e", events, "-a", "-x,", "sleep", str(WINDOW_S)],
            capture_output=True, text=True, timeout=WINDOW_S + 10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "reason": f"perf stat failed to run: {e}"}

    if "Access to performance monitoring" in proc.stderr or "paranoid" in proc.stderr:
        return {"available": False,
                "reason": "permission denied -- needs root/CAP_PERFMON or a lower "
                           "/proc/sys/kernel/perf_event_paranoid"}

    counts = {}
    for row in csv.reader(io.StringIO(proc.stderr)):
        if len(row) < 3:
            continue
        value, _unit, event = row[0], row[1], row[2]
        try:
            counts[event] = int(value)
        except ValueError:
            continue

    read_ev, write_ev = events.split(",")
    if read_ev not in counts or write_ev not in counts:
        return {"available": False, "reason": "perf ran but did not report the expected uncore counters"}

    return {
        "available": True,
        "read_count": counts[read_ev],
        "write_count": counts[write_ev],
        "window_s": WINDOW_S,
    }


def compute(prev, curr, dt):
    """Each read() already represents one self-contained perf stat window,
    so rates are derived from curr alone (prev/dt are unused but kept for
    interface consistency with the other collectors)."""
    if not curr.get("available"):
        return {"available": False, "reason": curr.get("reason", "unavailable")}

    window_s = curr["window_s"]
    read_bps = curr["read_count"] * CACHE_LINE_BYTES / window_s
    write_bps = curr["write_count"] * CACHE_LINE_BYTES / window_s
    return {
        "available": True,
        "read_mbps": read_bps / 1_000_000,
        "write_mbps": write_bps / 1_000_000,
        "total_mbps": (read_bps + write_bps) / 1_000_000,
    }
