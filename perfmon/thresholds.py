"""Headroom thresholds and OK/WARN/CRIT status evaluation.

"Headroom" is expressed as a percentage of capacity still available
(100% = idle, 0% = saturated). Lower headroom is worse.
"""

OK, WARN, CRIT = "OK", "WARN", "CRIT"

# Default headroom thresholds (percent remaining capacity). Below warn_pct ->
# WARN, below crit_pct -> CRIT. Tune via CLI flags or a config dict.
DEFAULTS = {
    "cpu_headroom_pct": {"warn": 20.0, "crit": 5.0},
    "mem_headroom_pct": {"warn": 15.0, "crit": 5.0},
    "net_headroom_pct": {"warn": 20.0, "crit": 5.0},
    "rdma_headroom_pct": {"warn": 20.0, "crit": 5.0},
    "disk_headroom_pct": {"warn": 20.0, "crit": 5.0},
    # PSI "some avg10" stall percentage: higher is worse (not a headroom value).
    "psi_some_avg10": {"warn": 10.0, "crit": 25.0},
    "swap_used_pct": {"warn": 10.0, "crit": 50.0},
}


def status_from_headroom(headroom_pct, thresholds):
    if headroom_pct is None:
        return OK
    if headroom_pct <= thresholds["crit"]:
        return CRIT
    if headroom_pct <= thresholds["warn"]:
        return WARN
    return OK


def status_from_load(value_pct, thresholds):
    """For metrics where a higher value is worse (e.g. PSI stall %, swap %)."""
    if value_pct is None:
        return OK
    if value_pct >= thresholds["crit"]:
        return CRIT
    if value_pct >= thresholds["warn"]:
        return WARN
    return OK


_RANK = {OK: 0, WARN: 1, CRIT: 2}


def worst(*statuses):
    return max(statuses, key=lambda s: _RANK[s]) if statuses else OK


def evaluate(sample, thresholds=None):
    """Evaluate a full sample dict (as produced by Sampler.sample()) and
    return {"overall": status, "findings": [(label, status, detail), ...]}.
    """
    t = {**DEFAULTS, **(thresholds or {})}
    findings = []

    cpu = sample.get("cpu")
    if cpu:
        s = status_from_headroom(cpu["headroom_pct"], t["cpu_headroom_pct"])
        findings.append(("cpu.headroom", s, f"{cpu['headroom_pct']:.1f}% headroom, busy {cpu['busy_pct']:.1f}%"))
        psi = cpu.get("psi")
        if psi and "some" in psi:
            s2 = status_from_load(psi["some"]["avg10"], t["psi_some_avg10"])
            findings.append(("cpu.psi_some_avg10", s2, f"{psi['some']['avg10']:.1f}% stall (avg10)"))

    mem = sample.get("memory")
    if mem:
        s = status_from_headroom(mem["headroom_pct"], t["mem_headroom_pct"])
        findings.append(("memory.headroom", s, f"{mem['headroom_pct']:.1f}% headroom, used {mem['used_pct']:.1f}%"))
        s2 = status_from_load(mem["swap_used_pct"], t["swap_used_pct"])
        if mem["swap_total_mb"] > 0:
            findings.append(("memory.swap", s2, f"{mem['swap_used_pct']:.1f}% swap used"))
        psi = mem.get("psi")
        if psi and "some" in psi:
            s3 = status_from_load(psi["some"]["avg10"], t["psi_some_avg10"])
            findings.append(("memory.psi_some_avg10", s3, f"{psi['some']['avg10']:.1f}% stall (avg10)"))

    for name, net in (sample.get("network") or {}).items():
        if net["headroom_pct"] is None:
            continue
        s = status_from_headroom(net["headroom_pct"], t["net_headroom_pct"])
        findings.append((f"net.{name}.headroom", s,
                          f"{net['headroom_pct']:.1f}% headroom of {net['link_mbps']} Mb/s link"))

    for name, port in (sample.get("rdma") or {}).items():
        if port["headroom_pct"] is None:
            continue
        s = status_from_headroom(port["headroom_pct"], t["rdma_headroom_pct"])
        findings.append((f"rdma.{name}.headroom", s,
                          f"{port['headroom_pct']:.1f}% headroom of {port['link_mbps']:.0f} Mb/s link"))

    for name, disk in (sample.get("disk") or {}).items():
        s = status_from_headroom(disk["headroom_pct"], t["disk_headroom_pct"])
        findings.append((f"disk.{name}.headroom", s,
                          f"{disk['headroom_pct']:.1f}% headroom, util {disk['util_pct']:.1f}%"))

    for name, pci in (sample.get("pci") or {}).items():
        if pci["degraded"]:
            findings.append((f"pci.{name}", WARN,
                              f"link at {pci['cur_speed']} x{pci['cur_width']}, "
                              f"max is {pci['max_speed']} x{pci['max_width']}"))

    overall = worst(*(s for _, s, _ in findings)) if findings else OK
    return {"overall": overall, "findings": findings}


def evaluate_multi(sample_by_host, thresholds_cfg=None):
    """Evaluate a {host: sample_or_exception_or_None} dict, as produced by
    MultiHostSampler.sample(). Returns {"overall": status, "per_host": {host: evaluation}}.
    """
    per_host = {}
    for host, sample in sample_by_host.items():
        if sample is None:
            per_host[host] = {"overall": WARN, "findings": [("connection", WARN, "waiting for data...")]}
        elif isinstance(sample, Exception):
            per_host[host] = {"overall": CRIT, "findings": [("connection", CRIT, str(sample))]}
        else:
            per_host[host] = evaluate(sample, thresholds_cfg)
    overall = worst(*(e["overall"] for e in per_host.values())) if per_host else OK
    return {"overall": overall, "per_host": per_host}
