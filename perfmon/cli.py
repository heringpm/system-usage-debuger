"""Command-line entry point for perfmon.

Examples:
  perfmon                         # live curses dashboard, 2s interval
  perfmon --mode text             # plain-text live view (good for SSH/logs)
  perfmon --csv out.csv           # also log every sample to CSV
  perfmon --once                  # take a single sample and print it, then exit
  perfmon --duration 60 --csv run.csv --mode text   # 60s unattended capture
  perfmon --host storage01 --host client01          # monitor two remote hosts over SSH
  perfmon --host 10.0.0.5 --mode text --interval 1  # monitor a remote IP, text mode

Remote hosts (--host) require passwordless (key-based) SSH access and a
python3 interpreter on the remote machine; nothing else needs to be
installed there -- perfmon ships its collector logic to the remote host
over the SSH session itself.
"""
import argparse
import functools
import json
import sys

from .sampler import Sampler, COLLECTORS
from . import thresholds as th
from .csvlog import CsvLogger


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="perfmon", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval", type=float, default=2.0, help="seconds between samples (default: 2)")
    p.add_argument("--duration", type=float, default=None, help="stop after N seconds (default: run until Ctrl-C/q)")
    p.add_argument("--mode", choices=["live", "text", "json"], default="live",
                    help="'live' = curses dashboard, 'text' = plain-text prints, "
                         "'json' = one JSON object per sample to stdout (default: live)")
    p.add_argument("--csv", metavar="PATH", help="also append each sample to this CSV file")
    p.add_argument("--no-clear", action="store_true",
                    help="(--mode text only) don't clear the screen between samples -- each sample "
                         "prints below the last so your terminal's normal scrollback shows history")
    p.add_argument("--once", action="store_true", help="take a single sample, print it, and exit")
    p.add_argument("--warn-headroom", type=float, default=None,
                    help="override default WARN headroom %% threshold for cpu/mem/net/disk")
    p.add_argument("--crit-headroom", type=float, default=None,
                    help="override default CRIT headroom %% threshold for cpu/mem/net/disk")
    p.add_argument("--host", action="append", metavar="[user@]HOST",
                    help="monitor a remote host over SSH instead of the local machine "
                         "(repeatable for multiple hosts); requires passwordless SSH + python3 on the remote")
    p.add_argument("--ssh-arg", action="append", default=[], metavar="ARG",
                    help="extra argument to pass to ssh (repeatable), e.g. --ssh-arg -i --ssh-arg ~/.ssh/id_ed25519")
    p.add_argument("--remote-python", default="python3", help="python interpreter to use on remote hosts (default: python3)")
    p.add_argument("--mem-bandwidth", action="store_true",
                    help="also measure DRAM read/write bandwidth via `perf stat` uncore counters "
                         "(Intel-only, needs the `perf` binary + root/CAP_PERFMON; adds ~0.2s per "
                         "sample; off by default). Not supported for --host (remote) targets.")
    p.add_argument("--prometheus-port", type=int, default=None, metavar="PORT",
                    help="expose the latest sample as Prometheus text format on "
                         "http://0.0.0.0:PORT/metrics, for scraping by Prometheus "
                         "(and graphing in Grafana). Runs alongside whatever --mode is active.")
    return p.parse_args(argv)


def _build_sampler(args):
    if args.host:
        from .remote import MultiHostSampler
        return MultiHostSampler(args.host, args.interval, python=args.remote_python, ssh_args=args.ssh_arg)
    if args.mem_bandwidth:
        from .collectors import membw
        return Sampler(collectors={**COLLECTORS, "membw": membw})
    return Sampler()


def _build_thresholds(args):
    if args.warn_headroom is None and args.crit_headroom is None:
        return None
    overrides = {}
    for key, defaults in th.DEFAULTS.items():
        if key.endswith("_headroom_pct"):
            overrides[key] = {
                "warn": args.warn_headroom if args.warn_headroom is not None else defaults["warn"],
                "crit": args.crit_headroom if args.crit_headroom is not None else defaults["crit"],
            }
    return overrides


def main(argv=None):
    args = parse_args(argv)
    thresholds_cfg = _build_thresholds(args)
    sampler = _build_sampler(args)
    is_multi = getattr(sampler, "is_multi", False)
    csv_logger = CsvLogger(args.csv) if args.csv else None
    metrics_server = None
    if args.prometheus_port is not None:
        from .metrics_server import MetricsServer
        metrics_server = MetricsServer(args.prometheus_port, is_multi=is_multi)
        metrics_server.start()

    try:
        if args.once:
            import time
            if is_multi:
                sampler.wait_for_first(timeout=args.interval + 20)
                sample = sampler.sample()
                evaluation = th.evaluate_multi(sample, thresholds_cfg)
                sample = {h: (s if not isinstance(s, Exception) else str(s)) for h, s in sample.items()}
            else:
                sampler.sample()  # prime counters so rates aren't zero on the only real sample
                time.sleep(min(args.interval, 1.0))
                sample = sampler.sample()
                evaluation = th.evaluate(sample, thresholds_cfg)
            if csv_logger:
                csv_logger.write(sample)
            if metrics_server:
                metrics_server.update(sample)
            print(json.dumps({**sample, "evaluation": evaluation}, indent=2, default=str))
            return 0

        if args.mode == "json":
            _run_json(sampler, args, thresholds_cfg, csv_logger, metrics_server, is_multi)
        elif args.mode == "text":
            from .text_ui import run as run_text
            if args.no_clear:
                run_text = functools.partial(run_text, clear=False)
            _wrap_hooks(run_text, sampler, args, thresholds_cfg, csv_logger, metrics_server, is_multi)
        else:
            from .curses_ui import run as run_curses
            _wrap_hooks(run_curses, sampler, args, thresholds_cfg, csv_logger, metrics_server, is_multi)
        return 0
    finally:
        if hasattr(sampler, "close"):
            sampler.close()
        if metrics_server:
            metrics_server.stop()


def _wrap_hooks(run_fn, sampler, args, thresholds_cfg, csv_logger, metrics_server, is_multi_flag):
    if csv_logger is None and metrics_server is None:
        run_fn(sampler, args.interval, thresholds_cfg, duration=args.duration)
        return

    class _HookedSampler:
        is_multi = is_multi_flag

        def sample(self):
            s = sampler.sample()
            if csv_logger:
                csv_logger.write(s)
            if metrics_server:
                metrics_server.update(s)
            return s

    run_fn(_HookedSampler(), args.interval, thresholds_cfg, duration=args.duration)


def _run_json(sampler, args, thresholds_cfg, csv_logger, metrics_server, is_multi):
    import time
    start = time.monotonic()
    try:
        while True:
            sample = sampler.sample()
            if is_multi:
                evaluation = th.evaluate_multi(sample, thresholds_cfg)
                sample_out = {h: (s if not isinstance(s, Exception) else str(s)) for h, s in sample.items()}
            else:
                evaluation = th.evaluate(sample, thresholds_cfg)
                sample_out = sample
            if csv_logger:
                csv_logger.write(sample_out)
            if metrics_server:
                metrics_server.update(sample if is_multi else sample_out)
            print(json.dumps({**sample_out, "evaluation": evaluation}, default=str))
            sys.stdout.flush()
            if args.duration is not None and (time.monotonic() - start) >= args.duration:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
