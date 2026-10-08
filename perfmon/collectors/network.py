"""Network interface throughput, errors, and link-utilization collection."""
import os

RX_FIELDS = ["rx_bytes", "rx_packets", "rx_errs", "rx_drop"]
TX_FIELDS = ["tx_bytes", "tx_packets", "tx_errs", "tx_drop"]


def _link_info(iface):
    base = f"/sys/class/net/{iface}"
    speed_mbps = None
    operstate = "unknown"
    try:
        with open(f"{base}/operstate") as f:
            operstate = f.read().strip()
    except OSError:
        pass
    try:
        with open(f"{base}/speed") as f:
            speed_mbps = int(f.read().strip())
            if speed_mbps < 0:
                speed_mbps = None  # reported for down/virtual interfaces
    except (OSError, ValueError):
        speed_mbps = None
    return speed_mbps, operstate


def read():
    ifaces = {}
    with open("/proc/net/dev") as f:
        lines = f.readlines()[2:]  # skip two header lines
    for line in lines:
        name, _, rest = line.partition(":")
        name = name.strip()
        if name == "lo":
            continue
        parts = rest.split()
        if len(parts) < 16:
            continue
        rx_bytes, rx_packets, rx_errs, rx_drop = (int(parts[i]) for i in (0, 1, 2, 3))
        tx_bytes, tx_packets, tx_errs, tx_drop = (int(parts[i]) for i in (8, 9, 10, 11))
        speed_mbps, operstate = _link_info(name)
        ifaces[name] = {
            "rx_bytes": rx_bytes, "rx_packets": rx_packets, "rx_errs": rx_errs, "rx_drop": rx_drop,
            "tx_bytes": tx_bytes, "tx_packets": tx_packets, "tx_errs": tx_errs, "tx_drop": tx_drop,
            "speed_mbps": speed_mbps, "operstate": operstate,
        }
    return {"ifaces": ifaces}


def compute(prev, curr, dt):
    result = {}
    for name, c in curr["ifaces"].items():
        if c["operstate"] != "up":
            continue
        p = prev["ifaces"].get(name) if prev else None
        if p is None or dt <= 0:
            continue
        rx_bps = max(0, c["rx_bytes"] - p["rx_bytes"]) / dt
        tx_bps = max(0, c["tx_bytes"] - p["tx_bytes"]) / dt
        rx_pps = max(0, c["rx_packets"] - p["rx_packets"]) / dt
        tx_pps = max(0, c["tx_packets"] - p["tx_packets"]) / dt
        rx_errs = max(0, c["rx_errs"] - p["rx_errs"]) + max(0, c["rx_drop"] - p["rx_drop"])
        tx_errs = max(0, c["tx_errs"] - p["tx_errs"]) + max(0, c["tx_drop"] - p["tx_drop"])

        speed_mbps = c["speed_mbps"]
        if speed_mbps:
            capacity_bps = speed_mbps * 1_000_000 / 8
            rx_util_pct = 100.0 * rx_bps / capacity_bps
            tx_util_pct = 100.0 * tx_bps / capacity_bps
        else:
            rx_util_pct = tx_util_pct = None

        util_pct = max(rx_util_pct or 0.0, tx_util_pct or 0.0) if speed_mbps else None
        headroom_pct = (100.0 - util_pct) if util_pct is not None else None

        result[name] = {
            "rx_mbps": rx_bps * 8 / 1_000_000,
            "tx_mbps": tx_bps * 8 / 1_000_000,
            "rx_pps": rx_pps,
            "tx_pps": tx_pps,
            "rx_errs_per_s": rx_errs / dt,
            "tx_errs_per_s": tx_errs / dt,
            "link_mbps": speed_mbps,
            "rx_util_pct": rx_util_pct,
            "tx_util_pct": tx_util_pct,
            "util_pct": util_pct,
            "headroom_pct": headroom_pct,
        }
    return result
