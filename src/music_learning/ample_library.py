"""Shared structural validation for Ample indexed sample resources.

Preparing canonical index names never edits source libraries or admits sounds.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import stat

STAGING_ROOT = Path("H:/music/Sound Library/.staging")


def reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 1024)


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
        families[family] = {
            "sample_count": len(numbers),
            "first_number": unique[0],
            "last_number": unique[-1],
            "dbconfig": expected.name if expected.is_file() else None,
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
