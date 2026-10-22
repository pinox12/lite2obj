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
    _block_prop,
    _is_full_cube,
    _recompute_connections,
    rotate_direction,
    rotate_uv,
    rotate_uv_about,
    uvlock_rotation,
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


def make_cushion_png(color=(255, 0, 0, 255)) -> bytes:
    """PNG 64x64 con sólo las primeras 20 filas usadas, como el de una entidad cojín."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(20):
        for x in range(64):
            image.putpixel((x, y), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
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


def cushion_setup(*colors: str) -> MeshBuilder:
    """Builder sin bloques, con las texturas de entidad de los colores indicados."""
    entries = {
        texture_path(f"minecraft:entity/cushion/{color}_cushion"): make_cushion_png()
        for color in colors
    }
    return make_setup(entries)


def _pane_model(direction: str) -> dict:
    """Media vidriera con el canto en ``direction`` y dos caras laterales."""
    if direction == "north":
        frm, to = [7, 0, 0], [9, 16, 7]
    else:
        frm, to = [7, 0, 9], [9, 16, 16]
    faces = {
        "west": {"texture": "#all"},
        "east": {"texture": "#all"},
        direction: {"texture": "#all", "cullface": direction},
    }
    return {
        "textures": {"all": "minecraft:block/tex"},
        "elements": [{"from": frm, "to": to, "faces": faces}],
    }


def pane_setup(*names: str) -> MeshBuilder:
    """Dos medias vidrieras (norte/sur) para probar el descarte de la junta."""
    entries: dict[str, object] = {
        model_path("test:block/pane_north"): _pane_model("north"),
        model_path("test:block/pane_south"): _pane_model("south"),
        texture_path("minecraft:block/tex"): make_png(),
    }
    for name in names:
        entries[blockstate_path(name)] = {
            "multipart": [
                {"when": {"north": "true"}, "apply": {"model": "test:block/pane_north"}},
                {"when": {"south": "true"}, "apply": {"model": "test:block/pane_south"}},
            ]
        }
    return make_setup(entries)


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


def test_rotate_uv_about_texture_center():
    # Minecraft gira las UV absolutas alrededor del píxel (8,8), no del centro de la cara.
    assert rotate_uv_about(0.0, 8.0, 180) == (16.0, 8.0)
    assert rotate_uv_about(16.0, 8.0, 180) == (0.0, 8.0)
    assert rotate_uv_about(0.0, 16.0, 180) == (16.0, 0.0)
    assert rotate_uv_about(16.0, 16.0, 180) == (0.0, 0.0)
    assert rotate_uv_about(4.0, 8.0, 90) == (8.0, 4.0)
    assert rotate_uv_about(4.0, 8.0, 360) == (4.0, 8.0)


@pytest.mark.parametrize(
    ("direction", "x", "y", "expected"),
    [
        # ``x: 180`` es el caso de las escaleras boca abajo: gira las cuatro caras
        # laterales, pero no las de arriba/abajo.
        ("north", 180, 0, 180.0),
        ("south", 180, 0, 180.0),
        ("east", 180, 0, 180.0),
        ("west", 180, 0, 180.0),
        ("up", 180, 0, 0.0),
        ("down", 180, 0, 0.0),
        # Un ``y`` sólo desbloquea las caras horizontales; arriba y abajo giran en sentidos
        # opuestos.
        ("north", 0, 90, 0.0),
        ("up", 0, 90, 90.0),
        ("down", 0, 90, 270.0),
        # Al combinar x/y, el ``y`` actúa sobre la dirección efectiva tras el giro x.
        ("up", 180, 90, 270.0),
        ("down", 180, 90, 90.0),
    ],
)
def test_uvlock_rotation(direction, x, y, expected):
    assert uvlock_rotation(direction, x, y) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("uvlock", "v_min", "v_max"),
    [
        # Sin uvlock la textura gira con el modelo: la mitad inferior acaba invertida.
        (False, 0.5, 1.0),
        # Con uvlock se muestrea la mitad superior y queda derecha, como en Minecraft.
        (True, 0.0, 0.5),
    ],
)
def test_uvlock_keeps_side_texture_upright(uvlock, v_min, v_max):
    """La cara lateral de una "escalera" boca abajo no debe invertir la textura."""
    builder = make_setup(
        {
            blockstate_path("test:stairish"): {
                "variants": {
                    "": {"model": "test:block/stairish", "x": 180, "uvlock": uvlock},
                }
            },
            model_path("test:block/stairish"): {
                "textures": {"all": "minecraft:block/tex"},
                "elements": [
                    {
                        "from": [0, 0, 0],
                        "to": [16, 16, 16],
                        "faces": {
                            "north": {"texture": "#all", "uv": [0, 8, 16, 16]},
                            "south": {"texture": "#all", "uv": [0, 0, 16, 16]},
                            "east": {"texture": "#all", "uv": [0, 0, 16, 16]},
                            "west": {"texture": "#all", "uv": [0, 0, 16, 16]},
                            "up": {"texture": "#all", "uv": [0, 0, 16, 16]},
                            "down": {"texture": "#all", "uv": [0, 0, 16, 16]},
                        },
                    }
                ],
            },
            texture_path("minecraft:block/tex"): make_png(),
        }
    )
    state = builder.state_mesh(BlockState("test:stairish"))
    # La cara norte original termina siendo la cara sur (z=1) y es la única sur del cubo.
    south = next(
        t
        for t in state.templates
        if np.allclose(t.normal, FACE_NORMALS["south"]) and np.allclose(t.positions[:, 2], 1.0)
    )
    assert south.st[:, 1].min() == pytest.approx(v_min)
    assert south.st[:, 1].max() == pytest.approx(v_max)
    top = south.positions[:, 1] > 0.5
    bottom = ~top
    if uvlock:
        assert south.st[top, 1].max() < south.st[bottom, 1].min()
    else:
        assert south.st[top, 1].min() > south.st[bottom, 1].max()


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


def test_cushion_entity_geometry_and_texture():
    """La entidad se dibuja como una caja de 1x0.25x1 con las UV de ``CushionModel``."""
    builder = cushion_setup("red")
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:cushion",
            "Pos": [0.5, 0.0, 0.5],
            "Rotation": [0.0, 0.0],
            "color": "red",
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    assert mesh.vertex_count == 24
    assert mesh.atlas.sprite_count == 1
    assert not mesh.atlas.missing
    assert mesh.palette[-1].name == "minecraft:cushion"
    assert np.all(mesh.block == len(mesh.palette) - 1)
    assert np.all(mesh.alpha_mode == 1)  # el PNG de cojín es cutout

    # CubeDeformation(-0.005): 15.99 x 3.99 x 15.99 px, con la base en la posición Y.
    assert mesh.positions.min(axis=0) == pytest.approx([0.0003125, 0.0003125, 0.0003125], abs=1e-6)
    assert mesh.positions.max(axis=0) == pytest.approx([0.9996875, 0.2496875, 0.9996875], abs=1e-6)

    normals = mesh.normals[mesh.quads[:, 0]]
    expected = {
        (-1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, 0.0, 1.0),
    }
    assert {tuple(np.round(normal, 6)) for normal in normals} == expected
    for quad, normal in zip(mesh.quads, normals, strict=True):
        a, b, c = mesh.positions[quad[:3]]
        assert np.dot(np.cross(b - a, c - a), normal) > 0.0

    # La cara superior toma la región (0.25..0.5, 0..0.25) del modelo, dentro del sprite.
    top = int(np.argmax(normals[:, 1]))
    sprite = mesh.atlas.sprite("minecraft:entity/cushion/red_cushion")
    assert sprite is not None
    width, height = mesh.atlas.size
    u0 = sprite.x / width
    v0 = sprite.y / height
    du = sprite.width / width
    dv = sprite.height / height
    top_uvs = mesh.uvs[mesh.quads[top]]
    assert np.allclose(
        np.unique(top_uvs[:, 0]),
        [u0 + 0.25 * du, u0 + 0.5 * du],
    )
    assert np.allclose(np.unique(top_uvs[:, 1]), [v0, v0 + 0.25 * dv])


def test_cushion_positions_are_relative_to_the_selection_corner_one():
    """``Position`` (esquina 1) no es la esquina mínima: las entidades usan la primera."""
    builder = cushion_setup("red")
    region = _region([AIR], [0, 0], (2, 1, 1), position=(4, 0, 0))
    region.position = (5, 0, 0)  # corner 1 de una selección de tamaño negativo
    region.entities = [
        {
            "id": "minecraft:cushion",
            "Pos": [-0.5, 0.5, 0.5],
            "Rotation": [0.0, 0.0],
            "color": "red",
        }
    ]
    mesh = builder.build(_doc([region], (2, 1, 1)))

    assert mesh.origin == (4, 0, 0)
    center = (mesh.positions.min(axis=0) + mesh.positions.max(axis=0)) / 2.0
    assert center[0] == pytest.approx(0.5)
    assert center[2] == pytest.approx(0.5)


def test_cushion_colors_fall_back_to_white_and_bad_entities_are_ignored():
    builder = cushion_setup("white", "red")
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:cushion",
            "Pos": [0.5, 0.0, 0.5],
            "Rotation": [0.0, 0.0],
            "color": "chartreuse",
        },
        {
            "id": "minecraft:cushion",
            "Pos": [1.5, 0.0, 0.5],
            "Rotation": [90.0, 0.0],
            "color": "red",
        },
        {"id": "minecraft:armor_stand", "Pos": [2.5, 0.0, 0.5]},
        {"id": "minecraft:cushion", "Pos": [3.5, 0.0]},  # Pos incompleto: se ignora
    ]
    mesh = builder.build(_doc([region], (2, 1, 1)))

    assert mesh.quad_count == 12
    assert mesh.atlas.sprite("minecraft:entity/cushion/white_cushion") is not None
    assert mesh.atlas.sprite("minecraft:entity/cushion/red_cushion") is not None
    # El color inválido no registra una textura inexistente.
    assert "minecraft:entity/cushion/chartreuse_cushion" not in mesh.atlas.missing
    assert len({int(sprite) for sprite in mesh.sprite}) == 2


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


def glass_setup(*names: str) -> MeshBuilder:
    """Cristales con un modelo de cubo propio, sin depender del jar."""
    entries: dict[str, object] = {
        model_path("test:block/glass"): _model("minecraft:block/tex"),
        # Translúcida: así el estado no es un oclusor opaco y se prueba el culling de cristal.
        texture_path("minecraft:block/tex"): make_png(color=(255, 255, 255, 100)),
    }
    for name in names:
        entries[blockstate_path(name)] = {
            "variants": {"": {"model": "test:block/glass"}},
        }
    return make_setup(entries)


def test_glass_culls_the_shared_face_between_equal_blocks():
    """Como ``AbstractGlassBlock.skipRendering``: el cristal no es opaco, pero no se duplica."""
    builder = glass_setup("minecraft:glass")
    region = _region([AIR, BlockState("minecraft:glass")], [1, 1], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    # 6 + 6 - 2 caras internas = 10.
    assert mesh.quad_count == 10


def test_glass_of_different_colours_keeps_the_shared_face():
    """Dos cristales tintados distintos no son el mismo bloque: Minecraft dibuja la unión."""
    red = BlockState("minecraft:red_stained_glass")
    blue = BlockState("minecraft:blue_stained_glass")
    builder = glass_setup(red.name, blue.name)
    region = _region([AIR, red, blue], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))
    assert mesh.quad_count == 12


# --------------------------------------------------------------------------- #
# Fluidos
# --------------------------------------------------------------------------- #


WATER = BlockState("minecraft:water", (("level", "0"),))


def fluid_setup() -> MeshBuilder:
    """Agua con una textura gris translúcida, como ``water_still`` en vanilla."""
    return make_setup(
        {
            blockstate_path("minecraft:water"): {
                "variants": {"": {"model": "minecraft:block/water"}}
            },
            model_path("minecraft:block/water"): {},
            texture_path("minecraft:block/water_still"): make_png(color=(174, 174, 174, 180)),
            blockstate_path("test:solid"): {"variants": {"": {"model": "test:block/solid"}}},
            model_path("test:block/solid"): _model("minecraft:block/solid"),
            texture_path("minecraft:block/solid"): make_png(),
        }
    )


def test_water_is_tinted_translucent_and_smooths_its_surface():
    builder = fluid_setup()
    region = _region([AIR, WATER], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    assert set(mesh.alpha_mode.tolist()) == {2}
    # Una fuente rodeada de aire no llega a 8/9: ``calculateAverageHeight`` también
    # promedia las esquinas secas (peso 1) con la altura propia (peso 10).
    assert mesh.positions[:, 1].max() == pytest.approx(8.0 / 9.0 * 10.0 / 12.0)

    key = ("minecraft:block/water_still", "#3f76e4")
    sprite = builder.atlas._sprites[key]
    assert sprite is not None
    pixel = builder.atlas.image.crop(sprite.box).getpixel((0, 0))
    assert pixel[:3] == (174 * 0x3F // 255, 174 * 0x76 // 255, 174 * 0xE4 // 255)
    assert pixel[3] == 180


def test_water_culls_faces_between_water_blocks():
    """Sin la regla de fluido contra fluido el agua translúcida es una rejilla de paredes."""
    builder = fluid_setup()
    region = _region([AIR, WATER], [1] * 9, (3, 1, 3))
    mesh = builder.build(_doc([region], (3, 1, 3)))

    # 9 caras superiores + 9 inferiores + 12 laterales; las 24 internas se descartan.
    assert mesh.quad_count == 30
    normals = mesh.normals[mesh.quads[:, 0]]
    # Las caras superiores ya no son planas: la normal se inclina en los bordes.
    assert int((normals[:, 1] > 0.5).sum()) == 9
    assert int((normals[:, 1] < -0.5).sum()) == 9


def test_stacked_water_fills_the_block_under_the_surface():
    builder = fluid_setup()
    region = _region([AIR, WATER], [1, 1], (1, 2, 1))
    mesh = builder.build(_doc([region], (1, 2, 1)))

    # 12 caras menos las dos horizontales internas.
    assert mesh.quad_count == 10
    assert mesh.positions[:, 1].min() == pytest.approx(0.0)
    # El bloque inferior llega a 1 porque tiene agua encima; la superficie superior se
    # suaviza contra el aire que la rodea.
    assert mesh.positions[:, 1].max() == pytest.approx(1.0 + 8.0 / 9.0 * 10.0 / 12.0)
    normals = mesh.normals[mesh.quads[:, 0]]
    # Sólo hay una cara superior (la de la superficie) y una inferior (la base).
    assert int(np.isclose(normals[:, 1], 1.0).sum()) == 1
    assert int(np.isclose(normals[:, 1], -1.0).sum()) == 1


def test_water_against_opaque_neighbour_culls_only_that_face():
    builder = fluid_setup()
    solid = BlockState("test:solid")
    region = _region([AIR, WATER, solid], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))

    water_index = next(i for i, block in enumerate(mesh.palette) if block.name == WATER.name)
    assert int((mesh.block == water_index).sum()) == 5


def _top_quads(mesh):
    """Vértices ``(4, 3)`` de las caras superiores, ordenados de oeste a este."""
    normals = mesh.normals[mesh.quads[:, 0]]
    tops = [mesh.positions[mesh.quads[index]] for index in np.where(normals[:, 1] > 0.5)[0]]
    return sorted(tops, key=lambda quad: float(quad[:, 0].mean()))


def test_water_and_lower_flowing_water_share_a_continuous_surface():
    builder = fluid_setup()
    flowing = BlockState("minecraft:water", (("level", "7"),))
    region = _region([AIR, WATER, flowing], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))

    # Ya no queda ninguna pared vertical entre los dos bloques: sólo las caras exteriores.
    assert mesh.quad_count == 10

    west, east = _top_quads(mesh)
    # El borde compartido (NE de la fuente y NW del agua que fluye) coincide exactamente.
    assert west[3, 1] == pytest.approx(east[0, 1])
    assert west[2, 1] == pytest.approx(east[1, 1])


def test_flowing_water_forms_a_continuous_ramp_instead_of_stairs():
    """Los niveles 0..3 forman una pendiente continua, no cuatro escalones planos."""
    builder = fluid_setup()
    levels = [WATER] + [
        BlockState("minecraft:water", (("level", str(level)),)) for level in (1, 2, 3)
    ]
    region = _region([AIR, *levels], [1, 2, 3, 4], (4, 1, 1))
    mesh = builder.build(_doc([region], (4, 1, 1)))

    # 4 bloques x 6 caras - 3 uniones x 2 caras internas.
    assert mesh.quad_count == 18

    tops = _top_quads(mesh)
    assert len(tops) == 4
    for left, right in zip(tops, tops[1:], strict=False):
        # NE/SE del bloque de la izquierda y NW/SW del de la derecha son el mismo borde.
        assert left[3, 1] == pytest.approx(right[0, 1])
        assert left[2, 1] == pytest.approx(right[1, 1])
        # La superficie de cada bloque está inclinada: sus dos bordes no están a la misma
        # altura. Si fuese una escalera, cada bloque sería plano.
        assert abs(float(left[3, 1] - left[0, 1])) > 1e-3

    # La altura del borde este desciende a lo largo de la corriente.
    east_edges = [float((quad[2, 1] + quad[3, 1]) * 0.5) for quad in tops]
    assert east_edges == sorted(east_edges, reverse=True)
    assert len(set(np.round(east_edges, 6))) == 4


def test_water_surface_is_lower_than_the_adjacent_block():
    """El agua no llega al techo del bloque y su orilla se inclina hacia el aire."""
    builder = fluid_setup()
    solid = BlockState("test:solid")
    region = _region([AIR, WATER, solid], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))

    assert mesh.positions[:, 1].max() == pytest.approx(1.0)
    water_index = next(i for i, block in enumerate(mesh.palette) if block.name == WATER.name)
    water_quads = np.where(mesh.block == water_index)[0]
    water_vertices = mesh.positions[np.unique(mesh.quads[water_quads])]
    assert water_vertices[:, 1].max() < 8.0 / 9.0
    assert water_vertices[:, 1].max() > 0.7


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


def test_real_panes_complete_stale_connections_and_cull_the_joint():
    """Con los modelos reales: la conexión desfasada se completa y la junta se oculta."""
    from _jar import jar_resources

    south = BlockState(
        "minecraft:glass_pane",
        (
            ("east", "false"),
            ("north", "false"),
            ("south", "true"),
            ("waterlogged", "false"),
            ("west", "false"),
        ),
    )
    north = BlockState(
        "minecraft:glass_pane",
        (
            ("east", "false"),
            ("north", "false"),
            ("south", "false"),
            ("waterlogged", "false"),
            ("west", "false"),
        ),
    )
    region = _region([AIR, south, north], [1, 2], (1, 1, 2))
    doc = _doc([region], (1, 1, 2))
    resolver = ModelResolver(jar_resources())

    culled = MeshBuilder(resolver).build(doc)
    assert culled.quad_count == 18
    # Sin culling quedan los dos cantos de la junta (20), pero la conexión ya está completa.
    unculled = MeshBuilder(resolver, options=MeshOptions(cull=False)).build(doc)
    assert unculled.quad_count == 20


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


def test_connected_panes_cull_the_joint_faces():
    """Dos vidrieras del mismo bloque y conectadas no dibujan el canto de la junta.

    Es el ``IronBarsBlock.skipRendering`` de Minecraft: sin esto, una vidriera continua
    enseña una línea vertical con ``*_pane_top`` en cada bloque.
    """
    builder = pane_setup("test:glass_pane")
    north = BlockState("test:glass_pane", (("north", "true"),))
    south = BlockState("test:glass_pane", (("south", "true"),))
    region = _region([AIR, south, north], [1, 2], (1, 1, 2))
    mesh = builder.build(_doc([region], (1, 1, 2)))
    # 3 caras por media vidriera menos los dos cantos de la junta.
    assert mesh.quad_count == 4


def test_different_panes_keep_the_joint_faces():
    """Dos vidrieras de bloques distintos no se ocultan (como en Minecraft)."""
    builder = pane_setup("test:glass_pane", "test:other_pane")
    north = BlockState("test:other_pane", (("north", "true"),))
    south = BlockState("test:glass_pane", (("south", "true"),))
    region = _region([AIR, south, north], [1, 2], (1, 1, 2))
    mesh = builder.build(_doc([region], (1, 1, 2)))
    assert mesh.quad_count == 6


def _conn_state(name: str, **props: str) -> BlockState:
    base = {"north": "false", "east": "false", "south": "false", "west": "false"}
    base.update(props)
    return BlockState(name, tuple(sorted(base.items())))


def test_stale_pane_connection_is_filled_from_the_neighbour():
    """Una vidriera colocada antes que su vecina se reconecta (no al revés)."""
    south = _conn_state("test:glass_pane", south="true")
    north = _conn_state("test:glass_pane")
    result = _recompute_connections({(0, 0, 0): south, (0, 0, 1): north})
    assert _block_prop(result[(0, 0, 1)], "north")
    assert not _block_prop(result[(0, 0, 1)], "south")
    # El aire no inventa conexiones.
    assert not _block_prop(result[(0, 0, 0)], "north")


def test_pane_connection_to_air_is_removed():
    """Un raíl hacia la nada se quita: es lo que deja vidrieras sueltas en los bordes."""
    lonely = _conn_state("test:glass_pane", west="true", south="true")
    result = _recompute_connections({(0, 0, 0): lonely})
    assert not _block_prop(result[(0, 0, 0)], "west")
    assert not _block_prop(result[(0, 0, 0)], "south")


def test_pane_connection_to_a_solid_block_is_preserved():
    """Contra un bloque no conectable se respeta el valor que traía el archivo."""
    pane = _conn_state("test:glass_pane", west="true")
    solid = BlockState("test:cube")
    result = _recompute_connections({(0, 0, 0): pane, (-1, 0, 0): solid})
    assert _block_prop(result[(0, 0, 0)], "west")


def test_stale_fence_connection_respects_the_material_family():
    oak = _conn_state("minecraft:oak_fence")
    spruce = _conn_state("minecraft:spruce_fence")
    nether = _conn_state("minecraft:nether_brick_fence")
    result = _recompute_connections({(0, 0, 0): oak, (0, 0, 1): spruce})
    assert _block_prop(result[(0, 0, 1)], "north")
    # La valla de ladrillo del Nether no se une a las de madera.
    result = _recompute_connections({(0, 0, 0): oak, (0, 0, 1): nether})
    assert not _block_prop(result[(0, 0, 1)], "north")


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


def test_real_top_stairs_keep_side_texture_upright():
    """Con el modelo real: la contrahuella superior no invierte la textura.

    Las escaleras usan ``x: 180, uvlock: true`` para la mitad superior. Sin ``uvlock`` la UV
    de la contrahuella viajaba con el giro y quedaba boca abajo; con él, Minecraft muestrea la
    mitad correspondiente de la textura y la deja derecha.
    """
    from _jar import jar_resources

    builder = MeshBuilder(ModelResolver(jar_resources()))

    def stairs(half: str):
        return builder.state_mesh(
            BlockState(
                "minecraft:oak_stairs",
                (
                    ("facing", "east"),
                    ("half", half),
                    ("shape", "straight"),
                    ("waterlogged", "false"),
                ),
            )
        )

    def north_riser(state, y_min: float, y_max: float):
        return next(
            template
            for template in state.templates
            if np.allclose(template.normal, FACE_NORMALS["north"])
            and np.allclose(template.positions[:, 2], 0.0)
            and np.allclose(template.positions[:, 1].min(), y_min)
            and np.allclose(template.positions[:, 1].max(), y_max)
        )

    bottom = north_riser(stairs("bottom"), 0.0, 0.5)
    top = north_riser(stairs("top"), 0.5, 1.0)

    assert bottom.st[:, 1].min() == pytest.approx(0.5)
    assert bottom.st[:, 1].max() == pytest.approx(1.0)
    assert top.st[:, 1].min() == pytest.approx(0.0)
    assert top.st[:, 1].max() == pytest.approx(0.5)

    for riser in (bottom, top):
        upper = riser.positions[:, 1] > riser.positions[:, 1].mean()
        lower = ~upper
        assert riser.st[upper, 1].max() < riser.st[lower, 1].min()


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
