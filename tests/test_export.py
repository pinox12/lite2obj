"""Tests de exportación OBJ + MTL + atlas (F5)."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from test_mesh import AIR, _doc, _region, cube_setup, cushion_setup

from lite2obj.assets import MemorySource, ResourceManager
from lite2obj.export import ExportOptions, export_obj, obj_geometry
from lite2obj.litematic import BlockState
from lite2obj.mesh import MeshBuilder
from lite2obj.models import ModelResolver


def single_cube_mesh(**options):
    builder = cube_setup(**options) if options else cube_setup()
    region = _region([AIR, BlockState("test:cube")], [1], (1, 1, 1))
    return builder.build(_doc([region], (1, 1, 1))), builder


def read_obj(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def lines_starting(lines, prefix: str) -> list[str]:
    return [line for line in lines if line.startswith(prefix)]


# --------------------------------------------------------------------------- #


def test_obj_contains_geometry_and_material(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path, ExportOptions(name="cosa"))

    lines = read_obj(result.obj)
    assert lines_starting(lines, "v ") != []
    assert lines_starting(lines, "vt ") != []
    assert lines_starting(lines, "vn ") != []
    assert lines_starting(lines, "f ")
    assert "mtllib cosa.mtl" in lines
    assert "usemtl atlas" in lines
    assert result.mtl == tmp_path / "cosa.mtl"
    assert result.atlas == tmp_path / "atlas.png"


def test_obj_cube_has_8_vertices_and_6_faces(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path)
    lines = read_obj(result.obj)
    assert len(lines_starting(lines, "v ")) == 8
    assert len(lines_starting(lines, "vt ")) == 4
    assert len(lines_starting(lines, "vn ")) == 6
    assert len(lines_starting(lines, "f ")) == 6
    assert result.vertices == 8
    assert result.faces == 6


def test_vertices_are_deduplicated(tmp_path):
    # Sin culling hay 12 caras; las esquinas compartidas deben fusionarse.
    builder = cube_setup(cullface=False)
    region = _region(
        [AIR, BlockState("test:cube")] * 1 + [BlockState("test:cube")], [1, 1], (2, 1, 1)
    )
    mesh = builder.build(_doc([region], (2, 1, 1)))
    assert mesh.quad_count == 12
    result = export_obj(mesh, tmp_path)
    assert result.vertices == 12  # 2 cubos contiguos comparten 4 esquinas
    assert result.vertices < mesh.vertex_count


def test_positions_have_no_floating_point_noise(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path, ExportOptions(name="m"))
    for line in lines_starting(read_obj(result.obj), "v "):
        for token in line.split()[1:]:
            assert "e" not in token.lower()
            assert not token.startswith("-0") or token == "-0"


def test_flip_v_option(tmp_path):
    mesh, _ = single_cube_mesh()
    flipped = obj_geometry(mesh, ExportOptions(flip_v=True))[1]
    plain = obj_geometry(mesh, ExportOptions(flip_v=False))[1]
    assert np.allclose(flipped[:, 1], 1.0 - plain[:, 1])


def test_up_axis_z_rotates_positions(tmp_path):
    mesh, _ = single_cube_mesh()
    positions_z = obj_geometry(mesh, ExportOptions(up_axis="z"))[0]
    assert positions_z[:, 1].max() == pytest.approx(1.0)  # altura original en Z
    assert positions_z[:, 2].min() == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        obj_geometry(mesh, ExportOptions(up_axis="w"))


def test_scale_option(tmp_path):
    mesh, _ = single_cube_mesh()
    positions = obj_geometry(mesh, ExportOptions(scale=16.0))[0]
    assert positions.max() == pytest.approx(16.0)


def test_atlas_file_is_a_valid_png(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path)
    assert result.atlas is not None
    with Image.open(result.atlas) as image:
        assert image.format == "PNG"
        assert image.size == mesh.atlas.size


def test_mtl_references_the_atlas(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path)
    assert result.mtl is not None
    text = result.mtl.read_text(encoding="utf-8")
    assert "newmtl atlas" in text
    assert "map_Kd atlas.png" in text
    assert "map_d atlas.png" in text
    assert "illum 1" in text


def test_atlas_and_mtl_can_be_skipped(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path, ExportOptions(write_atlas=False, write_mtl=False))
    assert result.mtl is None and result.atlas is None
    assert "mtllib" not in result.obj.read_text(encoding="utf-8")


def test_group_by_block_emits_groups(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path, ExportOptions(group_by="block"))
    assert "g test_cube" in result.obj.read_text(encoding="utf-8")


def test_cushion_entities_are_included_in_export(tmp_path):
    builder = cushion_setup("cyan")
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:cushion",
            "Pos": [0.5, 0.0, 0.5],
            "Rotation": [180.0, 0.0],
            "color": "cyan",
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))
    result = export_obj(mesh, tmp_path, ExportOptions(group_by="block"))

    lines = read_obj(result.obj)
    assert result.faces == 6
    assert len(lines_starting(lines, "f ")) == 6
    assert "g minecraft_cushion" in lines


def test_group_by_alpha_emits_groups(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path, ExportOptions(group_by="alpha"))
    assert "g opaque" in result.obj.read_text(encoding="utf-8")


def test_group_by_unknown_raises(tmp_path):
    mesh, _ = single_cube_mesh()
    with pytest.raises(ValueError):
        export_obj(mesh, tmp_path, ExportOptions(group_by="color"))


def test_face_indices_are_in_range(tmp_path):
    mesh, _ = single_cube_mesh()
    result = export_obj(mesh, tmp_path)
    lines = read_obj(result.obj)
    vertex_count = len(lines_starting(lines, "v "))
    uv_count = len(lines_starting(lines, "vt "))
    normal_count = len(lines_starting(lines, "vn "))
    for line in lines_starting(lines, "f "):
        for token in line.split()[1:]:
            vertex, uv, normal = (int(part) for part in token.split("/"))
            assert 1 <= vertex <= vertex_count
            assert 1 <= uv <= uv_count
            assert 1 <= normal <= normal_count


def test_faces_are_ccw_when_seen_from_outside(tmp_path):
    mesh, _ = single_cube_mesh()
    positions, _, normals, position_index, _, normal_index = obj_geometry(
        mesh, ExportOptions(flip_v=False)
    )
    for quad, normal in zip(position_index, normals[normal_index - 1], strict=True):
        a, b, c = positions[quad[:3] - 1]
        cross = np.cross(b - a, c - a)
        assert np.dot(cross, normal) > 0


def test_export_result_as_dict(tmp_path):
    mesh, _ = single_cube_mesh()
    data = export_obj(mesh, tmp_path).as_dict()
    assert set(data) == {"obj", "mtl", "atlas", "vertices", "uvs", "normals", "faces", "bytes"}
    assert data["faces"] == 6


# --------------------------------------------------------------------------- #


def test_real_fixture_golden_counts(tmp_path):
    """Conteos fijos del fixture con vanilla 26.3: cazan regresiones de malla y export.

    Si cambia la versión del jar esta prueba hay que actualizarla a mano (es su objetivo).
    """
    from _jar import jar_resources

    from lite2obj import litematic as litematic_module

    doc = litematic_module.load("tests/fixtures/store_1x4x4.litematic")
    mesh = MeshBuilder(ModelResolver(jar_resources())).build(doc)
    assert (mesh.quad_count, mesh.vertex_count) == (436, 1744)
    assert len(mesh.palette) == 19
    assert mesh.atlas.sprite_count == 13
    assert mesh.atlas.size == (256, 128)

    positions, uvs, normals, faces, _, _ = obj_geometry(mesh, ExportOptions())
    assert (len(positions), len(uvs), len(normals), len(faces)) == (448, 209, 6, 436)

    result = export_obj(mesh, tmp_path, ExportOptions(name="store"))
    assert result.vertices == 448
    assert result.faces == 436


def test_real_fixture_export_roundtrip(tmp_path):
    from _jar import jar_resources

    from lite2obj import litematic as litematic_module

    doc = litematic_module.load("tests/fixtures/store_1x4x4.litematic")
    mesh = MeshBuilder(ModelResolver(jar_resources())).build(doc)
    result = export_obj(mesh, tmp_path, ExportOptions(name="store", group_by="block"))

    lines = read_obj(result.obj)
    assert len(lines_starting(lines, "f ")) == mesh.quad_count
    assert result.faces == mesh.quad_count
    assert result.vertices < mesh.vertex_count
    assert result.bytes_written > 0
    with Image.open(result.atlas) as image:
        assert image.size == mesh.atlas.size
        assert image.mode == "RGBA"


@pytest.mark.parametrize("group_by", ["none", "block", "alpha"])
def test_group_modes_keep_every_face(tmp_path, group_by):
    from _jar import jar_resources

    from lite2obj import litematic as litematic_module

    doc = litematic_module.load("tests/fixtures/store_1x4x4.litematic")
    mesh = MeshBuilder(ModelResolver(jar_resources())).build(doc)
    result = export_obj(mesh, tmp_path, ExportOptions(group_by=group_by))
    assert len(lines_starting(read_obj(result.obj), "f ")) == mesh.quad_count


def test_empty_mesh_exports_cleanly(tmp_path):
    from lite2obj.mesh import Mesh
    from lite2obj.textures import TextureAtlas

    atlas = TextureAtlas(ResourceManager([MemorySource({})]))
    mesh = Mesh(
        positions=np.zeros((0, 3), np.float32),
        uvs=np.zeros((0, 2), np.float32),
        normals=np.zeros((0, 3), np.float32),
        quads=np.zeros((0, 4), np.int32),
        sprite=np.zeros(0, np.int32),
        alpha_mode=np.zeros(0, np.int8),
        block=np.zeros(0, np.int32),
        palette=[],
        atlas=atlas,
        size=(1, 1, 1),
    )
    result = export_obj(mesh, tmp_path)
    assert result.faces == 0
    assert lines_starting(read_obj(result.obj), "f ") == []
    with Image.open(BytesIO(result.atlas.read_bytes())) as image:
        assert image.size == (1, 1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
