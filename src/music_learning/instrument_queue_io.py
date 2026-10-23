"""Bounded retries for atomic queue replacement under concurrent Windows readers."""
import time


def atomic_queue_write(writer, path, value, *, pause=time.sleep, attempts=8):
    for attempt in range(attempts):
        try:
            return writer(path, value)
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            pause(0.05 * (attempt + 1))
