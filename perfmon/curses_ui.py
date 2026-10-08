"""Curses-based live dashboard (htop-style) with color-coded headroom status."""
import curses
import time

from . import thresholds as th
from .render import HEAD, build_lines, build_multi_lines

COLOR_PAIR = {}  # filled in once curses.start_color() has run


def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    COLOR_PAIR[th.OK] = curses.color_pair(1)
    COLOR_PAIR[th.WARN] = curses.color_pair(2) | curses.A_BOLD
    COLOR_PAIR[th.CRIT] = curses.color_pair(3) | curses.A_BOLD
    COLOR_PAIR[HEAD] = curses.color_pair(4) | curses.A_BOLD | curses.A_UNDERLINE


def _draw(stdscr, sample, evaluation, is_multi):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    ts = time.strftime("%H:%M:%S")
    header = f"perfmon  {ts}  overall: {evaluation['overall']}  (q to quit)"
    stdscr.addstr(0, 0, header[:max_x - 1], COLOR_PAIR.get(evaluation["overall"], 0) | curses.A_BOLD)
    stdscr.addstr(1, 0, "-" * min(max_x - 1, 100))

    lines = build_multi_lines(sample, evaluation) if is_multi else build_lines(sample, evaluation)
    row = 2
    for status, text in lines:
        if status == HEAD:
            row += 1  # blank line before each section header for spacing
        if row >= max_y:
            break
        if text:
            stdscr.addstr(row, 0, text[:max_x - 1], COLOR_PAIR.get(status, 0))
        row += 1
    stdscr.refresh()


def _main(stdscr, sampler, interval, thresholds_cfg, duration):
    curses.curs_set(0)
    stdscr.nodelay(True)
    _init_colors()
    is_multi = getattr(sampler, "is_multi", False)
    start = time.monotonic()
    while True:
        sample = sampler.sample()
        if is_multi:
            evaluation = th.evaluate_multi(sample, thresholds_cfg)
        else:
            evaluation = th.evaluate(sample, thresholds_cfg)
        _draw(stdscr, sample, evaluation, is_multi)

        if duration is not None and (time.monotonic() - start) >= duration:
            return
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                return
            time.sleep(0.05)


def run(sampler, interval, thresholds_cfg, duration=None):
    curses.wrapper(_main, sampler, interval, thresholds_cfg, duration)
