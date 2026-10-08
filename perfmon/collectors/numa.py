"""NUMA memory-locality collection.

On multi-socket (or multi-die) machines, each NUMA node has its own local
DRAM. A thread running on node A that keeps touching memory allocated on
node B pays a cross-node (remote) latency/bandwidth penalty on every
access -- the CPU looks busy and "load" looks high, but actual data
movement throughput stays low, since remote accesses are slower and
contend over the inter-socket link (e.g. UPI/Infinity Fabric) instead of
the local memory channel.

/sys/devices/system/node/node<N>/numastat exposes cumulative counters:
  numa_hit     - allocations that landed on the node the thread asked for
  numa_miss    - allocations that wanted this node but landed elsewhere
                 (i.e. this node absorbed another node's overflow)
  numa_foreign - allocations that wanted another node but landed here
  local_node   - allocations serviced by a CPU on the same node
  other_node   - allocations serviced by a CPU on a *different* node
                 (the strongest signal of remote-memory traffic)
These are allocation-site counters (not a live bandwidth meter), but a
climbing other_node/numa_miss rate while throughput stays flat is a good
indicator that NUMA placement, not raw capacity, is the bottleneck.
"""
import os

NODE_ROOT = "/sys/devices/system/node"
FIELDS = ["numa_hit", "numa_miss", "numa_foreign", "interleave_hit", "local_node", "other_node"]


def _read_numastat(path):
    stats = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.strip().partition(" ")
                if k in FIELDS:
                    try:
                        stats[k] = int(v)
                    except ValueError:
                        pass
    except OSError:
        pass
    return stats


def read():
    nodes = {}
    if not os.path.isdir(NODE_ROOT):
        return {"nodes": nodes}
    for entry in sorted(os.listdir(NODE_ROOT)):
        if not entry.startswith("node") or not entry[4:].isdigit():
            continue
        stats = _read_numastat(os.path.join(NODE_ROOT, entry, "numastat"))
        if stats:
            nodes[entry] = stats
    return {"nodes": nodes}


def compute(prev, curr, dt):
    result = {}
    if dt <= 0 or len(curr["nodes"]) < 2:
        # Single-node (or no NUMA) systems have nothing meaningful to report:
        # there's no "remote" node for traffic to cross to.
        return result
    for node, c in curr["nodes"].items():
        p = prev["nodes"].get(node) if prev else None
        if p is None:
            continue
        local_delta = max(0, c.get("local_node", 0) - p.get("local_node", 0))
        other_delta = max(0, c.get("other_node", 0) - p.get("other_node", 0))
        miss_delta = max(0, c.get("numa_miss", 0) - p.get("numa_miss", 0))
        total = local_delta + other_delta
        remote_pct = (100.0 * other_delta / total) if total > 0 else 0.0
        result[node] = {
            "local_per_s": local_delta / dt,
            "other_per_s": other_delta / dt,
            "miss_per_s": miss_delta / dt,
            "remote_pct": remote_pct,
        }
    return result
