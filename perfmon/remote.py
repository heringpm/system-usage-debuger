"""Run perfmon's collectors on remote hosts over SSH.

No install is required on the remote side: a self-contained collector
script (see bundler.py) is piped to `ssh <host> <python>` over stdin and
streams one JSON sample per line back over stdout, which we read in a
background thread per host.

Requires passwordless (key-based) SSH auth to each host -- we pass
BatchMode=yes so a failed/missing key fails fast instead of hanging on a
password prompt.
"""
import json
import subprocess
import threading
import time

from .bundler import build_bundle

CONNECT_TIMEOUT_S = 10


class RemoteError(RuntimeError):
    pass


class RemoteSampler:
    """Streams samples from a single remote host via a persistent SSH session."""

    def __init__(self, host, interval, python="python3", ssh_args=None):
        self.host = host
        self._latest = None
        self._error = None
        self._lock = threading.Lock()
        bundle = build_bundle(interval)

        cmd = [
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT_S}",
            *(ssh_args or []), host, python, "-",
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._proc.stdin.write(bundle)
        self._proc.stdin.close()

        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def _reader_loop(self):
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    sample = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self._lock:
                    self._latest = sample
        finally:
            self._proc.stdout.close()
            rc = self._proc.wait()
            if rc != 0:
                stderr = self._proc.stderr.read()
                with self._lock:
                    self._error = stderr.strip() or f"ssh to {self.host} exited with code {rc}"

    def latest(self):
        """Return the most recent sample (or None if none received yet)."""
        with self._lock:
            if self._error:
                raise RemoteError(self._error)
            return self._latest

    def wait_for_first(self, timeout=CONNECT_TIMEOUT_S + 15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sample = self.latest()
            if sample is not None:
                return sample
            time.sleep(0.1)
        raise RemoteError(f"timed out waiting for first sample from {self.host}")

    def close(self):
        self._proc.terminate()


class MultiHostSampler:
    """Fan-out sampler: polls multiple RemoteSamplers and returns a snapshot
    dict of {host: sample_or_None}. A host with sample=None is still waiting
    for its first data; a host whose value is an Exception failed."""

    is_multi = True

    def __init__(self, hosts, interval, python="python3", ssh_args=None):
        self._samplers = {
            host: RemoteSampler(host, interval, python=python, ssh_args=ssh_args)
            for host in hosts
        }

    def sample(self):
        result = {}
        for host, sampler in self._samplers.items():
            try:
                result[host] = sampler.latest()
            except RemoteError as e:
                result[host] = e
        return result

    def wait_for_first(self, timeout=CONNECT_TIMEOUT_S + 15):
        deadline = time.monotonic() + timeout
        for host, sampler in self._samplers.items():
            remaining = max(0.1, deadline - time.monotonic())
            try:
                sampler.wait_for_first(timeout=remaining)
            except RemoteError:
                pass  # surfaced later via sample()

    def close(self):
        for sampler in self._samplers.values():
            sampler.close()
