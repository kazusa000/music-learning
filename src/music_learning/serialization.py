"""Stable JSON identity without importing persistence or application state."""
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
