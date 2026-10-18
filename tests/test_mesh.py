"""Tests de generación de malla (F4).

Se comprueba en particular la orientación de caras y UV contra la convención documentada del
formato de modelos de Minecraft, que es la parte más fácil de equivocar.
"""

from __future__ import annotations

import json
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from lite2obj.assets import MemorySource, ResourceManager, blockstate_path, model_path, texture_path
from lite2obj.litematic import BlockState, Litematic, Region
from lite2obj.mesh import (
    FACE_CORNERS,
    FACE_NORMALS,
    FACE_UV_ORDER,
    UV_CORNERS,
    MeshBuilder,
    MeshOptions,
    rotate_uv,
)
from lite2obj.models import ModelResolver

EXPECTED_FACE_NORMALS = {
    "up": (0, 1, 0),
    "down": (0, -1, 0),
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}


def make_png(width: int = 16, height: int = 16, color=(255, 255, 255, 255)) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _model(texture: str = "minecraft:block/tex", *, cullface: bool = True) -> dict:
    faces = {}
    for direction in EXPECTED_FACE_NORMALS:
        face = {"texture": "#all"}
        if cullface:
            face["cullface"] = direction
        faces[direction] = face
    return {
        "textures": {"all": texture},
        "elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": faces}],
    }


def make_setup(entries: dict[str, object], **options) -> MeshBuilder:
    encoded: dict[str, bytes] = {}
    for path, value in entries.items():
        encoded[path] = value if isinstance(value, bytes) else json.dumps(value).encode()
    resolver = ModelResolver(ResourceManager([MemorySource(encoded)]))
    return MeshBuilder(resolver, options=MeshOptions(**options) if options else None)


def cube_setup(texture: str = "minecraft:block/tex", *, cullface: bool = True) -> MeshBuilder:
    return make_setup(
        {
            blockstate_path("test:cube"): {"variants": {"": {"model": "test:block/cube"}}},
            model_path("test:block/cube"): _model(texture, cullface=cullface),
            texture_path(texture): make_png(),
        }
    )


# --------------------------------------------------------------------------- #
# Orientación de caras
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("direction", sorted(EXPECTED_FACE_NORMALS))
def test_face_corners_are_ccw_with_outward_normal(direction):
    # Cubo unidad: esquinas según Element.corners.
    corners = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    quad = corners[list(FACE_CORNERS[direction])]
    normal = np.cross(quad[1] - quad[0], quad[2] - quad[0])
    normal = normal / np.linalg.norm(normal)
    assert np.allclose(normal, EXPECTED_FACE_NORMALS[direction], atol=1e-9)
    # La cuarta esquina cierra el cuadrilátero (es coplanaria y ordenada).
    assert (
        np.allclose(
            corners[list(FACE_CORNERS[direction])][0], corners[list(FACE_CORNERS[direction])][3]
        )
        is False
    )


def test_face_normals_table():
    assert {k: tuple(float(v) for v in n) for k, n in EXPECTED_FACE_NORMALS.items()} == FACE_NORMALS


def test_face_uv_order_matches_minecraft_conventions():
    # Cubo unidad 0..16 y UV explícita [0,0,16,16]: comprobamos la asignación (u,v) de cada
    # vértice según los ejes documentados de cada cara.
    corners = np.array(
        [
            [0, 0, 0],
            [16, 0, 0],
            [16, 16, 0],
            [0, 16, 0],
            [0, 0, 16],
            [16, 0, 16],
            [16, 16, 16],
            [0, 16, 16],
        ],
        dtype=float,
    )
    expectations = {
        # dirección: (u en función de, v en función de)
        "up": (lambda p: p[0] / 16, lambda p: p[2] / 16),
        "down": (lambda p: p[0] / 16, lambda p: 1 - p[2] / 16),
        "north": (lambda p: 1 - p[0] / 16, lambda p: 1 - p[1] / 16),
        "south": (lambda p: p[0] / 16, lambda p: 1 - p[1] / 16),
        "west": (lambda p: p[2] / 16, lambda p: 1 - p[1] / 16),
        "east": (lambda p: 1 - p[2] / 16, lambda p: 1 - p[1] / 16),
    }
    for direction, (u_of, v_of) in expectations.items():
        order = FACE_UV_ORDER[direction]
        for slot, corner_index in enumerate(FACE_CORNERS[direction]):
            position = corners[corner_index]
            s, t = UV_CORNERS[order[slot]]
            assert s == pytest.approx(u_of(position)), f"{direction} u del vértice {slot}"
            assert t == pytest.approx(v_of(position)), f"{direction} v del vértice {slot}"


