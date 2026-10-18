"""Tests de la API, del pipeline y del formato binario (F6)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_mesh import AIR, _doc, _region, cube_setup

from lite2obj.assets import MemorySource, ResourceManager
from lite2obj.litematic import BlockState
from lite2obj.mesh import Mesh
from lite2obj.pipeline import ALPHA_MODES, RenderOptions, mesh_bundle, mesh_meta, mesh_parts, render
from lite2obj.server import MeshStore, create_app
from lite2obj.textures import TextureAtlas

FIXTURE = Path(__file__).parent / "fixtures" / "store_1x4x4.litematic"


def two_cubes() -> Mesh:
    """Dos cubos contiguos sin culling: 12 caras."""
    builder = cube_setup(cullface=False)
    region = _region([AIR, BlockState("test:cube"), BlockState("test:cube")], [1, 1], (2, 1, 1))
    return builder.build(_doc([region], (2, 1, 1)))


def part_of(parts: list[dict], name: str) -> dict:
    return next(part for part in parts if part["name"] == name)


# --------------------------------------------------------------------------- #
# Formato binario
# --------------------------------------------------------------------------- #


def test_mesh_parts_layout_is_consistent():
    mesh = two_cubes()
    buffer, parts, groups = mesh_parts(mesh)
    assert len(buffer) == sum(part["byteLength"] for part in parts)
    for part in parts:
        assert part["offset"] + part["byteLength"] <= len(buffer)
        assert part["offset"] % 4 == 0
        assert (
            part["byteLength"]
            == part["count"] * np.dtype(part["dtype"]).itemsize * part["components"]
        )
    assert part_of(parts, "index")["count"] == mesh.quad_count * 2 * 3
    assert part_of(parts, "position")["count"] == mesh.vertex_count
    assert sum(group["count"] for group in groups) == mesh.quad_count * 2
    assert [part["name"] for part in parts] == ["position", "normal", "uv", "index"]


def test_mesh_parts_buffer_roundtrips():
    mesh = two_cubes()
    buffer, parts, _ = mesh_parts(mesh)
    position = part_of(parts, "position")
    data = np.frombuffer(
        buffer, dtype=np.float32, count=position["count"] * 3, offset=position["offset"]
    ).reshape(-1, 3)
    assert np.allclose(data, mesh.positions)


def test_mesh_parts_groups_are_ordered_by_alpha():
    atlas = TextureAtlas(ResourceManager([MemorySource({})]))
    source = two_cubes()
    alpha = np.resize(np.array([0, 1, 2], dtype=np.int8), source.quad_count)
    mesh = Mesh(
        positions=source.positions,
        uvs=source.uvs,
        normals=source.normals,
        quads=source.quads,
        sprite=source.sprite,
        alpha_mode=alpha,
        block=source.block,
        palette=source.palette,
        atlas=atlas,
        size=source.size,
    )
    _, parts, groups = mesh_parts(mesh)
    assert [group["mode"] for group in groups] == list(ALPHA_MODES)
    assert sum(group["count"] for group in groups) == part_of(parts, "index")["count"] // 3
    # Los grupos van seguidos y en orden.
    assert [group["start"] for group in groups] == [0, 8, 16]


def test_mesh_bundle_matches_meta():
    mesh = two_cubes()
    buffer, meta = mesh_bundle(mesh)
    assert len(buffer) == sum(part["byteLength"] for part in meta["parts"])
    assert meta["size"] == [2, 1, 1]
    assert meta["atlas"]["width"] > 0
    assert meta["stats"]["quads"] == mesh.quad_count
    assert mesh_meta(mesh)["parts"] == meta["parts"]


# --------------------------------------------------------------------------- #
# MeshStore
# --------------------------------------------------------------------------- #


def test_store_is_lru():
    store = MeshStore(capacity=2)
    mesh = two_cubes()
    first = store.put("a", mesh)
    second = store.put("b", mesh)
    third = store.put("c", mesh)
    assert len(store.list()) == 2
    with pytest.raises(KeyError):
        store.get(first.id)
    assert store.get(second.id).name == "b"
    assert store.get(third.id).summary()["stats"]["quads"] == mesh.quad_count


def test_store_clear():
    store = MeshStore()
    store.put("a", two_cubes())
    store.clear()
    assert store.list() == []


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _has_jar() -> bool:
    from lite2obj import paths

    return paths.client_jar() is not None


def test_health(client):
    body = client.get("/api/health").json()
    assert body["version"]
    assert "client_jar" in body


def test_render_requires_a_source(client):
    assert client.post("/api/render", json={}).status_code == 400


def test_render_missing_file(client):
    assert client.post("/api/render", json={"path": "/no/existe.litematic"}).status_code == 404


def test_render_and_fetch_mesh(client):
    if not _has_jar():
        pytest.skip("no hay client jar")
    body = client.post("/api/render", json={"path": str(FIXTURE)}).json()
    mesh_id = body["mesh_id"]
    assert body["meta"]["size"] == [2, 4, 8]

    meta = client.get(f"/api/mesh/{mesh_id}")
    assert meta.status_code == 200
    assert meta.json()["name"] == "store_1x4x4"

    buffer = client.get(f"/api/mesh/{mesh_id}/buffer")
    assert buffer.status_code == 200
    assert buffer.headers["content-type"] == "application/octet-stream"
    assert len(buffer.content) == sum(part["byteLength"] for part in meta.json()["parts"])

    atlas = client.get(f"/api/mesh/{mesh_id}/atlas.png")
    assert atlas.status_code == 200
    assert atlas.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_mesh_endpoints_404_for_unknown_id(client):
    assert client.get("/api/mesh/nope").status_code == 404
    assert client.get("/api/mesh/nope/buffer").status_code == 404
    assert client.get("/api/mesh/nope/atlas.png").status_code == 404
    assert client.get("/api/mesh/nope/export.obj").status_code == 404


def test_upload_and_render(client):
    if not _has_jar():
        pytest.skip("no hay client jar")
    response = client.post(
        "/api/schematics",
        files={"file": ("cosa.litematic", FIXTURE.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "cosa"

    rendered = client.post("/api/render", json={"upload_id": body["upload_id"]})
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["name"].startswith("subida-")


def test_render_unknown_upload_id(client):
    assert client.post("/api/render", json={"upload_id": "noexiste"}).status_code == 404


def test_export_endpoints(client):
    if not _has_jar():
        pytest.skip("no hay client jar")
    mesh_id = client.post("/api/render", json={"path": str(FIXTURE)}).json()["mesh_id"]
    obj = client.get(f"/api/mesh/{mesh_id}/export.obj")
    assert obj.status_code == 200
    assert b"usemtl atlas" in obj.content
    mtl = client.get(f"/api/mesh/{mesh_id}/export.mtl")
    assert b"newmtl atlas" in mtl.content


def test_schematics_listing(client):
    body = client.get("/api/schematics").json()
    assert "schematics" in body and "cached" in body


def test_packs_listing(client):
    assert isinstance(client.get("/api/packs").json()["packs"], list)


def test_render_invalid_schematic_returns_422(client, tmp_path):
    bad = tmp_path / "malo.litematic"
    bad.write_bytes(b"esto no es nbt")
    assert client.post("/api/render", json={"path": str(bad)}).status_code == 422


def test_render_rejects_missing_jar(client, monkeypatch):
    monkeypatch.setattr("lite2obj.paths.client_jar", lambda: None)
    response = client.post("/api/render", json={"path": str(FIXTURE)})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def test_render_options_map_to_mesh_options():
    options = RenderOptions(packs=["X"], cull=False, tint=False, max_quads=10)
    mesh_options = options.mesh_options()
    assert mesh_options.cull is False
    assert mesh_options.tint is False
    assert mesh_options.max_quads == 10


def test_render_pipeline_directly():
    if not _has_jar():
        pytest.skip("no hay client jar")
    mesh = render(FIXTURE, RenderOptions())
    assert mesh.quad_count > 0
    assert not mesh.atlas.missing
