"""Tests de la CLI (F6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lite2obj.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "store_1x4x4.litematic"


def _has_jar() -> bool:
    from lite2obj import paths

    return paths.client_jar() is not None


def test_version_exits(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert "lite2obj" in capsys.readouterr().out


def test_info_prints_metadata(capsys):
    assert main(["info", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert "almacen bonito 1x4x4" in out
    assert "2 x 4 x 8" in out
    assert "región" in out
    assert "tipos de bloque distintos" in out


def test_info_blocks_zero_lists_everything(capsys):
    assert main(["info", str(FIXTURE), "--blocks", "0"]) == 0
    assert "tipos de bloque distintos" in capsys.readouterr().out


def test_info_missing_file_exits_with_error():
    with pytest.raises(FileNotFoundError):
        main(["info", "/no/existe.litematic"])


def test_list_runs(capsys):
    code = main(["list"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert ".litematic" in out or "No se encontraron" in out


def test_list_with_packs(capsys):
    main(["list", "--packs"])
    assert (
        "Resource packs" in capsys.readouterr().out
        or "No se encontraron" in capsys.readouterr().out
    )


def test_export_writes_files(tmp_path, capsys):
    if not _has_jar():
        pytest.skip("no hay client jar")
    code = main(["export", str(FIXTURE), str(tmp_path), "--name", "cosa", "--group-by", "block"])
    assert code == 0
    out = capsys.readouterr().out
    assert "OBJ" in out and "caras" in out
    assert (tmp_path / "cosa.obj").exists()
    assert (tmp_path / "cosa.mtl").exists()
    assert (tmp_path / "atlas.png").exists()


def test_export_default_destination(tmp_path, capsys):
    if not _has_jar():
        pytest.skip("no hay client jar")
    import shutil

    copied = tmp_path / FIXTURE.name
    shutil.copy(FIXTURE, copied)
    assert main(["export", str(copied)]) == 0
    produced = tmp_path / f"{FIXTURE.stem}_obj"
    assert produced.is_dir()
    assert list(produced.glob("*.obj"))
    del capsys


def test_export_z_up_and_no_cull(tmp_path, capsys):
    if not _has_jar():
        pytest.skip("no hay client jar")
    assert main(["export", str(FIXTURE), str(tmp_path), "--z-up", "--no-cull", "--no-tint"]) == 0
    assert "caras" in capsys.readouterr().out
    assert list(tmp_path.glob("*.obj"))


def test_export_unknown_pack_fails(tmp_path):
    if not _has_jar():
        pytest.skip("no hay client jar")
    with pytest.raises(FileNotFoundError):
        main(["export", str(FIXTURE), str(tmp_path), "--packs", "pack inexistente"])
