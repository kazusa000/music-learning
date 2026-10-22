"""Shared structural validation for Ample indexed sample resources.

Preparing canonical index names never edits source libraries or admits sounds.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import xml.etree.ElementTree as ET

from .vst3_state import memory_block

STAGING_ROOT = Path("H:/music/Sound Library/.staging")
MAX_INDEX_BYTES = 16 * 1024**2


def reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)


def _inspect_dbconfig(index, family, samples):
    """Decode and validate Ample's indexed XML against its ASCF file family.

    ``*_DBConfig.cf`` is a JUCE MemoryBlock string (decimal decoded byte count,
    dot, then Ample's six-bit alphabet). The payload maps playable sample rows
    to compact-file names and byte offsets. File presence alone misses broken
    mappings, an important distinction when diagnosing silent pickup groups.
    """
    index = Path(index)
    if index.stat().st_size <= 0 or index.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError(f"Invalid Ample DBConfig size: {index.name}")
    try:
        encoded_bytes = index.read_bytes()
        encoded = encoded_bytes.decode("ascii")
        payload = memory_block(encoded)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"Invalid Ample DBConfig encoding: {index.name}") from error
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError(f"Unsupported Ample DBConfig XML: {index.name}")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError(f"Malformed Ample DBConfig XML: {index.name}") from error
    if root.tag != "DBConfigs" or root.get("DBName") != family:
        raise ValueError(f"Ample DBConfig identity differs: {index.name}")
    sample_sections = root.findall("SampleConfigs")
    compact_sections = root.findall("CompactConfigs")
    if len(sample_sections) != 1 or len(compact_sections) != 1:
        raise ValueError(f"Ample DBConfig lacks unique sample/compact sections: {index.name}")

    compact = compact_sections[0]
    compact_entries = {}
    for key, name in compact.attrib.items():
        match = re.fullmatch(r"CompactFileName(\d+)", key)
        if not match:
            raise ValueError(f"Unexpected compact-file key in {index.name}: {key}")
        number = int(match.group(1))
        if number <= 0 or number in compact_entries or not name:
            raise ValueError(f"Invalid compact-file index in {index.name}")
        compact_entries[number] = name
    if not compact_entries or sorted(compact_entries) != list(range(1, len(compact_entries) + 1)):
        raise ValueError(f"Non-contiguous compact-file index in {index.name}")

    expected_names = {path.name for path in samples.values()}
    listed_names = set(compact_entries.values())
    if len(listed_names) != len(compact_entries) or listed_names != expected_names:
        raise ValueError(f"Ample DBConfig compact-file list differs from ASCF files: {index.name}")
    for name in listed_names:
        if Path(name).name != name or not name.startswith(family + "-") or not name.endswith(".ascf"):
            raise ValueError(f"Invalid compact-file name in {index.name}: {name}")

    rows = sample_sections[0].findall("sample")
    if not rows:
        raise ValueError(f"Ample DBConfig contains no sample records: {index.name}")
    ids = set()
    maximum_offset = 0
    group_names = set()
    for row in rows:
        sample_id = row.get("SampleID")
        file_name = row.get("CompactFileName")
        try:
            sample_id_value = int(sample_id)
            offset = int(row.get("Offset", ""))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid sample ID/offset in {index.name}") from error
        if sample_id_value <= 0 or sample_id_value in ids:
            raise ValueError(f"Duplicate or invalid sample ID in {index.name}")
        ids.add(sample_id_value)
        sample = samples.get(file_name)
        if sample is None:
            raise ValueError(f"Unknown ASCF reference in {index.name}: {file_name}")
        if offset < 0 or offset >= sample.stat().st_size:
            raise ValueError(f"ASCF offset is outside its file in {index.name}: {file_name}@{offset}")
        group_name = row.get("GroupName")
        if not group_name:
            raise ValueError(f"Sample row has no group name in {index.name}")
        group_names.add(group_name)
        maximum_offset = max(maximum_offset, offset)
    return {
        "encoded_bytes": index.stat().st_size,
        "encoded_sha256": hashlib.sha256(encoded_bytes).hexdigest(),
        "decoded_bytes": len(payload),
        "decoded_sha256": hashlib.sha256(payload).hexdigest(),
        "sample_records": len(rows),
        "compact_files": len(compact_entries),
        "sample_groups": len(group_names),
        "maximum_offset": maximum_offset,
    }


def inspect_sample_directory(library, instrument, *, allow_text_indexes=False):
    """Validate Ample's indexed sample layout before touching its config.

    Ample does not discover a usable instrument from ASCF files alone. Every
    numbered sample family needs its matching ``*_DBConfig.cf`` index. Some
    packages append ``.txt`` to the index filename. File presence or a successful
    plug-in scan alone cannot establish that these indexes were loaded.
    """
    library = Path(library)
    if reparse(library):
        raise ValueError("Sample roots must not be reparse points")
    library = library.resolve()
    if instrument not in {"AGLP", "AGSC", "AGM"} or not library.is_dir():
        raise ValueError("Expected an existing Ample sample directory")

    pattern = re.compile(rf"^({re.escape(instrument)}[A-Za-z0-9]+)-(\d+)\.ascf$", re.IGNORECASE)
    groups = {}
    malformed = []
    for sample in library.glob(instrument + "*.ascf"):
        match = pattern.match(sample.name)
        if not match:
            malformed.append(sample.name)
            continue
        if reparse(sample) or not sample.is_file() or sample.stat().st_size == 0:
            raise ValueError(f"Empty Ample sample file: {sample.name}")
        groups.setdefault(match.group(1), []).append(int(match.group(2)))
    if not groups:
        raise ValueError("Missing Ample ASCF sample files")
    if malformed:
        raise ValueError("Malformed Ample ASCF filenames: " + ", ".join(sorted(malformed)))

    families = {}
    problems = []
    for family, numbers in sorted(groups.items()):
        unique = sorted(set(numbers))
        if len(unique) != len(numbers):
            problems.append(f"duplicate sample numbers in {family}")
        if unique[0] != 1 or unique != list(range(1, unique[-1] + 1)):
            problems.append(f"non-contiguous sample numbers in {family}")

        expected = library / f"{family}_DBConfig.cf"
        noncanonical = sorted(library.glob(f"{family}_DBConfig.cf.*"))
        alias = library / (expected.name + ".txt")
        accepted_alias = allow_text_indexes and noncanonical == [alias] and alias.is_file() and alias.stat().st_size > 0 and not reparse(alias)
        if not expected.is_file():
            if accepted_alias:
                pass
            elif noncanonical:
                problems.append(
                    f"noncanonical DBConfig index for {family}: "
                    + ", ".join(path.name for path in noncanonical)
                )
            else:
                problems.append(f"missing DBConfig index for {family}: {expected.name}")
        elif reparse(expected) or expected.stat().st_size == 0:
            problems.append(f"empty DBConfig index for {family}: {expected.name}")
        elif noncanonical:
            problems.append(f"ambiguous DBConfig indexes for {family}")
        index_metadata = None
        index_path = alias if accepted_alias else expected
        if (index_path.is_file() and not reparse(index_path)
                and 0 < index_path.stat().st_size <= MAX_INDEX_BYTES):
            sample_paths = {f"{family}-{number}.ascf": library / f"{family}-{number}.ascf"
                            for number in unique}
            index_metadata = _inspect_dbconfig(index_path, family, sample_paths)
        families[family] = {
            "sample_count": len(numbers),
            "first_number": unique[0],
            "last_number": unique[-1],
            "dbconfig": expected.name if expected.is_file() else None,
            "index": index_metadata,
        }
        if accepted_alias:
            families[family]["index_source"] = alias.name

    if problems:
        raise ValueError("Incomplete Ample sample library: " + "; ".join(problems))
    return {"path": str(library), "samples": sum(map(len, groups.values())), "families": families}


def stage_library(source, destination, instrument):
    """Expose a validated sample layout in the specified H staging directory.

    ASCF files are same-volume hard links; only small indexes are copied. There
    is no recursive source copy, source rename, silent cross-volume fallback,
    user configuration change or catalog publication. Partial failures retain
    an explicit journal instead of deleting unknown or protected directories.
    """
    source = Path(source)
    if reparse(source):
        raise ValueError("Sample roots must not be reparse points")
    source, destination = source.resolve(), Path(destination).resolve()
    allowed = STAGING_ROOT.resolve()
    if not destination.is_relative_to(allowed) or destination == allowed:
        raise ValueError("Destination must be a new directory under the configured sound-library staging root")
    if destination.exists() or source.is_relative_to(destination):
        raise ValueError("Choose a new staging directory distinct from the source")
    inventory = inspect_sample_directory(source, instrument, allow_text_indexes=True)
    destination.mkdir(parents=True)
    report = dict(status="preparing", instrument=instrument, source=str(source),
                  destination=str(destination), hardlinks=[], indexes=[], audio_verified=False)

    def save():
        path = destination / "preparation.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(path)

    try:
        save()
        for sample in sorted(source.glob(instrument + "*.ascf")):
            target = destination / sample.name
            target.hardlink_to(sample)
            if not target.samefile(sample):
                raise ValueError("Sample hardlink identity mismatch")
            report["hardlinks"].append(dict(name=sample.name, bytes=sample.stat().st_size))
        for family, item in inventory["families"].items():
            original = source / item.get("index_source", item["dbconfig"])
            target = destination / (family + "_DBConfig.cf")
            if original.stat().st_size > 16 * 1024**2:
                raise ValueError("Index exceeds the small-resource copy budget")
            original_bytes = original.read_bytes()
            shutil.copyfile(original, target)
            if target.read_bytes() != original_bytes or original.read_bytes() != original_bytes:
                raise ValueError("Index changed during preparation")
            report["indexes"].append(dict(source=original.name, name=target.name,
                bytes=len(original_bytes), sha256=hashlib.sha256(original_bytes).hexdigest()))
        report.update(status="prepared", inventory=inspect_sample_directory(destination, instrument))
    except BaseException as error:
        report.update(status="partial", error=str(error))
        raise
    finally:
        save()
    return report


def prepare_final_sample_bank(source, destination, instrument, library_id,
                              package_inventory, package_inventory_sha256, *,
                              allowed_library_root=Path("H:/music/Sound Library")):
    """Hard-link a validated sample layout into an approved final bank path.

    This is deliberately a preparation step, not an installation receipt: it
    reuses the SHA values in a prior package inventory for large ASCF files and
    marks them as needing a fresh full-content audit before publication. It
    reads and hashes only the small DBConfig indexes. No catalog or plugin
    configuration is changed.
    """
    source = Path(source)
    destination = Path(destination)
    if reparse(allowed_library_root):
        raise ValueError("Selected Sound Library root must not be a reparse point")
    allowed = Path(allowed_library_root).resolve(strict=True)
    if instrument not in {"AGM", "AGLP", "AGSC"}:
        raise ValueError("Unsupported Ample guitar product")
    if not library_id or not isinstance(library_id, str):
        raise ValueError("A stable Ample library ID is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(package_inventory_sha256 or "")):
        raise ValueError("A pinned package-inventory SHA-256 is required")
    if reparse(source) or not source.is_dir():
        raise ValueError("Ample sample source must be a regular directory")

    # Check the destination lexically before resolve(strict=False) can hide a
    # symlink/junction in an existing parent component.
    requested = Path(os.path.abspath(destination))
    try:
        relative = requested.relative_to(allowed)
    except ValueError as error:
        raise ValueError("Final Ample bank must stay inside the selected Sound Library") from error
    if not relative.parts:
        raise ValueError("Final Ample bank cannot be the Sound Library root")
    current = allowed
    for part in relative.parts:
        current = current / part
        if current.exists() and reparse(current):
            raise ValueError("Final Ample bank path contains a reparse point")

    inventory_product = package_inventory.get("libraries", {}).get(instrument)
    if not isinstance(inventory_product, dict):
        raise ValueError("Prior Ample package inventory has no product entry")
    inventory_samples = inventory_product.get("ascf")
    if not isinstance(inventory_samples, list) or not inventory_samples:
        raise ValueError("Prior Ample package inventory has no ASCF file list")
    prior = {}
    for record in inventory_samples:
        name, size, digest = record.get("name"), record.get("bytes"), record.get("sha256")
        if (not isinstance(name, str) or Path(name).name != name
                or type(size) is not int or size <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))
                or name in prior):
            raise ValueError("Prior Ample inventory contains an invalid ASCF record")
        prior[name] = {"bytes": size, "sha256": digest}

    # Structural validation consumes the small index files and stats the ASCF
    # ranges, but never reads the large sample contents.
    inventory = inspect_sample_directory(source, instrument)
    samples = sorted(source.glob(instrument + "*.ascf"), key=lambda path: path.name.casefold())
    actual = {path.name: path for path in samples}
    if set(actual) != set(prior):
        raise ValueError("Current Ample ASCF file set differs from the prior package inventory")
    for name, sample in actual.items():
        if reparse(sample) or not sample.is_file() or sample.stat().st_size != prior[name]["bytes"]:
            raise ValueError("Current Ample ASCF metadata differs from the prior inventory: " + name)

    index_sources = {}
    for family, details in inventory["families"].items():
        source_name = details.get("index_source") or details.get("dbconfig")
        if not source_name:
            raise ValueError("Validated Ample family has no source DBConfig: " + family)
        source_index = source / source_name
        target_name = family + "_DBConfig.cf"
        if (reparse(source_index) or not source_index.is_file()
                or source_index.stat().st_size > MAX_INDEX_BYTES):
            raise ValueError("Ample DBConfig is unavailable or exceeds the small-file limit")
        digest = hashlib.sha256(source_index.read_bytes()).hexdigest()
        index_sources[target_name] = {"source": source_index, "bytes": source_index.stat().st_size,
                                      "sha256": digest}

    expected_names = set(actual) | set(index_sources) | {"preparation.json"}
    if destination.exists():
        if not destination.is_dir() or reparse(destination):
            raise ValueError("Existing Ample destination is not a regular directory")
        unexpected = {path.name for path in destination.iterdir()} - expected_names
        if unexpected:
            raise ValueError("Existing Ample destination contains unknown files")
        journal_path = destination / "preparation.json"
        if not journal_path.is_file() or reparse(journal_path):
            raise ValueError("Existing Ample destination has no matching preparation journal")
        old = json.loads(journal_path.read_bytes())
        if (old.get("instrument") != instrument or old.get("library_id") != library_id
                or old.get("source_inventory_sha256") != package_inventory_sha256
                or old.get("source") != str(source.resolve())):
            raise ValueError("Existing Ample preparation belongs to another source or inventory")
    else:
        destination.mkdir(parents=True)

    report = {
        "schema_version": 1,
        "status": "preparing",
        "instrument": instrument,
        "library_id": library_id,
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "source_inventory_sha256": package_inventory_sha256,
        "sample_count": len(samples),
        "sample_bytes_logical": sum(path.stat().st_size for path in samples),
        "sample_transfer": "same-volume hard links; source files are unchanged",
        "sample_sha256_status": "prior inventory only; fresh full-content verification pending",
        "sample_hashes_currently_rechecked": False,
        "indexes": [],
        "hardlinks": [],
        "inventory": inventory,
        "catalog_written": False,
        "audio_verified": False,
    }

    def save():
        path = destination / "preparation.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(path)

    save()
    try:
        for sample in samples:
            target = destination / sample.name
            if target.exists():
                if reparse(target) or not target.is_file() or not target.samefile(sample):
                    raise ValueError("Existing Ample sample link is not the expected source: " + sample.name)
            else:
                target.hardlink_to(sample)
            target_stat = target.stat()
            if target_stat.st_size != prior[sample.name]["bytes"] or not target.samefile(sample):
                raise ValueError("Ample sample hardlink metadata/identity differs: " + sample.name)
            report["hardlinks"].append({
                "name": sample.name,
                "bytes": target_stat.st_size,
                "mtime_ns": target_stat.st_mtime_ns,
                "prior_inventory_sha256": prior[sample.name]["sha256"],
            })
        for name, details in sorted(index_sources.items()):
            source_index = details["source"]
            target = destination / name
            if target.exists():
                if reparse(target) or not target.is_file():
                    raise ValueError("Existing Ample DBConfig is not a regular file: " + name)
                if hashlib.sha256(target.read_bytes()).hexdigest() != details["sha256"]:
                    raise ValueError("Existing Ample DBConfig differs from its source: " + name)
            else:
                shutil.copy2(source_index, target)
            target_bytes = target.read_bytes()
            if (len(target_bytes) != details["bytes"]
                    or hashlib.sha256(target_bytes).hexdigest() != details["sha256"]):
                raise ValueError("Ample DBConfig copy verification failed: " + name)
            report["indexes"].append({
                "source": source_index.name,
                "name": name,
                "bytes": details["bytes"],
                "sha256": details["sha256"],
                "mtime_ns": target.stat().st_mtime_ns,
            })
        report.update(status="resources_prepared_pending_full_hash_and_audio_validation")
    except BaseException as error:
        report.update(status="partial_preparation", error=str(error)[:2000])
        raise
    finally:
        save()
    return report
