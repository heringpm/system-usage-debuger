"""Plain-text live view using ANSI colors (no curses dependency)."""
import shutil
import sys
import time

from . import thresholds as th
from .render import HEAD, build_lines, build_multi_lines

ANSI = {th.OK: "\033[32m", th.WARN: "\033[33m", th.CRIT: "\033[31m", HEAD: "\033[1;36m"}
RESET = "\033[0m"
CLEAR = "\033[2J\033[H"


def run(sampler, interval, thresholds_cfg, duration=None, stream=sys.stdout, clear=True):
    start = time.monotonic()
    use_color = stream.isatty()
    is_multi = getattr(sampler, "is_multi", False)
    try:
        while True:
            sample = sampler.sample()
            if is_multi:
                evaluation = th.evaluate_multi(sample, thresholds_cfg)
                lines = build_multi_lines(sample, evaluation)
            else:
                evaluation = th.evaluate(sample, thresholds_cfg)
                lines = build_lines(sample, evaluation)

            out = []
            if use_color and clear:
                out.append(CLEAR)
            width = shutil.get_terminal_size((100, 24)).columns
            ts = time.strftime("%H:%M:%S")
            header = f"perfmon  {ts}  overall: {evaluation['overall']}"
            out.append(header.ljust(width))
            out.append("-" * min(width, 100))
            for status, text in lines:
                if status == HEAD:
                    out.append("")
                    if use_color:
                        out.append(f"{ANSI[HEAD]}{text}{RESET}")
                    elif text:
                        out.append(text)
                        out.append("-" * len(text))
                    continue
                if use_color:
                    out.append(f"{ANSI[status]}{text}{RESET}")
                else:
                    out.append(f"[{status:<4}] {text}")
            stream.write("\n".join(out) + "\n")
            stream.flush()

            if duration is not None and (time.monotonic() - start) >= duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
