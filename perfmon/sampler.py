"""Orchestrates collectors: keeps previous raw readings and turns each new
snapshot into a dict of computed metrics via collector.compute(prev, curr, dt).
"""
import time

from .collectors import cpu, memory, network, disk, pci, rdma

COLLECTORS = {
    "cpu": cpu,
    "memory": memory,
    "network": network,
    "disk": disk,
    "pci": pci,
    "rdma": rdma,
}


class Sampler:
    def __init__(self, collectors=None):
        self._collectors = collectors or COLLECTORS
        self._prev_raw = {}
        self._prev_time = None

    def sample(self):
        now = time.monotonic()
        dt = (now - self._prev_time) if self._prev_time is not None else 0.0

        raw = {name: mod.read() for name, mod in self._collectors.items()}

        result = {"timestamp": time.time(), "dt": dt}
        for name, mod in self._collectors.items():
            prev = self._prev_raw.get(name)
            result[name] = mod.compute(prev, raw[name], dt)

        self._prev_raw = raw
        self._prev_time = now
        return result