def test_rotate_uv():
    assert rotate_uv(0.0, 0.0, 0) == (0.0, 0.0)
    assert rotate_uv(0.0, 0.0, 90) == (0.0, 1.0)
    assert rotate_uv(0.0, 0.0, 180) == (1.0, 1.0)
    assert rotate_uv(0.0, 0.0, 270) == (1.0, 0.0)
    assert rotate_uv(0.25, 0.5, 360) == (0.25, 0.5)


# --------------------------------------------------------------------------- #
# Plantillas de estado
# --------------------------------------------------------------------------- #


def test_state_mesh_has_six_quads_for_a_cube():
    builder = cube_setup()
    state = builder.state_mesh(BlockState("test:cube"))
    assert state.quad_count == 6
    assert state.covers
    builder.atlas.build()
    state.finalize(builder.atlas)
    assert state.uvs.shape == (6, 4, 2)


def test_state_mesh_uvs_are_inside_the_sprite():
    builder = cube_setup()
    state = builder.state_mesh(BlockState("test:cube"))
    atlas = builder.atlas.build()
    state.finalize(atlas)
    assert np.all(state.uvs >= 0.0) and np.all(state.uvs <= 1.0)
    # El sprite es pequeño respecto al atlas, así que las UV quedan en un rango estrecho.
    assert np.ptp(state.uvs[:, :, 0]) <= 0.5


def test_translucent_block_does_not_cover_neighbours():
    builder = make_setup(
        {
            blockstate_path("test:glass"): {"variants": {"": {"model": "test:block/glass"}}},
            model_path("test:block/glass"): _model("minecraft:block/glass"),
            texture_path("minecraft:block/glass"): make_png(color=(255, 255, 255, 100)),
        }
    )
    state = builder.state_mesh(BlockState("test:glass"))
    assert not state.covers
    assert all(template.alpha_mode != 0 for template in state.templates)


# --------------------------------------------------------------------------- #
# Construcción sobre esquemáticos sintéticos
# --------------------------------------------------------------------------- #


def _doc(doc_regions, size) -> Litematic:
    return Litematic(
        name="t",
        author="",
        description="",
        regions=doc_regions,
        total_blocks=0,
    )


def _region(palette, grid_yzx, size, position=(0, 0, 0)) -> Region:
    sy, sz, sx = size[1], size[2], size[0]
    return Region(
        name="r",
        origin=position,
        size=(sx, sy, sz),
        palette=palette,
        grid=np.array(grid_yzx, dtype=np.int32).reshape(sy, sz, sx),
        air_mask=np.array([b.is_air for b in palette], dtype=bool),
    )


AIR = BlockState("minecraft:air")


def test_single_cube_mesh_has_six_quads():
    builder = cube_setup()
    region = _region([AIR, BlockState("test:cube")], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))
    assert mesh.quad_count == 6
    assert mesh.vertex_count == 24
    assert mesh.positions.min() >= 0.0
    assert mesh.positions.max() == pytest.approx(1.0)
    assert mesh.atlas.image.size == builder.atlas.size


