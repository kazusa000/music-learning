"""Small preset preparation has a separate budget from full-song production.

Keep this orchestration outside the pinned capture/verification implementation.
The Keyscape batch uses its own fixed capture code and settings unchanged.
"""

from dataclasses import replace


def capture_settings(settings):
    # Resources are copied once; CAS publication is a hardlink on this volume.
    # The existing capture path separately counts every dependency's actual size.
    # Add room for short native probes/state, without charging a whole-song WAV.
    return replace(settings, pipeline_min_free_bytes=settings.instrument_min_free_bytes + 16 * 1024**2)
