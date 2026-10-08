"""Collectors read raw data from /proc and /sys. Each module exposes a
`read()` function returning raw counters, and a `compute(prev, curr, dt)`
function that turns two raw snapshots into human-meaningful metrics.
"""
