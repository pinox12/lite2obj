"""Tests de los scripts de empaquetado (builds con y sin texturas)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _prepare_assets() -> ModuleType:
    path = ROOT / "packaging" / "prepare_assets.py"
    spec = importlib.util.spec_from_file_location("lite2obj_prepare_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_assets_skip_does_not_touch_the_pack(tmp_path, monkeypatch, capsys):
    module = _prepare_assets()
    monkeypatch.setattr(module, "TARGET_DIR", tmp_path)

    assert module.main(["--skip"]) == 0
    assert not (tmp_path / "lite2obj-assets.l2oassets").exists()
    assert "sin archivo de assets" in capsys.readouterr().out.lower()


def test_prepare_assets_optional_accepts_missing_pack_and_jar(tmp_path, monkeypatch, capsys):
    module = _prepare_assets()
    monkeypatch.setattr(module, "TARGET_DIR", tmp_path)
    monkeypatch.setattr(module.paths, "asset_pack", lambda: None)
    monkeypatch.setattr(module.paths, "explicit_client_jar", lambda: None)

    assert module.main(["--optional"]) == 0
    assert not (tmp_path / "lite2obj-assets.l2oassets").exists()
    captured = capsys.readouterr()
    assert "No hay archivo de assets" in captured.err
    assert "se generará al abrir la app" in captured.err


def test_prepare_assets_fails_without_pack_by_default(tmp_path, monkeypatch, capsys):
    module = _prepare_assets()
    monkeypatch.setattr(module, "TARGET_DIR", tmp_path)
    monkeypatch.setattr(module.paths, "asset_pack", lambda: None)
    monkeypatch.setattr(module.paths, "explicit_client_jar", lambda: None)

    assert module.main([]) == 1
    assert "No hay archivo de assets" in capsys.readouterr().err
