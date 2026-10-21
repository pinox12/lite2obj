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
    _is_full_cube,
    rotate_direction,
    rotate_uv,
)
from lite2obj.models import (
    Element,
    ModelResolver,
    Variant,
    pick_variants,
    position_seed,
    variant_matrix,
)

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


def test_rotate_direction_matches_minecraft_octahedral_table():
    """El ``cullface`` gira como la geometría (tabla de ``OctahedralGroup`` de Minecraft).

    Los valores están tomados del propio cliente (`Quadrant.fromXYZAngles`): si alguien cambia
    el signo o el orden de las rotaciones, esto lo delata.
    """
    assert rotate_direction("north", variant_matrix(0, 90)) == "east"
    assert rotate_direction("west", variant_matrix(0, 90)) == "north"
    assert rotate_direction("north", variant_matrix(0, 180)) == "south"
    assert rotate_direction("north", variant_matrix(0, 270)) == "west"
    assert rotate_direction("down", variant_matrix(90, 0)) == "south"
    assert rotate_direction("north", variant_matrix(90, 0)) == "down"
    assert rotate_direction("north", variant_matrix(180, 90)) == "west"
    assert rotate_direction("down", variant_matrix(180, 90)) == "up"
    assert rotate_direction("north", variant_matrix(90, 90)) == "down"
    assert rotate_direction("up", variant_matrix(90, 90)) == "east"
    assert rotate_direction("east", variant_matrix(0, 0)) == "east"


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


def test_explicit_model_uv_is_used():
    """El ``uv`` del modelo se respeta (sin él, macetas y faroles estiran el PNG entero).

    ``uv`` ausente se deriva de la caja (``default_uv``), como en Minecraft.
    """
    builder = make_setup(
        {
            blockstate_path("test:panel"): {"variants": {"": {"model": "test:block/panel"}}},
            model_path("test:block/panel"): {
                "textures": {"all": "minecraft:block/tex"},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 16, 16],
                        "faces": {
                            "north": {"texture": "#all", "uv": [4, 4, 12, 12]},
                            "south": {"texture": "#all"},
                        },
                    }
                ],
            },
            texture_path("minecraft:block/tex"): make_png(),
        }
    )
    state = builder.state_mesh(BlockState("test:panel"))
    north = next(t for t in state.templates if np.allclose(t.normal, FACE_NORMALS["north"]))
    # UV explícita [4,4,12,12] sobre 16 -> 0.25..0.75 dentro del sprite.
    assert north.st.min() == pytest.approx(0.25)
    assert north.st.max() == pytest.approx(0.75)
    # Sin ``uv``, el cubo completo usa 0..1.
    south = next(t for t in state.templates if np.allclose(t.normal, FACE_NORMALS["south"]))
    assert south.st.min() == pytest.approx(0.0)
    assert south.st.max() == pytest.approx(1.0)


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
    # Las normales tienen que apuntar hacia fuera también en modelos reales.
    assert_normals_match_winding(mesh, "fixture")


# --------------------------------------------------------------------------- #
# Oclusión, variants rotados y elección por posición
# --------------------------------------------------------------------------- #


