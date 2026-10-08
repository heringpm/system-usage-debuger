"""RDMA / RoCE / InfiniBand port throughput collection.

RDMA traffic (verbs, kernel-bypass RoCE/InfiniBand) does NOT go through the
normal netdev stack, so it never shows up in /proc/net/dev or the `network`
collector -- a card can be saturated over RDMA while its Ethernet counters
stay at zero. The HCA firmware instead exposes physical port counters
directly under /sys/class/infiniband/<device>/ports/<port>/, which account
for all traffic through the port regardless of whether it took the verbs
(RDMA) path or the normal kernel networking path.

Per the InfiniBand spec, port_rcv_data/port_xmit_data are counted in units
of 4 bytes, hence the *4 below.
"""
import os
import re

RATE_RE = re.compile(r"([\d.]+)\s*Gb/sec")


def _read_attr(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path):
    val = _read_attr(path)
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _rate_mbps(rate_str):
    if not rate_str:
        return None
    m = RATE_RE.search(rate_str)
    return float(m.group(1)) * 1000 if m else None


def read():
    root = "/sys/class/infiniband"
    ports = {}
    if not os.path.isdir(root):
        return {"ports": ports}
    for dev in sorted(os.listdir(root)):
        ports_dir = os.path.join(root, dev, "ports")
        if not os.path.isdir(ports_dir):
            continue
        for port in sorted(os.listdir(ports_dir)):
            pbase = os.path.join(ports_dir, port)
            counters = os.path.join(pbase, "counters")
            key = f"{dev}/{port}"
            ports[key] = {
                "state": _read_attr(os.path.join(pbase, "state")),
                "rate": _read_attr(os.path.join(pbase, "rate")),
                "rcv_data": _read_int(os.path.join(counters, "port_rcv_data")),
                "xmit_data": _read_int(os.path.join(counters, "port_xmit_data")),
                "rcv_packets": _read_int(os.path.join(counters, "port_rcv_packets")),
                "xmit_packets": _read_int(os.path.join(counters, "port_xmit_packets")),
                "rcv_errors": _read_int(os.path.join(counters, "port_rcv_errors")),
                "xmit_discards": _read_int(os.path.join(counters, "port_xmit_discards")),
            }
    return {"ports": ports}


def compute(prev, curr, dt):
    result = {}
    for key, c in curr["ports"].items():
        if c["state"] and "ACTIVE" not in c["state"]:
            continue  # down/disabled/armed port
        p = prev["ports"].get(key) if prev else None
        if p is None or dt <= 0 or c["rcv_data"] is None or c["xmit_data"] is None:
            continue
        if p["rcv_data"] is None or p["xmit_data"] is None:
            continue

        rx_bps = max(0, c["rcv_data"] - p["rcv_data"]) * 4 / dt
        tx_bps = max(0, c["xmit_data"] - p["xmit_data"]) * 4 / dt
        rx_pps = (max(0, c["rcv_packets"] - p["rcv_packets"]) / dt
                  if c["rcv_packets"] is not None and p["rcv_packets"] is not None else 0.0)
        tx_pps = (max(0, c["xmit_packets"] - p["xmit_packets"]) / dt
                  if c["xmit_packets"] is not None and p["xmit_packets"] is not None else 0.0)

        errs = 0
        if c["rcv_errors"] is not None and p["rcv_errors"] is not None:
            errs += max(0, c["rcv_errors"] - p["rcv_errors"])
        if c["xmit_discards"] is not None and p["xmit_discards"] is not None:
            errs += max(0, c["xmit_discards"] - p["xmit_discards"])

        link_mbps = _rate_mbps(c["rate"])
        if link_mbps:
            capacity_bps = link_mbps * 1_000_000 / 8
            rx_util_pct = 100.0 * rx_bps / capacity_bps
            tx_util_pct = 100.0 * tx_bps / capacity_bps
            util_pct = max(rx_util_pct, tx_util_pct)
            headroom_pct = 100.0 - util_pct
        else:
            rx_util_pct = tx_util_pct = util_pct = headroom_pct = None

        result[key] = {
            "rx_mbps": rx_bps * 8 / 1_000_000,
            "tx_mbps": tx_bps * 8 / 1_000_000,
            "rx_pps": rx_pps,
            "tx_pps": tx_pps,
            "errs_per_s": errs / dt,
            "link_mbps": link_mbps,
            "rate_label": c["rate"],
            "rx_util_pct": rx_util_pct,
            "tx_util_pct": tx_util_pct,
            "util_pct": util_pct,
            "headroom_pct": headroom_pct,
        }
    return result
