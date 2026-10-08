"""Scheduler / run-queue pressure collection.

Catches a specific failure mode that plain CPU busy% misses: lots of
threads "running" (high load average, high aggregate CPU busy%) but very
little actual work/data getting through, because threads are serialized
on a lock/mutex/spinlock rather than doing independent work. Symptoms:
  - procs_blocked stays > 0 (threads waiting on uninterruptible I/O or a
    kernel lock, not actual CPU work)
  - context-switch rate is very high relative to busy time (threads keep
    getting preempted/re-scheduled instead of running to completion --
    classic lock-contention signature)
  - one core pegged near 100% while the rest sit mostly idle (serialized
    on a single-threaded hot path), which can't be seen from aggregate
    CPU busy% alone

/proc/stat's `ctxt`/`procs_running`/`procs_blocked` lines give the first
two; per-core imbalance is derived in render.py from cpu.py's existing
per_cpu_pct output, so it's not duplicated here.
"""


def read():
    ctxt = procs_running = procs_blocked = None
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("ctxt "):
                ctxt = int(line.split()[1])
            elif line.startswith("procs_running "):
                procs_running = int(line.split()[1])
            elif line.startswith("procs_blocked "):
                procs_blocked = int(line.split()[1])
    return {"ctxt": ctxt, "procs_running": procs_running, "procs_blocked": procs_blocked}


def compute(prev, curr, dt):
    if prev is None or dt <= 0 or curr["ctxt"] is None or prev["ctxt"] is None:
        ctxt_per_s = 0.0
    else:
        ctxt_per_s = max(0, curr["ctxt"] - prev["ctxt"]) / dt
    return {
        "ctxt_per_s": ctxt_per_s,
        "procs_running": curr["procs_running"],
        "procs_blocked": curr["procs_blocked"],
    }
