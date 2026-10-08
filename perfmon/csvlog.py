"""Flatten a sample dict into a wide CSV row and append it to a file.

Because network/disk/pci metrics are keyed by device name (which can vary
over time, e.g. hotplug), the header is (re)written whenever a new column
appears. Simpler consumers (pandas, Excel) can just read the final file.
"""
import csv
import os


def _flatten(d, prefix, out):
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            _flatten(v, key, out)
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                out[f"{key}.{i}"] = item
        else:
            out[key] = v


def flatten_sample(sample):
    out = {}
    _flatten(sample, "", out)
    return out


class CsvLogger:
    def __init__(self, path):
        self.path = path
        self._fieldnames = None
        self._load_existing_header()

    def _load_existing_header(self):
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            with open(self.path, newline="") as f:
                reader = csv.reader(f)
                try:
                    self._fieldnames = next(reader)
                except StopIteration:
                    self._fieldnames = None

    def write(self, sample):
        row = flatten_sample(sample)
        new_fields = [k for k in row if k not in (self._fieldnames or [])]

        if self._fieldnames is None:
            self._fieldnames = list(row.keys())
            self._rewrite_with_header(self._fieldnames)
        elif new_fields:
            self._fieldnames = self._fieldnames + new_fields
            self._rewrite_with_header(self._fieldnames, migrate=True)

        with open(self.path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._fieldnames, extrasaction="ignore")
            writer.writerow(row)

    def _rewrite_with_header(self, fieldnames, migrate=False):
        """Write header (and, if migrating, re-pad existing rows with blanks
        for newly-added columns) so the CSV stays a consistent rectangle."""
        existing_rows = []
        if migrate and os.path.exists(self.path):
            with open(self.path, newline="") as f:
                reader = csv.DictReader(f)
                existing_rows = list(reader)
        with open(self.path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in existing_rows:
                writer.writerow(row)
