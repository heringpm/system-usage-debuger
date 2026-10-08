"""Block device (disk/NVMe) I/O utilization, throughput, latency collection."""
import os

SECTOR_BYTES = 512


def _whole_disks():
    try:
        return set(os.listdir("/sys/block"))
    except OSError:
        return set()


def read():
    whole_disks = _whole_disks()
    devices = {}
    with open("/proc/diskstats") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if whole_disks and name not in whole_disks:
                continue  # skip partitions, keep whole devices only
            if name.startswith(("loop", "ram", "sr")):
                continue
            devices[name] = {
                "reads_completed": int(parts[3]),
                "sectors_read": int(parts[5]),
                "ms_reading": int(parts[6]),
                "writes_completed": int(parts[7]),
                "sectors_written": int(parts[9]),
                "ms_writing": int(parts[10]),
                "ios_in_progress": int(parts[11]),
                "ms_io": int(parts[12]),          # io_ticks: time device had >=1 IO in flight
                "ms_weighted_io": int(parts[13]),  # sum of queue-length * ms, for avg queue depth
            }
    return {"devices": devices}


def compute(prev, curr, dt):
    result = {}
    for name, c in curr["devices"].items():
        p = prev["devices"].get(name) if prev else None
        if p is None or dt <= 0:
            continue
        d_reads = max(0, c["reads_completed"] - p["reads_completed"])
        d_writes = max(0, c["writes_completed"] - p["writes_completed"])
        d_sect_r = max(0, c["sectors_read"] - p["sectors_read"])
        d_sect_w = max(0, c["sectors_written"] - p["sectors_written"])
        d_ms_io = max(0, c["ms_io"] - p["ms_io"])
        d_ms_weighted = max(0, c["ms_weighted_io"] - p["ms_weighted_io"])
        d_ms_rw = max(0, (c["ms_reading"] + c["ms_writing"]) - (p["ms_reading"] + p["ms_writing"]))
        d_ops = d_reads + d_writes

        util_pct = min(100.0, 100.0 * d_ms_io / (dt * 1000.0))
        avg_queue_depth = d_ms_weighted / (dt * 1000.0)
        avg_latency_ms = (d_ms_rw / d_ops) if d_ops else 0.0

        result[name] = {
            "read_iops": d_reads / dt,
            "write_iops": d_writes / dt,
            "read_mbps": d_sect_r * SECTOR_BYTES / 1_000_000 / dt,
            "write_mbps": d_sect_w * SECTOR_BYTES / 1_000_000 / dt,
            "util_pct": util_pct,
            "headroom_pct": max(0.0, 100.0 - util_pct),
            "avg_queue_depth": avg_queue_depth,
            "avg_latency_ms": avg_latency_ms,
            "ios_in_progress": c["ios_in_progress"],
        }
    return result