def test_neighbouring_full_cubes_cull_shared_faces():
    builder = cube_setup()
    region = _region([AIR, BlockState("test:cube")], [1, 1], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    # 6 + 6 - 2 caras compartidas = 10.
    assert mesh.quad_count == 10


def test_culling_can_be_disabled():
    builder = cube_setup(cullface=False)
    region = _region([AIR, BlockState("test:cube")], [1, 1], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    assert mesh.quad_count == 12


def test_slab_does_not_cull_the_cube_behind_it():
    # Una losa no es un cubo completo: no debe ocultar la cara del vecino.
    builder = make_setup(
        {
            blockstate_path("test:cube"): {"variants": {"": {"model": "test:block/cube"}}},
            model_path("test:block/cube"): _model(),
            blockstate_path("test:slab"): {"variants": {"": {"model": "test:block/slab"}}},
            model_path("test:block/slab"): {
                "textures": {"all": "minecraft:block/tex"},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 8, 16],
                        "faces": {
                            d: {"texture": "#all", "cullface": d} for d in EXPECTED_FACE_NORMALS
                        },
                    }
                ],
            },
            texture_path("minecraft:block/tex"): make_png(),
        }
    )
    region = _region([AIR, BlockState("test:cube"), BlockState("test:slab")], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    # El cubo conserva su cara este (la losa no la oculta) pero la losa sí pierde su cara
    # oeste contra el cubo: 6 + 6 - 1 = 11.
    assert mesh.quad_count == 11


def test_mesh_places_blocks_at_world_coordinates_minus_origin():
    builder = cube_setup(
        cullface=False,
    )
    region = _region([AIR, BlockState("test:cube")], [1], (1, 1, 1), position=(5, 7, 9))
    mesh = builder.build(_doc([region], (1, 1, 1)))
    assert mesh.origin == (5, 7, 9)
    assert mesh.positions.min() == pytest.approx(0.0)
    assert mesh.positions.max() == pytest.approx(1.0)


def test_max_quads_marks_partial_mesh():
    builder = cube_setup(cullface=False)
    region = _region([AIR, BlockState("test:cube")], [1, 1, 1, 1], (2, 1, 2))
    mesh = builder.build(_doc([region], (2, 1, 2)))
    assert mesh.quad_count == 24

    builder = cube_setup(cullface=False)
    builder.options.max_quads = 5
    mesh = builder.build(_doc([region], (2, 1, 2)))
    assert mesh.quad_count == 5
    assert mesh.partial


def test_progress_callback_is_called():
    calls: list[tuple[int, int]] = []
    builder = cube_setup()
    builder.options.progress = lambda done, total: calls.append((done, total))
    region = _region(
        [AIR, BlockState("test:cube")] * 1 + [BlockState("test:cube")], [1, 1], (2, 1, 1)
    )
    builder.build(_doc([region], (2, 1, 1)))
    assert calls
    assert calls[-1][0] == calls[-1][1]


def test_triangle_indices_are_valid():
    builder = cube_setup()
    region = _region([AIR, BlockState("test:cube")], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))
    triangles = mesh.triangle_indices()
    assert triangles.shape == (12, 3)
    assert triangles.max() < mesh.vertex_count
    # Dos triángulos por cara, con la misma normal hacia fuera.
    assert mesh.normals.shape == (24, 3)


def test_stats_reports_top_blocks():
    builder = cube_setup()
    region = _region(
        [AIR, BlockState("test:cube")] * 1 + [BlockState("test:cube")], [1, 1], (2, 1, 1)
    )
    mesh = builder.build(_doc([region], (2, 1, 1)))
    stats = mesh.stats()
    assert stats["quads"] == mesh.quad_count
    assert stats["top_blocks"][0][0] == "test:cube"


# --------------------------------------------------------------------------- #
# Integración con el jar real y un esquemático real
# --------------------------------------------------------------------------- #


def test_real_fixture_mesh():
    from _jar import jar_resources

    from lite2obj import litematic as litematic_module
    from lite2obj.paths import find_schematics

    del find_schematics
    fixture = "tests/fixtures/store_1x4x4.litematic"
    doc = litematic_module.load(fixture)
    builder = MeshBuilder(ModelResolver(jar_resources()))
    mesh = builder.build(doc)

    assert mesh.quad_count > 0
    assert not mesh.partial
    assert mesh.size == (2, 4, 8)
    assert mesh.positions.min(axis=0) == pytest.approx([0.0, 0.0, 0.0])
    assert mesh.positions.max(axis=0) == pytest.approx([2.0, 4.0, 8.0])
    assert not mesh.atlas.missing
    # Todas las UV dentro del atlas.
    assert mesh.uvs.min() >= 0.0
    assert mesh.uvs.max() <= 1.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