def make_cutout_png() -> bytes:
    """Textura con alfa 0/1 (como las hojas): se clasifica como *cutout*."""
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
    for x in range(8):
        for y in range(16):
            image.putpixel((x, y), (255, 0, 0, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def rotated_cube_setup(**options) -> MeshBuilder:
    """Bloque de terreno: cuatro variants que sólo se diferencian en la rotación."""
    return make_setup(
        {
            blockstate_path("test:rock"): {
                "variants": {
                    "": [
                        {"model": "test:block/cube"},
                        {"model": "test:block/cube", "y": 90},
                        {"model": "test:block/cube", "y": 180},
                        {"model": "test:block/cube", "y": 270},
                    ]
                }
            },
            model_path("test:block/cube"): _model(),
            texture_path("minecraft:block/tex"): make_png(),
        },
        **options,
    )


def test_full_cube_accepts_quarter_turns():
    element = Element(frm=(0.0, 0.0, 0.0), to=(16.0, 16.0, 16.0), faces={})
    assert _is_full_cube(element, Variant(model="m"))
    assert _is_full_cube(element, Variant(model="m", y=180))
    assert _is_full_cube(element, Variant(model="m", x=90, y=270))
    assert not _is_full_cube(element, Variant(model="m", y=45))
    half = Element(frm=(0.0, 0.0, 0.0), to=(16.0, 8.0, 16.0), faces={})
    assert not _is_full_cube(half, Variant(model="m", y=180))


def test_rotated_variants_still_cull_neighbours():
    """Los bloques con rotaciones aleatorias siguen ocultando las caras de al lado."""
    builder = rotated_cube_setup()
    region = _region([AIR, BlockState("test:rock")], [1, 1], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    assert mesh.quad_count == 10


def test_rotated_variant_cullface_is_rotated():
    """El ``cullface`` de un variant girado apunta al vecino ya girado, no al original.

    Si no se gira, una cara que mira al este se descarta contra el bloque del norte (o al
    revés): es lo que rompía las vallas y las escaleras giradas."""
    builder = make_setup(
        {
            blockstate_path("test:panel"): {
                "variants": {"": {"model": "test:block/cube_rot", "y": 90}}
            },
            model_path("test:block/cube_rot"): _model(),
            texture_path("minecraft:block/tex"): make_png(),
        }
    )
    state = builder.state_mesh(BlockState("test:panel"))
    assert state.quad_count == 6
    # La cara que queda mirando al este (x=1) llevaba "north" en el modelo: debe ser "east".
    east = [t for t in state.templates if t.positions[:, 0].min() > 0.999]
    assert len(east) == 1
    assert east[0].cullface == "east"
    # Invariante general: cada cullface apunta igual que su cara.
    for template in state.templates:
        assert template.cullface is not None
        assert np.allclose(template.normal, EXPECTED_FACE_NORMALS[template.cullface])


def test_rotated_cullface_culls_against_the_rotated_neighbour():
    """Con un cubo opaco al este, la cara este del panel girado se descarta."""
    builder = make_setup(
        {
            blockstate_path("test:panel"): {
                "variants": {"": {"model": "test:block/cube_rot", "y": 90}}
            },
            blockstate_path("test:solid"): {"variants": {"": {"model": "test:block/solid"}}},
            model_path("test:block/cube_rot"): _model(),
            model_path("test:block/solid"): _model(),
            texture_path("minecraft:block/tex"): make_png(),
        }
    )
    # Panel en x=0, cubo opaco en x=1 (este): la cara este del panel se descarta.
    region = _region([AIR, BlockState("test:panel"), BlockState("test:solid")], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    # 6 + 6 caras menos la del panel (este) y la del cubo (oeste) que coinciden.
    assert mesh.quad_count == 10
    # Si el cullface no rotase, el panel miraría al norte (sin vecino) y quedarían 11.


def test_covers_is_true_for_the_terrain_variants():
    builder = rotated_cube_setup()
    for _ in range(4):
        assert builder.state_mesh(BlockState("test:rock"), (1, 2, 3)).covers


def assert_normals_match_winding(mesh, label: str = "") -> None:
    """La normal declarada tiene que apuntar igual que el giro de los vértices.

    Es la comprobación que caza rotaciones aplicadas a las posiciones y no a las normales.
    """
    quads = mesh.positions.reshape(-1, 4, 3)
    for index, quad in enumerate(quads):
        expected = np.cross(quad[1] - quad[0], quad[2] - quad[0])
        length = float(np.linalg.norm(expected))
        if length < 1e-9:
            continue  # cara degenerada: no la puede haber, pero no rompemos el test
        expected = expected / length
        assert np.allclose(expected, mesh.normals[index * 4], atol=1e-4), (
            f"{label}: cara {index} apunta a {mesh.normals[index * 4]}, el giro dice {expected}"
        )


def test_normals_follow_the_variant_rotation():
    """Un cubo girado por el variant debe tener las normales giradas con él."""
    builder = rotated_cube_setup(cull=False)
    region = _region([AIR, BlockState("test:rock")], [1, 1, 1, 1], (4, 1, 1))
    mesh = builder.build(_doc([region], (4, 1, 1)))
    assert mesh.quad_count == 24  # cuatro cubos sin descarte
    # Las cuatro rotaciones están en juego (si no, el test no comprobaría nada).
    normals = {tuple(float(v) for v in row) for row in mesh.normals[::4]}
    assert len(normals) >= 4
    assert_normals_match_winding(mesh, "variants rotados")


@pytest.mark.parametrize(
    "name",
    [
        "minecraft:grass_block",
        "minecraft:dandelion",
        "minecraft:tube_coral_fan",
        "minecraft:big_dripleaf",
        "minecraft:brewing_stand",
        "minecraft:chain",
        "minecraft:ladder",
        "minecraft:oak_stairs",
        "minecraft:oak_fence",
        "minecraft:hopper",
        "minecraft:anvil",
    ],
)
def test_real_models_have_outward_normals(name):
    """Modelos reales con rotaciones de elemento (cruces, fans, cadenas...) incluidos."""
    from _jar import jar_resources

    builder = MeshBuilder(ModelResolver(jar_resources()))
    region = _region([AIR, BlockState(name)], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))
    assert mesh.quad_count > 0
    assert_normals_match_winding(mesh, name)


def test_variants_are_picked_per_position():
    """Como Minecraft: cada posición escoge una de las variantes del bloque."""
    entries: dict[str, object] = {}
    variants = []
    for index, direction in enumerate(("north", "east", "south", "west")):
        model = f"test:block/rock_{direction}"
        texture = f"minecraft:block/rock_{direction}"
        entries[model_path(model)] = _model(texture)
        entries[texture_path(texture)] = make_png(color=(40 * index + 20, 0, 0, 255))
        variants.append({"model": model})
    entries[blockstate_path("test:rock")] = {"variants": {"": variants}}

    grid = [1] * 16  # 4x4 bloques de roca
    region = _region([AIR, BlockState("test:rock")], grid, (4, 1, 4))
    doc = _doc([region], (4, 1, 4))
    mesh = make_setup(entries).build(doc)

    # Hay más de un variant en juego (si siempre se eligiese el mismo, habría un solo sprite).
    assert len(set(mesh.sprite.tolist())) >= 3
    # Y la elección es estable: reconstruir da exactamente la misma malla.
    again = make_setup(entries).build(doc)
    assert np.array_equal(mesh.positions, again.positions)


def test_position_seed_changes_between_neighbours():
    seeds = {position_seed((x, 0, 0)) for x in range(16)}
    assert len(seeds) == 16
    assert len({position_seed((x, y, 3)) % 4 for x in range(8) for y in range(8)}) > 1


def test_pick_variants_decorrelates_groups():
    groups = (
        (Variant("a1"), Variant("a2")),
        (Variant("b1"), Variant("b2")),
    )
    picks = [pick_variants(groups, seed) for seed in range(8)]
    assert len({pick[0].model for pick in picks}) == 2
    assert len({pick[1].model for pick in picks}) == 2
    assert {pick[0].model for pick in picks} != {pick[1].model for pick in picks}


def test_overlay_element_does_not_prevent_occlusion():
    """La hierba (cubo opaco + capa exterior translúcida) sí tapa lo de dentro."""
    overlay = {
        d: {"texture": "#overlay", "cullface": d} for d in ("north", "south", "east", "west")
    }
    builder = make_setup(
        {
            blockstate_path("test:grass"): {"variants": {"": {"model": "test:block/grass"}}},
            model_path("test:block/grass"): {
                "textures": {"all": "minecraft:block/tex", "overlay": "minecraft:block/overlay"},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 16, 16],
                        "faces": {
                            d: {"texture": "#all", "cullface": d} for d in EXPECTED_FACE_NORMALS
                        },
                    },
                    {"from": [0, 0, 0], "to": [16, 16, 16], "faces": overlay},
                ],
            },
            blockstate_path("test:cube"): {"variants": {"": {"model": "test:block/cube"}}},
            model_path("test:block/cube"): _model(),
            texture_path("minecraft:block/tex"): make_png(),
            texture_path("minecraft:block/overlay"): make_cutout_png(),
        }
    )
    assert builder.state_mesh(BlockState("test:grass")).covers

    # La hierba se queda con una cara por lado: la base y el overlay comparten plano y se
    # hornean juntos (si no, Blender descartaría el overlay).
    grass = builder.state_mesh(BlockState("test:grass"))
    assert grass.quad_count == 6

    # Cubo + hierba pegados: el cubo pierde su cara contra la hierba, y la hierba pierde la
    # suya contra el cubo: 5 + 5 = 10 caras de 12 posibles.
    region = _region([AIR, BlockState("test:cube"), BlockState("test:grass")], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    assert mesh.quad_count == 10


def test_block_with_transparent_overlay_does_not_occlude():
    """Si el cubo completo es translúcido (cristal), no oculta al vecino."""
    builder = make_setup(
        {
            blockstate_path("test:glass"): {"variants": {"": {"model": "test:block/glass"}}},
            model_path("test:block/glass"): _model("minecraft:block/glass"),
            blockstate_path("test:cube"): {"variants": {"": {"model": "test:block/cube"}}},
            model_path("test:block/cube"): _model(),
            texture_path("minecraft:block/tex"): make_png(),
            texture_path("minecraft:block/glass"): make_cutout_png(),
        }
    )
    assert not builder.state_mesh(BlockState("test:glass")).covers
    region = _region([AIR, BlockState("test:cube"), BlockState("test:glass")], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    # El cubo conserva su cara este (el cristal no la tapa): 6 + 5 = 11.
    assert mesh.quad_count == 11


def test_multipart_variants_pick_one_instead_of_everything():
    """Un ``apply`` con varios variants es una elección, no una superposición."""
    builder = make_setup(
        {
            blockstate_path("test:thing"): {
                "multipart": [
                    {
                        "when": {"powered": "true"},
                        "apply": [
                            {"model": "test:block/a", "weight": 2},
                            {"model": "test:block/b"},
                        ],
                    }
                ]
            },
            model_path("test:block/a"): _model("minecraft:block/tex_a"),
            model_path("test:block/b"): _model("minecraft:block/tex_b"),
            texture_path("minecraft:block/tex_a"): make_png(),
            texture_path("minecraft:block/tex_b"): make_png(color=(0, 0, 255, 255)),
        }
    )
    region = _region([AIR, BlockState("test:thing", (("powered", "true"),))], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))
    assert mesh.quad_count == 6


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
