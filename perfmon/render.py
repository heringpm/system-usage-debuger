"""Builds the list of (status, text) lines shown by both the text and
curses front ends, so the two UIs stay in sync.
"""
from . import thresholds as th

# Pseudo-status used for section header lines (CPU / MEMORY / NETWORK / ...).
# Not a real OK/WARN/CRIT severity -- the UIs render it as a plain banner
# rather than a color-coded status line.
HEAD = "HEAD"


def build_lines(sample, evaluation):
    lines = []
    status_by_label = {label: s for label, s, _ in evaluation["findings"]}

    def header(title):
        lines.append((HEAD, title))

    cpu = sample.get("cpu") or {}
    if cpu:
        header("CPU")
        lines.append((status_by_label.get("cpu.headroom", th.OK),
                      f"  busy {cpu['busy_pct']:5.1f}%  headroom {cpu['headroom_pct']:5.1f}%  "
                      f"load {cpu['loadavg'][0]:.2f}/{cpu['loadavg'][1]:.2f}/{cpu['loadavg'][2]:.2f} "
                      f"({cpu['cpu_count']} cores)"))
        psi = cpu.get("psi")
        if psi and "some" in psi:
            lines.append((status_by_label.get("cpu.psi_some_avg10", th.OK),
                          f"  PSI some avg10={psi['some']['avg10']:.1f}% avg60={psi['some']['avg60']:.1f}%"))

    mem = sample.get("memory") or {}
    if mem:
        header("MEMORY")
        lines.append((status_by_label.get("memory.headroom", th.OK),
                      f"  used {mem['used_pct']:5.1f}%  headroom {mem['headroom_pct']:5.1f}%  "
                      f"{mem['used_mb']:.0f}/{mem['total_mb']:.0f} MB"))
        if mem["swap_total_mb"] > 0:
            lines.append((status_by_label.get("memory.swap", th.OK),
                          f"  swap {mem['swap_used_mb']:.0f}/{mem['swap_total_mb']:.0f} MB "
                          f"({mem['swap_used_pct']:.1f}%)"))
        psi = mem.get("psi")
        if psi and "some" in psi:
            lines.append((status_by_label.get("memory.psi_some_avg10", th.OK),
                          f"  PSI some avg10={psi['some']['avg10']:.1f}% avg60={psi['some']['avg60']:.1f}%"))

    net_devices = sample.get("network") or {}
    if net_devices:
        header("NETWORK")
        for name, net in sorted(net_devices.items()):
            label = f"net.{name}.headroom"
            link = f"{net['link_mbps']} Mb/s" if net["link_mbps"] else "unknown link speed"
            headroom = f"{net['headroom_pct']:5.1f}%" if net["headroom_pct"] is not None else "  n/a"
            lines.append((status_by_label.get(label, th.OK),
                          f"  {name:<10} rx {net['rx_mbps']:8.1f} Mb/s  tx {net['tx_mbps']:8.1f} Mb/s  "
                          f"headroom {headroom}  ({link})"))
            if net["rx_errs_per_s"] or net["tx_errs_per_s"]:
                lines.append((th.WARN,
                              f"    errors/drops: rx {net['rx_errs_per_s']:.1f}/s  tx {net['tx_errs_per_s']:.1f}/s"))

    rdma_ports = sample.get("rdma") or {}
    if rdma_ports:
        header("RDMA")
        for name, port in sorted(rdma_ports.items()):
            label = f"rdma.{name}.headroom"
            link = f"{port['link_mbps']:.0f} Mb/s" if port["link_mbps"] else "unknown link speed"
            headroom = f"{port['headroom_pct']:5.1f}%" if port["headroom_pct"] is not None else "  n/a"
            lines.append((status_by_label.get(label, th.OK),
                          f"  {name:<14} rx {port['rx_mbps']:8.1f} Mb/s  tx {port['tx_mbps']:8.1f} Mb/s  "
                          f"headroom {headroom}  ({link})"))
            if port["errs_per_s"]:
                lines.append((th.WARN, f"    errors/discards: {port['errs_per_s']:.1f}/s"))

    disk_devices = sample.get("disk") or {}
    if disk_devices:
        header("DISK")
        for name, d in sorted(disk_devices.items()):
            label = f"disk.{name}.headroom"
            lines.append((status_by_label.get(label, th.OK),
                          f"  {name:<9} util {d['util_pct']:5.1f}%  headroom {d['headroom_pct']:5.1f}%  "
                          f"r {d['read_mbps']:6.1f} MB/s w {d['write_mbps']:6.1f} MB/s  "
                          f"qd {d['avg_queue_depth']:.2f}  lat {d['avg_latency_ms']:.2f}ms"))

    pci_devices = sample.get("pci") or {}
    if pci_devices:
        header("PCIe")
        for name, p in sorted(pci_devices.items()):
            status = th.WARN if p["degraded"] else th.OK
            lines.append((status, f"  {p['label']}"))
            flag = "  <-- degraded link" if p["degraded"] else ""
            lines.append((status,
                          f"    {p['cur_speed']} x{p['cur_width']} "
                          f"(max {p['max_speed']} x{p['max_width']}){flag}"))

    return lines


def build_multi_lines(sample_by_host, evaluation_multi):
    """Same as build_lines, but for multiple hosts: emits a '=== host ===' header
    line per host followed by that host's normal metric lines.
    """
    lines = []
    for i, host in enumerate(sorted(sample_by_host)):
        if i > 0:
            lines.append((HEAD, ""))
        sample = sample_by_host[host]
        host_eval = evaluation_multi["per_host"][host]
        banner = f" {host}  [{host_eval['overall']}] "
        lines.append((host_eval["overall"], banner.center(60, "=")))
        if isinstance(sample, dict):
            lines.extend(build_lines(sample, host_eval))
        else:
            label, status, detail = host_eval["findings"][0]
            lines.append((status, f"  {detail}"))
    return lines
