"""Tests del archivo .l2oassets que sustituye al client jar en tiempo de ejecución."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lite2obj import assetpack, assets, paths
from lite2obj.cli import main


@pytest.fixture(autouse=True)
def _clear_asset_pack_cache():
    paths.asset_pack.cache_clear()
    yield
    paths.asset_pack.cache_clear()


def _fake_jar(tmp_path: Path, version: str = "26.3") -> Path:
    jar = tmp_path / "client.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("version.json", json.dumps({"id": version}))
        archive.writestr("assets/minecraft/blockstates/stone.json", b"{}")
        archive.writestr("assets/minecraft/models/block/stone.json", b"{}")
        archive.writestr("assets/minecraft/textures/block/stone.png", b"png")
        archive.writestr("assets/minecraft/textures/block/stone.png.mcmeta", b"{}")
        archive.writestr("assets/minecraft/items/stone.json", b"{}")
        archive.writestr("data/minecraft/painting_variant/kebab.json", b"{}")
        # Ruido que no debe acabar en el archivo de assets.
        archive.writestr("net/minecraft/client/Main.class", b"class")
        archive.writestr("assets/minecraft/sounds/click.ogg", b"ogg")
        archive.writestr("data/minecraft/recipe/stone.json", b"{}")
    return jar


def test_build_asset_pack_keeps_only_render_assets(tmp_path):
    jar = _fake_jar(tmp_path)
    output = tmp_path / "minecraft.l2oassets"

    info = assetpack.build_asset_pack(jar, output)

    assert info.path == output
    assert info.minecraft_version == "26.3"
    assert info.files == 6
    assert info.compressed_bytes == output.stat().st_size
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert assetpack.MANIFEST_NAME in names
    assert "assets/minecraft/blockstates/stone.json" in names
    assert "assets/minecraft/textures/block/stone.png.mcmeta" in names
    assert "data/minecraft/painting_variant/kebab.json" in names
    assert "net/minecraft/client/Main.class" not in names
    assert "assets/minecraft/sounds/click.ogg" not in names
    assert "data/minecraft/recipe/stone.json" not in names


def test_manifest_round_trip_and_validation(tmp_path):
    jar = _fake_jar(tmp_path)
    output = tmp_path / "minecraft.l2oassets"
    assetpack.build_asset_pack(jar, output)

    manifest = assetpack.read_manifest(output)
    assert manifest is not None
    assert manifest["format"] == 1
    assert manifest["minecraft_version"] == "26.3"
    assert manifest["files"] == 6
    assert assetpack.is_asset_pack(output)
    assert not assetpack.is_asset_pack(tmp_path / "no-existe.l2oassets")


def test_build_asset_pack_refuses_to_overwrite_without_force(tmp_path):
    jar = _fake_jar(tmp_path)
    output = tmp_path / "minecraft.l2oassets"
    assetpack.build_asset_pack(jar, output)

    with pytest.raises(FileExistsError):
        assetpack.build_asset_pack(jar, output)
    assetpack.build_asset_pack(jar, output, force=True)


def test_build_asset_pack_rejects_a_non_zip(tmp_path):
    jar = tmp_path / "client.jar"
    jar.write_bytes(b"no soy un zip")

    with pytest.raises(assetpack.AssetPackError):
        assetpack.build_asset_pack(jar, tmp_path / "out.l2oassets")


def test_resource_manager_uses_the_asset_pack_without_minecraft(tmp_path, monkeypatch):
    jar = _fake_jar(tmp_path)
    pack = tmp_path / "minecraft.l2oassets"
    assetpack.build_asset_pack(jar, pack)

    monkeypatch.setattr(paths, "asset_pack", lambda: pack)
    monkeypatch.setattr(paths, "explicit_client_jar", lambda: None)
    monkeypatch.setattr(paths, "client_jar", lambda: None)
    monkeypatch.setattr(assets, "list_packs", lambda: [])

    manager = assets.ResourceManager.discover()

    assert manager.names == ["minecraft"]
    assert manager.read("assets/minecraft/blockstates/stone.json") == b"{}"
    assert manager.read_texture("minecraft:block/stone") is None  # el PNG de prueba no es válido


def test_explicit_client_jar_still_has_priority(tmp_path, monkeypatch):
    jar = _fake_jar(tmp_path)
    pack = tmp_path / "minecraft.l2oassets"
    assetpack.build_asset_pack(jar, pack)

    monkeypatch.setattr(paths, "asset_pack", lambda: pack)
    monkeypatch.setattr(paths, "explicit_client_jar", lambda: jar)
    monkeypatch.setattr(assets, "list_packs", lambda: [])

    manager = assets.ResourceManager.discover()

    assert manager.names == ["client"]


def test_asset_pack_env_override(tmp_path, monkeypatch):
    pack = tmp_path / "personalizado.l2oassets"
    pack.write_bytes(b"")
    monkeypatch.setenv("LITE2OBJ_ASSET_PACK", str(pack))

    assert paths.asset_pack() == pack


def test_bundled_asset_pack_has_priority(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundled = bundle / paths.ASSET_PACK_NAME
    bundled.write_bytes(b"")
    user = tmp_path / "user"
    user.mkdir()
    (user / paths.ASSET_PACK_NAME).write_bytes(b"")
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    monkeypatch.delenv("LITE2OBJ_ASSET_PACK", raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths, "user_data_dir", lambda: user)
    monkeypatch.chdir(cwd)

    assert paths.asset_pack() == bundled


def test_cli_assets_build_and_info(tmp_path, capsys):
    jar = _fake_jar(tmp_path)
    pack = tmp_path / "minecraft.l2oassets"

    assert main(["assets", "build", "--jar", str(jar), "--output", str(pack)]) == 0
    output = capsys.readouterr().out
    assert "Minecraft  26.3" in output
    assert "6 archivos" in output

    assert main(["assets", "info", str(pack)]) == 0
    output = capsys.readouterr().out
    assert "minecraft_version  26.3" in output
