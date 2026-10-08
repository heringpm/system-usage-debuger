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


def _build_rows(sample, evaluation, is_multi):
    """Flatten the section lines into (status, text) rows, inserting the
    blank-line spacer before headers so scrolling sees the same layout
    that _draw() used to paint directly."""
    lines = build_multi_lines(sample, evaluation) if is_multi else build_lines(sample, evaluation)
    rows = []
    for status, text in lines:
        if status == HEAD:
            rows.append((None, ""))  # blank line before each section header
        rows.append((status, text))
    return rows


def _draw(stdscr, sample, evaluation, is_multi, scroll):
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()
    ts = time.strftime("%H:%M:%S")
    header = f"perfmon  {ts}  overall: {evaluation['overall']}  (q to quit, \u2191/\u2193/PgUp/PgDn to scroll)"
    stdscr.addstr(0, 0, header[:max_x - 1], COLOR_PAIR.get(evaluation["overall"], 0) | curses.A_BOLD)
    stdscr.addstr(1, 0, "-" * min(max_x - 1, 100))

    rows = _build_rows(sample, evaluation, is_multi)
    body_height = max_y - 2
    max_scroll = max(0, len(rows) - body_height)
    scroll = max(0, min(scroll, max_scroll))

    row_y = 2
    for status, text in rows[scroll:scroll + body_height]:
        if text:
            stdscr.addstr(row_y, 0, text[:max_x - 1], COLOR_PAIR.get(status, 0))
        row_y += 1
    if max_scroll > 0:
        indicator = f"[{scroll}/{max_scroll} -- more below]" if scroll < max_scroll else f"[{scroll}/{max_scroll} -- bottom]"
        stdscr.addstr(max_y - 1, max(0, max_x - len(indicator) - 1), indicator[:max_x - 1])
    stdscr.refresh()
    return scroll, max_scroll


def _main(stdscr, sampler, interval, thresholds_cfg, duration):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    _init_colors()
    is_multi = getattr(sampler, "is_multi", False)
    start = time.monotonic()
    scroll = 0
    while True:
        sample = sampler.sample()
        if is_multi:
            evaluation = th.evaluate_multi(sample, thresholds_cfg)
        else:
            evaluation = th.evaluate(sample, thresholds_cfg)
        scroll, max_scroll = _draw(stdscr, sample, evaluation, is_multi, scroll)

        if duration is not None and (time.monotonic() - start) >= duration:
            return
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                return
            elif ch == curses.KEY_UP:
                scroll = max(0, scroll - 1)
            elif ch == curses.KEY_DOWN:
                scroll = min(max_scroll, scroll + 1)
            elif ch == curses.KEY_PPAGE:
                scroll = max(0, scroll - (stdscr.getmaxyx()[0] - 2))
            elif ch == curses.KEY_NPAGE:
                scroll = min(max_scroll, scroll + (stdscr.getmaxyx()[0] - 2))
            elif ch == curses.KEY_HOME:
                scroll = 0
            elif ch == curses.KEY_END:
                scroll = max_scroll
            else:
                time.sleep(0.05)
                continue
            scroll, max_scroll = _draw(stdscr, sample, evaluation, is_multi, scroll)


def run(sampler, interval, thresholds_cfg, duration=None):
    try:
        curses.wrapper(_main, sampler, interval, thresholds_cfg, duration)
    except KeyboardInterrupt:
        pass
