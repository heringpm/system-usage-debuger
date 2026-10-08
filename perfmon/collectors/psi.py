"""Shared helper for reading Linux Pressure Stall Information (PSI) files."""
import os


def read_psi(path):
    """Parse a PSI file (e.g. /proc/pressure/cpu) into {"some": {...}, "full": {...}}."""
    if not os.path.exists(path):
        return None
    result = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                kind = parts[0]  # "some" or "full"
                fields = {}
                for p in parts[1:]:
                    k, v = p.split("=")
                    fields[k] = float(v) if k != "total" else int(v)
                result[kind] = fields
    except OSError:
        return None
    return result
