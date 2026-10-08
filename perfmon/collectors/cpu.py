"""CPU utilization, load average, and pressure (PSI) collection."""
import os

from .psi import read_psi

# /proc/stat cpu line field order (jiffies).
FIELDS = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]


def read():
    """Return raw CPU counters: per-cpu jiffy tuples, load average, PSI."""
    cpus = {}
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            name = parts[0]
            if name == "cpu":
                continue  # skip aggregate, we derive it from per-cpu
            vals = [int(x) for x in parts[1:1 + len(FIELDS)]]
            cpus[name] = dict(zip(FIELDS, vals))

    loadavg = None
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            loadavg = (float(parts[0]), float(parts[1]), float(parts[2]))
    except OSError:
        pass

    psi = read_psi("/proc/pressure/cpu")

    return {"cpus": cpus, "loadavg": loadavg, "psi": psi, "count": os.cpu_count() or 1}


def compute(prev, curr, dt):
    """Compute per-cpu and aggregate utilization percentages."""
    per_cpu = {}
    totals = {k: 0 for k in FIELDS}
    n = 0
    for name, curr_vals in curr["cpus"].items():
        prev_vals = prev["cpus"].get(name) if prev else None
        if prev_vals is None:
            continue
        delta = {k: curr_vals[k] - prev_vals[k] for k in FIELDS}
        total = sum(delta.values())
        if total <= 0:
            pct = {k: 0.0 for k in FIELDS}
        else:
            pct = {k: 100.0 * v / total for k, v in delta.items()}
        per_cpu[name] = pct
        for k in FIELDS:
            totals[k] += delta[k]
        n += 1

    grand_total = sum(totals.values())
    if grand_total > 0:
        aggregate = {k: 100.0 * v / grand_total for k, v in totals.items()}
        busy_pct = max(0.0, min(100.0, 100.0 - aggregate["idle"] - aggregate.get("iowait", 0.0)))
    else:
        # No prior sample yet (first call) -- nothing to compare against.
        aggregate = {k: 0.0 for k in FIELDS}
        busy_pct = 0.0

    # Hottest individual core: aggregate busy% can look moderate/low while
    # one core is pegged at 100% serializing everything behind it (e.g. a
    # single-threaded hot path or lock holder) -- a pattern invisible in
    # the aggregate number alone.
    hot_core = hot_core_busy_pct = None
    for name, pct in per_cpu.items():
        core_busy = max(0.0, 100.0 - pct["idle"] - pct.get("iowait", 0.0))
        if hot_core_busy_pct is None or core_busy > hot_core_busy_pct:
            hot_core, hot_core_busy_pct = name, core_busy

    return {
        "per_cpu_pct": per_cpu,
        "aggregate_pct": aggregate,
        "busy_pct": busy_pct,
        "headroom_pct": max(0.0, 100.0 - busy_pct),
        "loadavg": curr["loadavg"],
        "load_per_core": (curr["loadavg"][0] / curr["count"]) if curr["loadavg"] else None,
        "cpu_count": curr["count"],
        "psi": curr["psi"],
        "hot_core": hot_core,
        "hot_core_busy_pct": hot_core_busy_pct,
    }
