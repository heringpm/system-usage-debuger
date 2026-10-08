"""Renders a sample dict (as produced by Sampler.sample()) as Prometheus
text exposition format, so Prometheus can scrape perfmon and Grafana can
graph it over time. Mirrors the section-by-section structure of render.py
so every number shown in the live/text UI has a matching metric here.

No new dependencies: this is plain string formatting plus the stdlib
http.server (see metrics_server.py) -- Prometheus/Grafana themselves run
as separate services, same as `perf`/`dmidecode` are separate binaries.
"""

def _line(out, name, value, labels=None):
    if value is None:
        return
    full_name = f"perfmon_{name}"
    if labels:
        label_str = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
        out.append(f"{full_name}{{{label_str}}} {value}")
    else:
        out.append(f"{full_name} {value}")


def _escape(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def render(sample):
    """Return a single host's sample as Prometheus text format."""
    out = []

    cpu = sample.get("cpu") or {}
    if cpu:
        _line(out, "cpu_busy_percent", cpu.get("busy_pct"))
        _line(out, "cpu_headroom_percent", cpu.get("headroom_pct"))
        if cpu.get("loadavg"):
            _line(out, "cpu_load1", cpu["loadavg"][0])
            _line(out, "cpu_load5", cpu["loadavg"][1])
            _line(out, "cpu_load15", cpu["loadavg"][2])
        if cpu.get("hot_core_busy_pct") is not None:
            _line(out, "cpu_hot_core_busy_percent", cpu["hot_core_busy_pct"], {"core": cpu.get("hot_core", "")})
        psi = cpu.get("psi") or {}
        if "some" in psi:
            _line(out, "cpu_psi_some_avg10", psi["some"]["avg10"])

    sched = sample.get("sched") or {}
    if sched.get("procs_running") is not None:
        _line(out, "sched_procs_running", sched["procs_running"])
        _line(out, "sched_procs_blocked", sched["procs_blocked"])
        _line(out, "sched_context_switches_per_second", sched["ctxt_per_s"])

    for pid_info in (sample.get("procs") or {}).get("top") or []:
        labels = {"pid": pid_info["pid"], "comm": pid_info["comm"]}
        _line(out, "proc_cpu_user_percent", pid_info["user_pct"], labels)
        _line(out, "proc_cpu_sys_percent", pid_info["sys_pct"], labels)
        _line(out, "proc_cpu_total_percent", pid_info["total_pct"], labels)

    for node, n in (sample.get("numa") or {}).items():
        labels = {"node": node}
        _line(out, "numa_local_per_second", n["local_per_s"], labels)
        _line(out, "numa_remote_per_second", n["other_per_s"], labels)
        _line(out, "numa_remote_percent", n["remote_pct"], labels)
        _line(out, "numa_miss_per_second", n["miss_per_s"], labels)

    membw = sample.get("membw")
    if membw is not None and membw.get("available"):
        _line(out, "membw_read_mbps", membw.get("read_mbps"))
        _line(out, "membw_write_mbps", membw.get("write_mbps"))
        _line(out, "membw_total_mbps", membw.get("total_mbps"))
        _line(out, "membw_peak_mbps", membw.get("peak_mbps"))
        _line(out, "membw_headroom_percent", membw.get("headroom_pct"))

    mem = sample.get("memory") or {}
    if mem:
        _line(out, "memory_used_percent", mem.get("used_pct"))
        _line(out, "memory_headroom_percent", mem.get("headroom_pct"))
        _line(out, "memory_used_mb", mem.get("used_mb"))
        _line(out, "memory_total_mb", mem.get("total_mb"))
        if mem.get("swap_total_mb", 0) > 0:
            _line(out, "memory_swap_used_percent", mem.get("swap_used_pct"))

    for name, net in (sample.get("network") or {}).items():
        labels = {"device": name}
        _line(out, "network_rx_mbps", net["rx_mbps"], labels)
        _line(out, "network_tx_mbps", net["tx_mbps"], labels)
        _line(out, "network_headroom_percent", net["headroom_pct"], labels)
        _line(out, "network_rx_errors_per_second", net["rx_errs_per_s"], labels)
        _line(out, "network_tx_errors_per_second", net["tx_errs_per_s"], labels)

    for name, port in (sample.get("rdma") or {}).items():
        labels = {"device": name}
        _line(out, "rdma_rx_mbps", port["rx_mbps"], labels)
        _line(out, "rdma_tx_mbps", port["tx_mbps"], labels)
        _line(out, "rdma_headroom_percent", port["headroom_pct"], labels)
        _line(out, "rdma_errors_per_second", port["errs_per_s"], labels)

    for name, d in (sample.get("disk") or {}).items():
        labels = {"device": name}
        _line(out, "disk_util_percent", d["util_pct"], labels)
        _line(out, "disk_headroom_percent", d["headroom_pct"], labels)
        _line(out, "disk_read_mbps", d["read_mbps"], labels)
        _line(out, "disk_write_mbps", d["write_mbps"], labels)
        _line(out, "disk_avg_queue_depth", d["avg_queue_depth"], labels)
        _line(out, "disk_avg_latency_ms", d["avg_latency_ms"], labels)

    for name, p in (sample.get("pci") or {}).items():
        labels = {"device": name, "label": p.get("label", "")}
        _line(out, "pci_degraded", 1 if p["degraded"] else 0, labels)

    return "\n".join(out) + "\n" if out else ""


def render_multi(sample_by_host):
    """Same as render(), but for multiple hosts: adds a 'host' label to
    every metric line by re-rendering each host and injecting the label."""
    parts = []
    for host, sample in sorted(sample_by_host.items()):
        if not isinstance(sample, dict):
            continue
        text = render(sample)
        for line in text.splitlines():
            if line.startswith("#") or "{" not in line:
                if line.startswith("#"):
                    parts.append(line)
                    continue
                name, _, rest = line.partition(" ")
                parts.append(f'{name}{{host="{_escape(host)}"}} {rest}')
            else:
                name, _, rest = line.partition("{")
                parts.append(f'{name}{{host="{_escape(host)}",{rest}')
    return "\n".join(parts) + "\n" if parts else ""
