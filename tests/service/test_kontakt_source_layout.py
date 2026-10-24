from pathlib import Path

import pytest

from music_learning import kontakt_source_layout as layout


def _roots(tmp_path: Path):
    library = tmp_path / "Kontakt" / "Libraries" / "css-solo"
    samples = library / "Samples"
    source = library / "Classic Legato Patches" / "CSSS (Classic Legato) Cello.nki"
    source.parent.mkdir(parents=True)
    samples.mkdir()
    source.write_bytes(b"fixture-nki")
    layout_root = tmp_path / "Kontakt" / "SourceLayouts" / "css-solo-legacy-v1"
    return library, samples, source, layout_root


def _fake_junctions(monkeypatch):
    targets: dict[str, Path] = {}

    def create(path: Path, target: Path) -> None:
        path.mkdir()
        targets[str(path)] = target

    def read(path: Path) -> Path | None:
        return targets.get(str(path))

    monkeypatch.setattr(layout, "_create_junction", create)
    monkeypatch.setattr(layout, "_read_junction_target", read)
    return targets


def _allow_budget(drive: str, *, idle_samples: int) -> dict:
    assert drive == "H:"
    assert idle_samples == 3
    return {"allowed": True, "reason": "test_fixture"}


def test_private_layout_copies_only_named_nki_and_reuses_matching_file(tmp_path, monkeypatch):
    library, samples, source, layout_root = _roots(tmp_path)
    _fake_junctions(monkeypatch)
    relative = Path("Classic Legato Patches") / source.name

    first = layout.prepare_kontakt_source_layout(
        library_root=library,
        layout_root=layout_root,
        sample_root=samples,
        relative_nki_paths=[relative],
        budget=_allow_budget,
    )
    destination = layout_root / "Instruments" / relative
    assert destination.read_bytes() == source.read_bytes()
    assert first["files"][0]["disposition"] == "copied_verified"
    assert first["samples_junction"]["target"] == str(samples)

    second = layout.prepare_kontakt_source_layout(
        library_root=library,
        layout_root=layout_root,
        sample_root=samples,
        relative_nki_paths=[relative],
        budget=_allow_budget,
    )
    assert second["files"][0]["disposition"] == "reused_verified"
    assert destination.read_bytes() == source.read_bytes()


def test_private_layout_refuses_changed_existing_nki_without_overwrite(tmp_path, monkeypatch):
    library, samples, source, layout_root = _roots(tmp_path)
    _fake_junctions(monkeypatch)
    relative = Path("Classic Legato Patches") / source.name
    destination = layout_root / "Instruments" / relative
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different")

    with pytest.raises(layout.KontaktSourceLayoutError, match="refusing overwrite"):
        layout.prepare_kontakt_source_layout(
            library_root=library,
            layout_root=layout_root,
            sample_root=samples,
            relative_nki_paths=[relative],
            budget=_allow_budget,
        )
    assert destination.read_bytes() == b"different"


def test_private_layout_rejects_unexpected_junction_target_before_copy(tmp_path, monkeypatch):
    library, samples, source, layout_root = _roots(tmp_path)
    _fake_junctions(monkeypatch)
    wrong_target = tmp_path / "other-samples"
    wrong_target.mkdir()
    layout_root.mkdir(parents=True)
    (layout_root / "Instruments").mkdir()
    (layout_root / "Samples").mkdir()
    monkeypatch.setattr(layout, "_read_junction_target", lambda _path: wrong_target)

    with pytest.raises(layout.KontaktSourceLayoutError, match="not the expected junction"):
        layout.prepare_kontakt_source_layout(
            library_root=library,
            layout_root=layout_root,
            sample_root=samples,
            relative_nki_paths=[Path("Classic Legato Patches") / source.name],
            budget=_allow_budget,
        )
    assert not (layout_root / "Instruments" / "Classic Legato Patches" / source.name).exists()


def test_busy_storage_defers_before_creating_layout(tmp_path):
    library, samples, source, layout_root = _roots(tmp_path)

    def busy_budget(_drive: str, *, idle_samples: int) -> dict:
        assert idle_samples == 3
        return {"allowed": False, "reason": "io_pressure"}

    with pytest.raises(layout.KontaktSourceLayoutDeferred):
        layout.prepare_kontakt_source_layout(
            library_root=library,
            layout_root=layout_root,
            sample_root=samples,
            relative_nki_paths=[Path("Classic Legato Patches") / source.name],
            budget=busy_budget,
        )
    assert not layout_root.exists()


def test_private_layout_rejects_path_traversal_before_creating_copy(tmp_path, monkeypatch):
    library, samples, _source, layout_root = _roots(tmp_path)
    _fake_junctions(monkeypatch)

    with pytest.raises(layout.KontaktSourceLayoutError, match="relative NKI path"):
        layout.prepare_kontakt_source_layout(
            library_root=library,
            layout_root=layout_root,
            sample_root=samples,
            relative_nki_paths=[Path("..") / "outside.nki"],
            budget=_allow_budget,
        )
    assert not layout_root.exists()


@pytest.mark.parametrize(
    "unsafe_path",
    [
        r"C:relative.nki",
        r"C:\absolute.nki",
        r"\rooted.nki",
        r"\\server\share\remote.nki",
        r"folder:stream.nki",
        r"file.nki:payload",
    ],
)
def test_private_layout_rejects_windows_drive_root_and_ads_paths(tmp_path, monkeypatch, unsafe_path):
    library, samples, _source, layout_root = _roots(tmp_path)
    _fake_junctions(monkeypatch)

    with pytest.raises(layout.KontaktSourceLayoutError, match="relative NKI path"):
        layout.prepare_kontakt_source_layout(
            library_root=library,
            layout_root=layout_root,
            sample_root=samples,
            relative_nki_paths=[unsafe_path],
            budget=_allow_budget,
        )
    assert not layout_root.exists()
