"""Memory usage and pressure (PSI) collection."""
from .psi import read_psi


def read():
    meminfo = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            rest = rest.strip().split()
            if not rest:
                continue
            # Values are in kB.
            meminfo[key] = int(rest[0])

    psi = read_psi("/proc/pressure/memory")
    return {"meminfo": meminfo, "psi": psi}


def compute(prev, curr, dt):
    m = curr["meminfo"]
    total_kb = m.get("MemTotal", 0)
    free_kb = m.get("MemFree", 0)
    available_kb = m.get("MemAvailable", free_kb)
    buffers_kb = m.get("Buffers", 0)
    cached_kb = m.get("Cached", 0)
    swap_total_kb = m.get("SwapTotal", 0)
    swap_free_kb = m.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)

    used_kb = max(0, total_kb - available_kb)
    used_pct = (100.0 * used_kb / total_kb) if total_kb else 0.0
    headroom_pct = max(0.0, 100.0 - used_pct)
    swap_used_pct = (100.0 * swap_used_kb / swap_total_kb) if swap_total_kb else 0.0

    return {
        "total_mb": total_kb / 1024.0,
        "used_mb": used_kb / 1024.0,
        "available_mb": available_kb / 1024.0,
        "buffers_mb": buffers_kb / 1024.0,
        "cached_mb": cached_kb / 1024.0,
        "swap_total_mb": swap_total_kb / 1024.0,
        "swap_used_mb": swap_used_kb / 1024.0,
        "used_pct": used_pct,
        "headroom_pct": headroom_pct,
        "swap_used_pct": swap_used_pct,
        "psi": curr["psi"],
    }
