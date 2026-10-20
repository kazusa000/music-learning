"""Library-scoped instrument models above immutable playable preset variants."""

from __future__ import annotations

import hashlib
import json

from .contracts import InstrumentModelSpec


def keyscape_model(models):
    """Use original Model attributes, never split a patch title into a model."""
    names = sorted(set(models), key=str.casefold)
    if not names:
        return None
    layer = len(names) > 1 or any(
        n.casefold() in {"duo combination", "hohner clavinet-pianet duo"} for n in names
    )
    name = " + ".join(names)
    return dict(key="factory:" + name, name=name, kind="layer" if layer else "instrument")


def with_model(entry):
    spec = entry.get("model_spec")
    basis = entry.get("model_basis", "import-assignment")
    if spec is None:
        piano = entry.get("expected_pianoteq", {}).get("instrument")
        if piano:
            spec = dict(
                key="factory:" + piano, name=piano, kind="synth" if piano == "Syngular" else "instrument"
            )
            basis = "pianoteq-factory-model"
        elif entry.get("library_id") == "spectrasonics-keyscape":
            # Older catalogs kept original Model values mixed with Subcategory tags.
            from .keyscape_library import FACTORY_MODEL_NAMES

            names = [t for t in entry.get("tags", []) if t.casefold() in FACTORY_MODEL_NAMES]
            spec = keyscape_model(names)
            basis = "keyscape-factory-model"
        elif entry.get("backend") == "surge-fxp":
            spec = dict(key="engine:surge-xt", name="Surge XT", kind="synth")
            basis = "surge-engine"
    if spec is None:
        # Unknown legacy entries remain independent; matching names never merge them.
        spec = dict(key="preset:" + entry["id"], name=entry["name"], kind="unspecified")
        basis = "single-preset-fallback"
    spec = InstrumentModelSpec.model_validate(spec).model_dump()
    library = entry["source"]["library_id"]
    identity = json.dumps([library, spec["key"]], ensure_ascii=True, separators=(",", ":"))
    model_id = "model-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    variant = entry.get("variant_name", entry["name"])
    if not isinstance(variant, str) or not variant.strip() or len(variant) > 512:
        raise ValueError("Invalid variant name for preset " + entry["id"])
    return {
        **entry,
        "model_spec": spec,
        "model_basis": basis,
        "model": dict(id=model_id, name=spec["name"], kind=spec["kind"], basis=basis),
        "variant_name": variant,
    }
