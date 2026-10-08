"""Per-process CPU breakdown, via /proc/<pid>/stat.

Answers "the CPU is pegged -- which process(es), and are they doing real
work (user-space computation) or fighting the kernel (syscalls, page
faults, lock contention, driver/network stack work)?" Aggregate CPU%
(the "CPU" section) can't tell you that; this collector can, with zero
extra dependencies since /proc/<pid>/stat is always present on Linux.

Each process's utime/stime (in clock ticks) is read every sample and
diffed against the previous sample, same pattern as the system-wide CPU
collector. user_pct/sys_pct are expressed as %% of one CPU core (so a
single-threaded process pegging one core shows ~100%%, not 100%%/ncores),
matching the convention `top` uses.
"""
import os

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
TOP_N = 10


def _read_one(pid):
    """Parse /proc/<pid>/stat. The comm field (2nd field) is wrapped in
    parens and may itself contain spaces/parens, so split on the last ')'
    to find the reliable start of the fixed-format fields after it."""
    with open(f"/proc/{pid}/stat", "r") as f:
        line = f.read()
    lparen = line.find("(")
    rparen = line.rfind(")")
    comm = line[lparen + 1:rparen]
    rest = line[rparen + 2:].split()
    # rest[0]=state, [11]=utime, [12]=stime (0-indexed from state).
    utime = int(rest[11])
    stime = int(rest[12])
    return comm, utime, stime


def read():
    procs = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            comm, utime, stime = _read_one(entry)
        except (OSError, ValueError, IndexError):
            continue  # process exited mid-read, or an unreadable/odd entry -- skip it
        procs[entry] = {"comm": comm, "utime": utime, "stime": stime}
    return {"procs": procs}


def compute(prev, curr, dt):
    if not prev or dt <= 0:
        return {"top": []}

    prev_procs = prev["procs"]
    rows = []
    for pid, c in curr["procs"].items():
        p = prev_procs.get(pid)
        if p is None:
            continue  # new process since last sample -- no rate to compute yet
        d_utime = c["utime"] - p["utime"]
        d_stime = c["stime"] - p["stime"]
        if d_utime < 0 or d_stime < 0:
            continue  # pid reused by a different process between samples
        user_pct = 100.0 * (d_utime / CLK_TCK) / dt
        sys_pct = 100.0 * (d_stime / CLK_TCK) / dt
        total_pct = user_pct + sys_pct
        if total_pct <= 0.1:
            continue
        rows.append({
            "pid": pid,
            "comm": c["comm"],
            "user_pct": user_pct,
            "sys_pct": sys_pct,
            "total_pct": total_pct,
        })

    rows.sort(key=lambda r: r["total_pct"], reverse=True)
    return {"top": rows[:TOP_N]}
