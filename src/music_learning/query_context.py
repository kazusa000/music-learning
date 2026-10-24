"""Musical catalogue context and explicit, size-bounded query pages."""

from .serialization import canonical


def preset_context(preset):
    if preset is None:
        return None
    # Capture still uses the complete pinned inventory. Discovery needs names,
    # playable ranges and mapping, not thousands of restore-time parameters.
    fields = ("id", "name", "display_name", "variant_name", "family", "tags", "usage",
              "backend", "audio_verified", "default", "gm_programs", "instrument_ids",
              "key_range", "drum_map", "model", "model_spec", "source", "performance_profile")
    return {key: preset[key] for key in fields if key in preset}


def fit_pages(result, pages, offset, budget=45000):
    """Shorten only declared pages; every omitted item has a next_offset."""
    while len(canonical(result)) > budget:
        candidates = [(parent, key, total, next_key) for parent, key, total, next_key in pages
                      if len(parent[key]) > 1]
        if not candidates:
            return False
        parent, key, total, next_key = max(candidates, key=lambda page: len(canonical(page[0][page[1]])))
        parent[key] = parent[key][:max(1, len(parent[key]) // 2)]
        parent[next_key] = offset + len(parent[key]) if offset + len(parent[key]) < total else None
    return True
