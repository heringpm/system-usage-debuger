"""PCIe link speed/width collection for storage and network controllers.

Linux doesn't expose a generic PCIe bandwidth-utilization counter, so this
reports link *capability* (current vs max negotiated speed/width) which is
still useful: a device running below its max link state is leaving PCIe
bandwidth on the table (power-saving link state, bad slot, riser issue, etc).
"""
import os
import re
import shlex
import subprocess

# Per-lane usable throughput in MB/s for each PCIe generation's GT/s rating,
# after line-encoding overhead (8b/10b for Gen1-2, 128b/130b for Gen3+).
PER_LANE_MBPS = {
    "2.5": 250.0,    # Gen1
    "5.0": 500.0,    # Gen2
    "8.0": 984.6,    # Gen3
    "16.0": 1969.0,  # Gen4
    "32.0": 3938.0,  # Gen5
}

# PCI base class codes of interest: mass storage (01), network (02).
RELEVANT_CLASS_PREFIXES = ("0x01", "0x02")

GT_RE = re.compile(r"([\d.]+)\s*GT/s")
WIDTH_RE = re.compile(r"(\d+)")

# A few common vendor IDs, used as a last-resort label if neither the sysfs
# "label" attribute nor the `lspci` utility is available on this host.
_KNOWN_VENDORS = {
    "0x8086": "Intel", "0x10de": "NVIDIA", "0x1022": "AMD", "0x15b3": "Mellanox",
    "0x1000": "Broadcom/LSI", "0x14e4": "Broadcom", "0x144d": "Samsung",
    "0x1b4b": "Marvell", "0x1cc1": "ADATA", "0x1344": "Micron", "0x1c5c": "SK hynix",
}

_label_cache = {}


def _read_attr(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _lspci_label(addr):
    """Best-effort human-readable device name via `lspci`, e.g.
    'Intel Ethernet Controller XL710 for 40GbE QSFP+'. Cached per address
    since it doesn't change while the process is running."""
    if addr in _label_cache:
        return _label_cache[addr]
    label = None
    try:
        out = subprocess.run(
            ["lspci", "-mm", "-s", addr], capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if out:
            fields = shlex.split(out)
            # lspci -mm fields: slot, class, vendor, device, [-rrev], [svendor, sdevice]
            if len(fields) >= 4:
                vendor, device = fields[2], fields[3]
                label = f"{vendor} {device}"
    except (OSError, subprocess.SubprocessError):
        pass

    if not label:
        vendor_id = _read_attr(f"/sys/bus/pci/devices/{addr}/vendor")
        device_id = _read_attr(f"/sys/bus/pci/devices/{addr}/device")
        if vendor_id:
            vendor_name = _KNOWN_VENDORS.get(vendor_id.lower(), vendor_id)
            label = f"{vendor_name} {device_id or ''}".strip()

    _label_cache[addr] = label
    return label


def read():
    devices = {}
    root = "/sys/bus/pci/devices"
    if not os.path.isdir(root):
        return {"devices": devices}
    for dev in os.listdir(root):
        base = os.path.join(root, dev)
        cls = _read_attr(os.path.join(base, "class"))
        if not cls or not cls.startswith(RELEVANT_CLASS_PREFIXES):
            continue
        cur_speed = _read_attr(os.path.join(base, "current_link_speed"))
        max_speed = _read_attr(os.path.join(base, "max_link_speed"))
        cur_width = _read_attr(os.path.join(base, "current_link_width"))
        max_width = _read_attr(os.path.join(base, "max_link_width"))
        label = _read_attr(os.path.join(base, "label")) or _lspci_label(dev) or dev
        devices[dev] = {
            "label": label, "class": cls,
            "cur_speed": cur_speed, "max_speed": max_speed,
            "cur_width": cur_width, "max_width": max_width,
        }
    return {"devices": devices}


def _gt(speed_str):
    if not speed_str:
        return None
    m = GT_RE.search(speed_str)
    return m.group(1) if m else None


def _width(width_str):
    if not width_str:
        return None
    m = WIDTH_RE.search(width_str)
    return int(m.group(1)) if m else None


def compute(prev, curr, dt):
    result = {}
    for dev, d in curr["devices"].items():
        cur_gt, max_gt = _gt(d["cur_speed"]), _gt(d["max_speed"])
        cur_w, max_w = _width(d["cur_width"]), _width(d["max_width"])
        if cur_gt is None and max_gt is None:
            continue  # no usable link info (e.g. virtual/bridge function)

        cur_mbps = PER_LANE_MBPS.get(cur_gt, 0.0) * cur_w if cur_gt and cur_w else None
        max_mbps = PER_LANE_MBPS.get(max_gt, 0.0) * max_w if max_gt and max_w else None
        degraded = bool(cur_mbps and max_mbps and cur_mbps < max_mbps - 1e-6)

        result[dev] = {
            "label": d["label"],
            "cur_speed": d["cur_speed"], "max_speed": d["max_speed"],
            "cur_width": cur_w, "max_width": max_w,
            "cur_theoretical_mbps": cur_mbps,
            "max_theoretical_mbps": max_mbps,
            "degraded": degraded,
        }
    return result
