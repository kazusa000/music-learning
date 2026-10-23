"""Queue previews for admitted passes without revisiting validation evidence."""
from collections import Counter

from .contracts import PrepareInstrumentPreview
from .instrument_previews import fingerprint


def handoff(store, entries, passed_ids, offered, *, key_prefix, batch_size=32):
    eligible = [e for e in entries if e["id"] in passed_ids
                and e.get("audio_verified") and e.get("native_admission")]
    identities = {e["id"]: fingerprint(store.settings, e) for e in eligible}
    with store.db() as db:
        rows = {r["id"]: dict(r) for r in db.execute("select id,status,error from previews")}
    queued = 0
    for entry in eligible:
        identifier = identities[entry["id"]]
        row = rows.get(identifier)
        if row and row["status"] in {"ready", "queued", "loading", "failed"}:
            offered[identifier] = entry["id"]
            continue
        # A later manual cancellation or cache eviction must not cause a loop.
        if identifier in offered or queued >= batch_size:
            continue
        result = store.prepare(
            PrepareInstrumentPreview(preset_id=entry["id"], retry=False),
            key=f"{key_prefix}:{identifier}", priority=-5,
            _entry=entry, _background=True,
        )
        offered[identifier] = entry["id"]
        rows[identifier] = result
        queued += 1
    counts = Counter(rows.get(i, {}).get("status", "not_queued") for i in identities.values())
    failures = {preset: rows[i].get("error") for preset, i in identities.items()
                if rows.get(i, {}).get("status") == "failed"}
    wake_preset = next((p for p, i in identities.items()
                        if rows.get(i, {}).get("status") == "queued"), None)
    return dict(eligible=len(eligible), enqueued=queued,
                awaiting_enqueue=sum(i not in offered for i in identities.values()), counts=dict(counts),
                failures=failures, wake_preset=wake_preset)
