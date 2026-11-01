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

from lite2obj.assets import (
    MemorySource,
    ResourceManager,
    blockstate_path,
    items_model_path,
    model_path,
    texture_path,
)
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


def cushion_setup(*colors: str, **options) -> MeshBuilder:
    """Builder sin bloques, con las texturas de entidad de los colores indicados."""
    entries = {
        texture_path(f"minecraft:entity/cushion/{color}_cushion"): make_cushion_png()
        for color in colors
    }
    return make_setup(entries, **options)


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


def test_unknown_block_is_skipped_and_reported():
    builder = cube_setup()
    region = _region(
        [AIR, BlockState("test:cube"), BlockState("minecraft:bloque_del_futuro")],
        [1, 2],
        (2, 1, 1),
    )
    mesh = builder.build(_doc([region], (2, 1, 1)))

    assert mesh.quad_count == 6
    assert mesh.missing_blocks == ("minecraft:bloque_del_futuro",)
    assert mesh.missing_count == 1
    assert all(block.name != "minecraft:bloque_del_futuro" for block in mesh.palette)


def test_block_with_missing_model_is_skipped_and_reported():
    builder = make_setup(
        {
            blockstate_path("test:roto"): {"variants": {"": {"model": "test:block/no_existe"}}},
        }
    )
    region = _region([AIR, BlockState("test:roto")], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 0
    assert mesh.missing_blocks == ("test:roto",)
    assert mesh.missing_count == 1


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
        {"id": "minecraft:cushion", "Pos": [3.5, 0.0]},  # Pos incompleto: se ignora
    ]
    mesh = builder.build(_doc([region], (2, 1, 1)))

    assert mesh.quad_count == 12
    assert mesh.atlas.sprite("minecraft:entity/cushion/white_cushion") is not None
    assert mesh.atlas.sprite("minecraft:entity/cushion/red_cushion") is not None
    # El color inválido no registra una textura inexistente.
    assert "minecraft:entity/cushion/chartreuse_cushion" not in mesh.atlas.missing
    assert len({int(sprite) for sprite in mesh.sprite}) == 2


def entity_setup(**options) -> MeshBuilder:
    """Builder con las texturas y modelos mínimos para las entidades sintéticas.

    Los modelos de entidad vienen del paquete (``entity_models.json``); aquí sólo hacen
    falta las texturas, el blockstate de los *block entities* y algunos modelos de objeto.
    """
    frame = {
        "textures": {
            "particle": "minecraft:block/birch_planks",
            "wood": "minecraft:block/birch_planks",
            "back": "minecraft:block/item_frame",
        },
        "elements": [
            {
                "from": [3, 3, 15.5],
                "to": [13, 13, 16],
                "faces": {
                    "north": {"uv": [3, 3, 13, 13], "texture": "#back"},
                    "south": {"uv": [3, 3, 13, 13], "texture": "#back"},
                },
            }
        ],
    }
    entries = {
        blockstate_path("minecraft:chest"): {"variants": {"": {"model": "test:block/chest"}}},
        model_path("test:block/chest"): _model("minecraft:entity/chest/normal", cullface=False),
        blockstate_path("minecraft:item_frame"): {
            "variants": {
                "map=false": {"model": "test:block/item_frame"},
                "map=true": {"model": "test:block/item_frame"},
            }
        },
        blockstate_path("minecraft:light_blue_banner"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:light_blue_wall_banner"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:bell"): {"variants": {"": {"model": "test:block/empty"}}},
        blockstate_path("minecraft:conduit"): {"variants": {"": {"model": "test:block/empty"}}},
        blockstate_path("minecraft:water"): {"variants": {"": {"model": "minecraft:block/water"}}},
        model_path("minecraft:block/water"): {},
        blockstate_path("minecraft:white_shulker_box"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:skeleton_skull"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:skeleton_wall_skull"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:creeper_head"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:creeper_wall_head"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:dragon_head"): {"variants": {"": {"model": "test:block/empty"}}},
        blockstate_path("minecraft:dragon_wall_head"): {
            "variants": {"": {"model": "test:block/empty"}}
        },
        blockstate_path("minecraft:piston_head"): {"variants": {"": {"model": "test:block/empty"}}},
        blockstate_path("minecraft:hopper"): {"variants": {"": {"model": "test:block/contents"}}},
        blockstate_path("minecraft:furnace"): {"variants": {"": {"model": "test:block/contents"}}},
        blockstate_path("minecraft:tnt"): {"variants": {"": {"model": "test:block/contents"}}},
        blockstate_path("minecraft:command_block"): {
            "variants": {"": {"model": "test:block/contents"}}
        },
        blockstate_path("minecraft:spawner"): {"variants": {"": {"model": "test:block/contents"}}},
        model_path("test:block/empty"): {"textures": {}, "elements": []},
        model_path("test:block/contents"): _model("minecraft:block/contents", cullface=False),
        model_path("test:block/item_frame"): frame,
        texture_path("minecraft:block/birch_planks"): make_png(),
        texture_path("minecraft:block/item_frame"): make_png(),
        texture_path("minecraft:block/contents"): make_png(),
        texture_path("minecraft:entity/chest/normal"): make_png(),
        texture_path("minecraft:entity/boat/oak"): make_png(),
        texture_path("minecraft:entity/chest_boat/oak"): make_png(),
        texture_path("minecraft:entity/minecart/minecart"): make_png(),
        texture_path("minecraft:entity/armorstand/armorstand"): make_png(),
        texture_path("minecraft:entity/banner/banner_base"): make_png(),
        texture_path("minecraft:entity/banner/base"): make_png(),
        texture_path("minecraft:entity/bell/bell_body"): make_png(),
        texture_path("minecraft:entity/conduit/base"): make_png(),
        texture_path("minecraft:entity/conduit/cage"): make_png(),
        texture_path("minecraft:entity/conduit/open_eye"): make_png(),
        texture_path("minecraft:block/water_still"): make_png(color=(174, 174, 174, 180)),
        texture_path("minecraft:block/water_flow"): make_png(color=(174, 174, 174, 180)),
        texture_path("minecraft:entity/shulker/shulker"): make_png(),
        texture_path("minecraft:entity/shulker/shulker_white"): make_png(),
        texture_path("minecraft:entity/skeleton/skeleton"): make_png(),
        texture_path("minecraft:entity/creeper/creeper"): make_png(),
        texture_path("minecraft:entity/enderdragon/dragon"): make_png(),
        texture_path("minecraft:item/thing"): make_png(color=(255, 0, 0, 255)),
        items_model_path("minecraft:thing"): {
            "model": {"type": "minecraft:model", "model": "minecraft:item/thing"}
        },
        model_path("minecraft:item/thing"): {
            "parent": "minecraft:item/generated",
            "textures": {"layer0": "minecraft:item/thing"},
        },
        model_path("minecraft:item/generated"): {
            "display": {"fixed": {"rotation": [0, 180, 0], "scale": [1, 1, 1]}}
        },
        "data/minecraft/painting_variant/kebab.json": {
            "asset_id": "minecraft:kebab",
            "width": 1,
            "height": 1,
        },
        "data/minecraft/painting_variant/pointer.json": {
            "asset_id": "minecraft:pointer",
            "width": 4,
            "height": 4,
        },
        texture_path("minecraft:painting/kebab"): make_png(),
        texture_path("minecraft:painting/pointer"): make_png(),
        texture_path("minecraft:painting/back"): make_png(color=(0, 0, 0, 255)),
        texture_path("minecraft:entity/pig/pig_temperate"): make_png(),
        texture_path("minecraft:entity/pig/pig_temperate_baby"): make_png(),
    }
    return make_setup(entries, **options)


def _chest_state(facing: str = "north") -> BlockState:
    return BlockState(
        "minecraft:chest",
        (("facing", facing), ("type", "single"), ("waterlogged", "false")),
    )


def _sprites_of(mesh, sprite) -> np.ndarray:
    return mesh.sprite == sprite.index


def _quad_points(mesh, selected: np.ndarray) -> np.ndarray:
    return mesh.positions[mesh.quads[selected]].reshape(-1, 3)


@pytest.mark.parametrize(
    ("facing", "axis", "value"),
    [
        ("north", 2, 0.0),
        ("south", 2, 1.0),
        ("west", 0, 0.0),
        ("east", 0, 1.0),
    ],
)
def test_chest_entity_replaces_block_and_faces_its_direction(facing, axis, value):
    """El cofre es una entidad: sustituye al bloque y se gira según ``facing``."""
    builder = entity_setup()
    chest = _chest_state(facing)
    region = _region([AIR, chest], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:chest", "x": 0, "y": 0, "z": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 18
    assert mesh.palette[-1] == chest
    texture = mesh.atlas.sprite("minecraft:entity/chest/normal")
    assert texture is not None
    assert np.all(_sprites_of(mesh, texture))
    # El modelo mide 15/16 de alto y su tapa toca la cara hacia la que mira.
    assert mesh.positions[:, 1].max() == pytest.approx(0.875)
    extent = mesh.positions[:, axis]
    assert (extent.min() if value == 0.0 else extent.max()) == pytest.approx(value)


def test_chest_block_keeps_its_model_with_entities_disabled():
    builder = entity_setup(entities=False)
    region = _region([AIR, _chest_state()], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:chest", "x": 0, "y": 0, "z": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    # El cubo sintético del bloque, sin la geometría del cofre.
    assert mesh.quad_count == 6


def test_skull_without_tile_entity_uses_its_entity_model():
    """El `.litematic` puede no guardar ``TileEntities``; el blockstate basta."""
    builder = entity_setup()
    skull = BlockState("minecraft:skeleton_skull", (("powered", "false"), ("rotation", "0")))
    region = _region([AIR, skull], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    texture = mesh.atlas.sprite("minecraft:entity/skeleton/skeleton")
    assert texture is not None
    assert _sprites_of(mesh, texture).all()
    assert mesh.quad_count == 6
    # La calavera mide media altura y está centrada, no es el cubo de respaldo.
    assert mesh.positions[:, 0].min() == pytest.approx(0.25)
    assert mesh.positions[:, 0].max() == pytest.approx(0.75)
    assert mesh.positions[:, 1].min() == pytest.approx(0.0)
    assert mesh.positions[:, 1].max() == pytest.approx(0.5)


def test_wall_skull_without_tile_entity_uses_wall_anchor():
    builder = entity_setup()
    skull = BlockState("minecraft:skeleton_wall_skull", (("facing", "north"), ("powered", "false")))
    region = _region([AIR, skull], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    # La calavera de pared va pegada al sur y subida 4 píxeles, como vanilla.
    assert mesh.positions[:, 1].min() == pytest.approx(0.25)
    assert mesh.positions[:, 1].max() == pytest.approx(0.75)
    assert mesh.positions[:, 2].min() == pytest.approx(0.5)
    assert mesh.positions[:, 2].max() == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("block", "texture", "width"),
    [
        ("minecraft:creeper_head", "minecraft:entity/creeper/creeper", 0.5),
        ("minecraft:dragon_head", "minecraft:entity/enderdragon/dragon", 0.75),
    ],
)
def test_other_heads_use_their_entity_texture(block, texture, width):
    builder = entity_setup()
    skull = BlockState(block, (("powered", "false"), ("rotation", "0")))
    region = _region([AIR, skull], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    sprite = mesh.atlas.sprite(texture)
    assert sprite is not None
    assert _sprites_of(mesh, sprite).all()
    assert mesh.positions[:, 0].max() - mesh.positions[:, 0].min() == pytest.approx(width)


def test_skull_with_tile_entity_is_not_duplicated():
    builder = entity_setup()
    skull = BlockState("minecraft:skeleton_skull", (("powered", "false"), ("rotation", "0")))
    region = _region([AIR, skull], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:skeleton_skull", "x": 0, "y": 0, "z": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6


def test_piston_head_is_not_treated_as_skull():
    builder = entity_setup()
    piston = BlockState(
        "minecraft:piston_head", (("facing", "north"), ("short", "false"), ("type", "normal"))
    )
    region = _region([AIR, piston], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.atlas.sprite("minecraft:entity/skeleton/skeleton") is None


def test_skull_is_not_synthesized_with_entities_disabled():
    builder = entity_setup(entities=False)
    skull = BlockState("minecraft:skeleton_skull", (("powered", "false"), ("rotation", "0")))
    region = _region([AIR, skull], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.atlas.sprite("minecraft:entity/skeleton/skeleton") is None


def test_banner_without_tile_entity_uses_blockstate_color_and_rotation():
    builder = entity_setup()
    banner = BlockState("minecraft:light_blue_banner", (("rotation", "4"),))
    region = _region([AIR, banner], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    tint = mesh.atlas.sprite("minecraft:entity/banner/base", (58, 179, 218))
    assert tint is not None
    assert _sprites_of(mesh, tint).sum() > 0
    assert mesh.atlas.sprite("minecraft:entity/banner/banner_base") is not None
    assert mesh.quad_count == 18
    # ``rotation=4`` son 90°: la tela, ancha en X, pasa a ser ancha en Z.
    x_extent = mesh.positions[:, 0].max() - mesh.positions[:, 0].min()
    z_extent = mesh.positions[:, 2].max() - mesh.positions[:, 2].min()
    assert z_extent > x_extent


def test_wall_banner_without_tile_entity_uses_blockstate_color():
    builder = entity_setup()
    banner = BlockState("minecraft:light_blue_wall_banner", (("facing", "north"),))
    region = _region([AIR, banner], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    tint = mesh.atlas.sprite("minecraft:entity/banner/base", (58, 179, 218))
    assert tint is not None
    assert _sprites_of(mesh, tint).sum() > 0
    assert mesh.quad_count == 12


def test_bell_without_tile_entity_uses_the_entity_model():
    builder = entity_setup()
    bell = BlockState(
        "minecraft:bell",
        (("attachment", "floor"), ("facing", "north"), ("powered", "false")),
    )
    region = _region([AIR, bell], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    sprite = mesh.atlas.sprite("minecraft:entity/bell/bell_body")
    assert sprite is not None
    assert _sprites_of(mesh, sprite).sum() > 0
    # La campana ocupa el centro del bloque; el cubo de respaldo llegaría a 0 y 1.
    assert mesh.positions.min() > 0.1
    assert mesh.positions.max() < 0.9


def test_bell_with_tile_entity_is_not_duplicated():
    builder = entity_setup()
    bell = BlockState(
        "minecraft:bell",
        (("attachment", "floor"), ("facing", "north"), ("powered", "false")),
    )
    region = _region([AIR, bell], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:bell", "x": 0, "y": 0, "z": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    sprite = mesh.atlas.sprite("minecraft:entity/bell/bell_body")
    assert sprite is not None
    assert _sprites_of(mesh, sprite).sum() == 12


def test_waterlogged_conduit_without_tile_entity_uses_the_entity_model():
    """El conduit no tiene modelo de bloque: se reconstruye desde el blockstate."""
    builder = entity_setup()
    conduit = BlockState("minecraft:conduit", (("waterlogged", "true"),))
    region = _region([AIR, conduit], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    for texture in (
        "minecraft:entity/conduit/base",
        "minecraft:entity/conduit/cage",
        "minecraft:entity/conduit/open_eye",
    ):
        sprite = mesh.atlas.sprite(texture)
        assert sprite is not None
        assert _sprites_of(mesh, sprite).sum() > 0

    # El agua del conduit waterlogged se conserva aunque el cubo de respaldo desaparezca.
    still = mesh.atlas.sprite("minecraft:block/water_still", (0x3F, 0x76, 0xE4))
    assert still is not None
    assert _sprites_of(mesh, still).sum() > 0
    waterlogged_index = next(i for i, block in enumerate(mesh.palette) if block == conduit)
    waterlogged_quads = np.where(mesh.block == waterlogged_index)[0]
    assert len(waterlogged_quads) == 6
    assert np.all(mesh.alpha_mode[waterlogged_quads] == 2)

    # La geometría de la entidad queda centrada y no ocupa el cubo completo del bloque.
    entity = BlockState("minecraft:conduit")
    entity_index = next(i for i, block in enumerate(mesh.palette) if block == entity)
    entity_quads = np.where(mesh.block == entity_index)[0]
    entity_points = mesh.positions[np.unique(mesh.quads[entity_quads])]
    assert entity_points.min() > 0.1
    assert entity_points.max() < 0.9


def test_banner_parses_two_word_colors_and_tints_the_flag():
    builder = entity_setup()
    banner = BlockState("minecraft:light_blue_banner", (("rotation", "0"),))
    region = _region([AIR, banner], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:banner", "x": 0, "y": 0, "z": 0, "Rotation": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 18
    assert mesh.palette[-1] == banner
    # ``light_blue`` es (58, 179, 218); cortar por el primer guion bajo daba ``light``.
    tint = mesh.atlas.sprite("minecraft:entity/banner/base", (58, 179, 218))
    assert tint is not None
    assert _sprites_of(mesh, tint).sum() > 0
    assert mesh.atlas.sprite("minecraft:entity/banner/banner_base") is not None


def test_white_shulker_box_uses_its_own_texture():
    builder = entity_setup()
    shulker = BlockState("minecraft:white_shulker_box", (("facing", "up"),))
    region = _region([AIR, shulker], [1], (1, 1, 1))
    region.tile_entities = [{"id": "minecraft:shulker_box", "x": 0, "y": 0, "z": 0}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 12
    white = mesh.atlas.sprite("minecraft:entity/shulker/shulker_white")
    assert white is not None
    assert _sprites_of(mesh, white).all()
    # La textura sin color no se registra siquiera.
    assert mesh.atlas.sprite("minecraft:entity/shulker/shulker") is None


def test_boat_and_chest_boat_use_their_models_and_water_patch():
    builder = entity_setup()

    def build(kind: str, yaw: float = 0.0):
        region = _region([AIR], [0], (1, 1, 1))
        region.entities = [
            {"id": f"minecraft:{kind}", "Pos": [0.5, 0.0, 0.5], "Rotation": [yaw, 0.0]}
        ]
        return builder.build(_doc([region], (1, 1, 1)))

    boat = build("oak_boat")
    assert boat.quad_count == 60  # casco (54) + lámina de agua (6)
    texture = boat.atlas.sprite("minecraft:entity/boat/oak")
    assert texture is not None
    assert np.all(_sprites_of(boat, texture))
    assert boat.positions[:, 1].min() == pytest.approx(0.0, abs=1e-3)
    assert boat.positions[:, 1].max() == pytest.approx(0.8836, abs=1e-3)

    # Con giro de 90° el eje largo del bote pasa de X a Z.
    turned = build("oak_boat", yaw=90.0)
    assert turned.positions[:, 0].max() - turned.positions[:, 0].min() == pytest.approx(2.0)
    assert turned.positions[:, 2].max() - turned.positions[:, 2].min() == pytest.approx(3.0)

    chest_boat = build("oak_chest_boat")
    assert chest_boat.quad_count == 78  # el cofre va incluido en su capa
    assert chest_boat.atlas.sprite("minecraft:entity/chest_boat/oak") is not None


@pytest.mark.parametrize(
    ("kind", "quads"),
    [
        ("minecart", 30),
        ("chest_minecart", 48),  # casco + cofre (modelo de entidad)
        ("hopper_minecart", 36),
        ("furnace_minecart", 36),
        ("tnt_minecart", 36),
        ("command_block_minecart", 36),
        ("spawner_minecart", 36),
    ],
)
def test_minecart_body_and_variant_contents(kind, quads):
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [{"id": f"minecraft:{kind}", "Pos": [0.5, 0.0, 0.5], "Rotation": [0.0, 0.0]}]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == quads
    assert 0.0 < mesh.positions[:, 1].min() <= 0.0625
    assert mesh.positions[:, 1].max() > 0.6


def test_item_frame_anchors_to_the_support_and_draws_its_item():
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:item_frame",
            "Pos": [0.5, 0.5, -0.03125],
            "Facing": 2,
            "Item": {"id": "minecraft:thing", "count": 1},
            "ItemRotation": 0,
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    frame_texture = mesh.atlas.sprite("minecraft:block/item_frame")
    item_texture = mesh.atlas.sprite("minecraft:item/thing")
    assert frame_texture is not None
    assert item_texture is not None
    assert mesh.quad_count == 4
    assert _sprites_of(mesh, frame_texture).sum() == 2
    item = _quad_points(mesh, _sprites_of(mesh, item_texture))
    assert item.shape == (8, 3)

    # El marco queda pegado a la cara del bloque de apoyo (z = 0) y el objeto delante.
    frame = _quad_points(mesh, _sprites_of(mesh, frame_texture))
    assert frame[:, 2].min() == pytest.approx(-1.0 / 32.0)
    assert frame[:, 2].max() == pytest.approx(0.0)
    assert item[:, 2].max() < -1.0 / 32.0
    # El objeto generado se dibuja a media escala y centrado en el marco.
    assert (item[:, 0].min(), item[:, 0].max()) == pytest.approx((0.25, 0.75))
    assert (item[:, 1].min(), item[:, 1].max()) == pytest.approx((0.25, 0.75))
    assert {tuple(np.round(normal, 6)) for normal in mesh.normals} == {
        (0.0, 0.0, -1.0),
        (0.0, 0.0, 1.0),
    }


def test_item_frame_rotation_turns_the_item_but_not_the_frame():
    builder = entity_setup()

    def item_bounds(turns: int) -> tuple[float, float]:
        region = _region([AIR], [0], (1, 1, 1))
        region.entities = [
            {
                "id": "minecraft:item_frame",
                "Pos": [0.5, 0.5, -0.03125],
                "Facing": 2,
                "Item": {"id": "minecraft:thing", "count": 1},
                "ItemRotation": turns,
            }
        ]
        mesh = builder.build(_doc([region], (1, 1, 1)))
        texture = mesh.atlas.sprite("minecraft:item/thing")
        assert texture is not None
        x = _quad_points(mesh, _sprites_of(mesh, texture))[:, 0]
        return float(x.min()), float(x.max())

    assert item_bounds(0) == pytest.approx((0.25, 0.75))
    # 45°: el cuadrado de lado 0.5 pasa a medir 0.5·√2 en cada eje.
    assert item_bounds(1)[1] - item_bounds(1)[0] == pytest.approx(0.5 * np.sqrt(2.0))


def test_invisible_item_frame_hides_the_frame_but_keeps_the_item():
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:item_frame",
            "Pos": [0.5, 0.5, -0.03125],
            "Facing": 2,
            "Invisible": 1,
            "Item": {"id": "minecraft:thing", "count": 1},
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 2
    assert mesh.atlas.sprite("minecraft:item/thing") is not None
    frame_texture = mesh.atlas.sprite("minecraft:block/item_frame")
    assert frame_texture is not None
    assert not _sprites_of(mesh, frame_texture).any()


@pytest.mark.parametrize(
    ("facing", "pos", "axis", "support", "outward"),
    [
        (0, [0.5, -0.03125, 0.5], 1, 0.0, -1.0),
        (1, [0.5, 1.03125, 0.5], 1, 1.0, 1.0),
        (2, [0.5, 0.5, -0.03125], 2, 0.0, -1.0),
        (3, [0.5, 0.5, 1.03125], 2, 1.0, 1.0),
        (4, [-0.03125, 0.5, 0.5], 0, 0.0, -1.0),
        (5, [1.03125, 0.5, 0.5], 0, 1.0, 1.0),
    ],
)
def test_item_frame_attaches_to_every_face(facing, pos, axis, support, outward):
    """El marco queda pegado a la cara del apoyo y el objeto sobresale hacia fuera."""
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:item_frame",
            "Pos": pos,
            "Facing": facing,
            "Item": {"id": "minecraft:thing", "count": 1},
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    frame_texture = mesh.atlas.sprite("minecraft:block/item_frame")
    item_texture = mesh.atlas.sprite("minecraft:item/thing")
    assert frame_texture is not None
    assert item_texture is not None
    frame = _quad_points(mesh, _sprites_of(mesh, frame_texture))[:, axis]
    item = _quad_points(mesh, _sprites_of(mesh, item_texture))[:, axis]
    # El visor descarta las caras traseras: la cara exterior del objeto debe mirar hacia
    # fuera del apoyo, no hacia la pared.
    item_quads = mesh.quads[_sprites_of(mesh, item_texture)]
    expected = np.zeros(3, dtype=np.float32)
    expected[axis] = outward
    assert any(np.allclose(normal, expected) for normal in mesh.normals[item_quads.ravel()])
    if outward < 0:
        assert frame.min() == pytest.approx(support - 1.0 / 32.0)
        assert frame.max() == pytest.approx(support)
        assert item.max() < support - 1.0 / 32.0
    else:
        assert frame.min() == pytest.approx(support)
        assert frame.max() == pytest.approx(support + 1.0 / 32.0)
        assert item.min() > support + 1.0 / 32.0


def test_painting_uses_the_variant_size_and_faces_the_entity_rotation():
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {"id": "minecraft:painting", "Pos": [0.5, 0.5, 0.5], "facing": 0, "variant": "kebab"}
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    assert mesh.positions.min(axis=0) == pytest.approx([0.0, 0.0, 0.46875])
    assert mesh.positions.max(axis=0) == pytest.approx([1.0, 1.0, 0.53125])
    front = mesh.atlas.sprite("minecraft:painting/kebab")
    assert front is not None
    front_quad = _quad_points(mesh, _sprites_of(mesh, front))
    assert np.allclose(front_quad[:, 2], 0.53125)
    assert {tuple(np.round(normal, 6)) for normal in mesh.normals} == {
        (-1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, 0.0, 1.0),
    }


def test_painting_size_comes_from_the_variant_data():
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {
            "id": "minecraft:painting",
            "Pos": [0.5, 0.5, 0.5],
            "facing": 2,
            "variant": "minecraft:pointer",
        }
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    # 4x4 centrado en el ``Pos`` de la entidad.
    assert mesh.positions.min(axis=0) == pytest.approx([-1.5, -1.5, 0.46875])
    assert mesh.positions.max(axis=0) == pytest.approx([2.5, 2.5, 0.53125])


def test_armor_stand_hides_arms_and_base_plate_when_asked():
    builder = entity_setup()

    def build(**extra):
        entity = {"id": "minecraft:armor_stand", "Pos": [0.5, 0.0, 0.5]}
        entity.update(extra)
        region = _region([AIR], [0], (1, 1, 1))
        region.entities = [entity]
        return builder.build(_doc([region], (1, 1, 1)))

    plain = build()
    assert plain.quad_count == 54
    assert plain.positions[:, 1].min() == pytest.approx(0.001, abs=1e-3)
    assert plain.positions[:, 1].max() == pytest.approx(1.9698, abs=1e-3)

    armed = build(ShowArms=1)
    assert armed.quad_count == 66
    assert armed.positions[:, 0].min() < plain.positions[:, 0].min()

    no_plate = build(NoBasePlate=1)
    assert no_plate.quad_count == 48
    assert no_plate.positions[:, 1].min() > plain.positions[:, 1].min()

    assert plain.atlas.sprite("minecraft:entity/armorstand/armorstand") is not None


def test_living_entity_uses_its_layer_and_baby_variant():
    builder = entity_setup(mobs=True)

    def build(**extra):
        entity = {"id": "minecraft:pig", "Pos": [0.5, 0.0, 0.5]}
        entity.update(extra)
        region = _region([AIR], [0], (1, 1, 1))
        region.entities = [entity]
        return builder.build(_doc([region], (1, 1, 1)))

    adult = build()
    assert adult.quad_count == 42
    assert adult.positions[:, 1].min() == pytest.approx(0.0, abs=1e-3)
    assert adult.atlas.sprite("minecraft:entity/pig/pig_temperate") is not None

    baby = build(IsBaby=1)
    assert baby.quad_count == 42
    assert baby.positions[:, 1].max() < adult.positions[:, 1].max()
    assert baby.atlas.sprite("minecraft:entity/pig/pig_temperate_baby") is not None


def test_mobs_are_hidden_by_default_and_rendered_with_the_flag():
    def build(**options):
        builder = entity_setup(**options)
        region = _region([AIR], [0], (1, 1, 1))
        region.entities = [{"id": "minecraft:pig", "Pos": [0.5, 0.0, 0.5]}]
        return builder.build(_doc([region], (1, 1, 1)))

    hidden = build()
    assert hidden.quad_count == 0
    assert hidden.atlas.sprite("minecraft:entity/pig/pig_temperate") is None

    shown = build(mobs=True)
    assert shown.quad_count == 42
    assert shown.atlas.sprite("minecraft:entity/pig/pig_temperate") is not None


def test_unknown_malformed_and_invisible_entities_are_ignored():
    builder = entity_setup()
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {"id": "minecraft:not_a_creature", "Pos": [0.5, 0.0, 0.5]},
        {"id": "minecraft:pig", "Pos": [0.5, 0.0]},  # Pos incompleto
        {"id": "minecraft:pig", "Pos": [0.5, 0.0, 0.5], "Invisible": 1},
        {"Pos": [0.5, 0.0, 0.5]},  # sin id
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 0
    assert not mesh.palette


def test_cushions_are_hidden_with_entities_disabled():
    builder = cushion_setup("red", entities=False)
    region = _region([AIR], [0], (1, 1, 1))
    region.entities = [
        {"id": "minecraft:cushion", "Pos": [0.5, 0.0, 0.5], "Rotation": [0.0, 0.0], "color": "red"}
    ]
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 0
    assert mesh.atlas.sprite_count == 0


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
            blockstate_path("minecraft:bubble_column"): {
                "variants": {"": {"model": "minecraft:block/water"}}
            },
            model_path("minecraft:block/water"): {},
            texture_path("minecraft:block/water_still"): make_png(color=(174, 174, 174, 180)),
            texture_path("minecraft:block/water_flow"): make_png(color=(174, 174, 174, 180)),
            blockstate_path("test:solid"): {"variants": {"": {"model": "test:block/solid"}}},
            model_path("test:block/solid"): _model("minecraft:block/solid"),
            texture_path("minecraft:block/solid"): make_png(),
            blockstate_path("test:slab"): {"variants": {"": {"model": "test:block/slab"}}},
            model_path("test:block/slab"): {
                "textures": {"all": "minecraft:block/slab"},
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
            texture_path("minecraft:block/slab"): make_png(color=(190, 190, 190, 255)),
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
    # ``FluidRenderer`` baja la superficie 0.001 para no coincidir con el bloque vecino.
    assert mesh.positions[:, 1].max() == pytest.approx(8.0 / 9.0 * 10.0 / 12.0 - 0.001)

    key = ("minecraft:block/water_still", "#3f76e4")
    sprite = builder.atlas._sprites[key]
    assert sprite is not None
    pixel = builder.atlas.image.crop(sprite.box).getpixel((0, 0))
    assert pixel[:3] == (174 * 0x3F // 255, 174 * 0x76 // 255, 174 * 0xE4 // 255)
    assert pixel[3] == 180


def test_water_sides_use_the_flow_texture_with_vertical_uvs():
    """El agua usa ``water_flow`` en los laterales y la V recorre la altura de la caída."""
    builder = fluid_setup()
    falling = BlockState("minecraft:water", (("level", "8"),))
    region = _region([AIR, WATER, falling], [1, 2], (1, 2, 1))
    mesh = builder.build(_doc([region], (1, 2, 1)))

    water_tint = (0x3F, 0x76, 0xE4)
    still = mesh.atlas.sprite("minecraft:block/water_still", water_tint)
    flow = mesh.atlas.sprite("minecraft:block/water_flow", water_tint)
    assert still is not None
    assert flow is not None

    normals = mesh.normals[mesh.quads[:, 0]]
    sides = np.where(np.abs(normals[:, 1]) < 0.5)[0]
    assert len(sides) == 8
    assert np.all(mesh.sprite[sides] == flow.index)

    width, height = mesh.atlas.size
    for index in sides:
        quad = mesh.quads[index]
        uvs = mesh.uvs[quad]
        # Pasar de UV de atlas a UV dentro del sprite de flujo.
        u = (uvs[:, 0] - flow.x / width) / (flow.width / width)
        v = (uvs[:, 1] - flow.y / height) / (flow.height / height)
        assert u.min() >= -1e-6
        assert u.max() <= 0.5 + 1e-6
        # La textura de flujo es vertical: arriba se muestrea antes (V menor) que abajo.
        ys = mesh.positions[quad][:, 1]
        top = ys > (ys.min() + ys.max()) * 0.5
        assert v[top].mean() < v[~top].mean()
        assert v[top].max() <= 0.5 + 1e-6
        assert v[~top].min() >= 0.5 - 1e-6

    # La superficie del bloque que cae también usa flujo; el fondo sigue quieto.
    top = int(np.where(normals[:, 1] > 0.5)[0][0])
    bottom = int(np.where(normals[:, 1] < -0.5)[0][0])
    assert mesh.sprite[top] == flow.index
    assert mesh.sprite[bottom] == still.index


def test_still_water_keeps_the_still_surface_and_flowing_sides():
    """Una fuente quieta usa ``water_still`` arriba y ``water_flow`` en los laterales."""
    builder = fluid_setup()
    region = _region([AIR, WATER], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    water_tint = (0x3F, 0x76, 0xE4)
    still = mesh.atlas.sprite("minecraft:block/water_still", water_tint)
    flow = mesh.atlas.sprite("minecraft:block/water_flow", water_tint)
    assert still is not None
    assert flow is not None
    normals = mesh.normals[mesh.quads[:, 0]]
    top = int(np.where(normals[:, 1] > 0.5)[0][0])
    assert mesh.sprite[top] == still.index
    sides = np.where(np.abs(normals[:, 1]) < 0.5)[0]
    assert len(sides) == 4
    assert np.all(mesh.sprite[sides] == flow.index)


def test_bubble_column_is_rendered_as_tinted_water():
    """La columna de burbujas es agua: no debe caer al cubo de respaldo con partículas."""
    builder = fluid_setup()
    bubble = BlockState("minecraft:bubble_column", (("drag", "true"),))
    region = _region([AIR, bubble], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    assert set(mesh.alpha_mode.tolist()) == {2}
    assert mesh.positions[:, 1].max() == pytest.approx(8.0 / 9.0 * 10.0 / 12.0 - 0.001)

    water_tint = (0x3F, 0x76, 0xE4)
    still = mesh.atlas.sprite("minecraft:block/water_still", water_tint)
    flow = mesh.atlas.sprite("minecraft:block/water_flow", water_tint)
    assert still is not None
    assert flow is not None
    assert set(int(sprite) for sprite in mesh.sprite) == {still.index, flow.index}


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
    # El fondo también se desplaza 0.001 hacia arriba, como ``bottomOffs`` de vanilla.
    assert mesh.positions[:, 1].min() == pytest.approx(0.001)
    # El bloque inferior llega a 1 porque tiene agua encima; la superficie superior se
    # suaviza contra el aire que la rodea.
    assert mesh.positions[:, 1].max() == pytest.approx(1.0 + 8.0 / 9.0 * 10.0 / 12.0 - 0.001)
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


def test_waterlogged_slab_keeps_its_geometry_and_adds_fluid():
    """Una losa *waterlogged* dibuja la losa y el agua que contiene."""
    builder = fluid_setup()
    slab = BlockState("test:slab", (("waterlogged", "true"),))
    region = _region([AIR, slab], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    # 6 caras de losa (opacas) + 6 de agua (translúcidas), sin descartes contra el aire.
    assert mesh.quad_count == 12
    assert int((mesh.alpha_mode == 0).sum()) == 6
    water = np.where(mesh.alpha_mode == 2)[0]
    assert len(water) == 6

    water_vertices = mesh.positions[np.unique(mesh.quads[water])]
    # El agua sobresale por encima de la losa y conserva la altura de una fuente.
    assert water_vertices[:, 1].max() == pytest.approx(8.0 / 9.0 * 10.0 / 12.0 - 0.001)
    assert water_vertices[:, 1].max() > 0.5
    # El tinte sale del fluido, no del bloque anfitrión (que daría verde de césped).
    assert ("minecraft:block/water_still", "#3f76e4") in builder.atlas._sprites


def test_waterlogged_false_does_not_add_fluid():
    builder = fluid_setup()
    slab = BlockState("test:slab", (("waterlogged", "false"),))
    region = _region([AIR, slab], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    assert mesh.quad_count == 6
    assert set(mesh.alpha_mode.tolist()) == {0}


def test_waterlogged_slab_connects_to_adjacent_water():
    """El agua de una losa *waterlogged* comparte superficie y caras con el agua vecina."""
    builder = fluid_setup()
    slab = BlockState("test:slab", (("waterlogged", "true"),))
    region = _region([AIR, slab, WATER], [1, 2], (2, 1, 1))
    mesh = builder.build(_doc([region], (2, 1, 1)))

    # 6 caras de losa + 6 agua de la losa + 6 del bloque de agua - 2 de la unión.
    water = np.where(mesh.alpha_mode == 2)[0]
    assert len(water) == 10
    water_vertices = mesh.positions[np.unique(mesh.quads[water])]
    # Esquina compartida: agua propia y vecina (peso 10 cada una) y dos lados secos
    # (peso 1 cada uno), menos el desplazamiento de la superficie.
    assert water_vertices[:, 1].max() == pytest.approx(8.0 / 9.0 * 20.0 / 22.0 - 0.001)


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


def test_real_waterlogged_slab_and_stairs_add_fluid():
    """Los blockstates reales de losa y escalera conservan su geometría y añaden agua."""
    from _jar import jar_resources

    slab = BlockState("minecraft:oak_slab", (("type", "bottom"), ("waterlogged", "true")))
    stairs = BlockState(
        "minecraft:oak_stairs",
        (
            ("facing", "east"),
            ("half", "bottom"),
            ("shape", "straight"),
            ("waterlogged", "true"),
        ),
    )
    region = _region([AIR, slab, stairs], [1, 2], (2, 1, 1))
    doc = _doc([region], (2, 1, 1))
    builder = MeshBuilder(ModelResolver(jar_resources()))
    mesh = builder.build(doc)

    assert not builder.atlas.missing
    # Dos bloques *waterlogged* contiguos: 6 caras de agua cada uno menos la unión.
    assert int((mesh.alpha_mode == 2).sum()) == 10
    # La losa y la escalera conservan sus caras opacas.
    assert int((mesh.alpha_mode == 0).sum()) > 0


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


def test_stonecutter_saw_has_no_tint():
    """La sierra del cortapiedras lleva ``tintindex: 0`` pero no tiene color de bloque."""
    builder = make_setup(
        {
            blockstate_path("minecraft:stonecutter"): {
                "variants": {"facing=north": {"model": "test:block/stonecutter"}}
            },
            model_path("test:block/stonecutter"): {
                "textures": {"saw": "minecraft:block/stonecutter_saw"},
                "elements": [
                    {
                        "from": [1, 9, 8],
                        "to": [15, 16, 8],
                        "faces": {
                            "north": {
                                "uv": [1, 9, 15, 16],
                                "texture": "#saw",
                                "tintindex": 0,
                            },
                            "south": {
                                "uv": [15, 9, 1, 16],
                                "texture": "#saw",
                                "tintindex": 0,
                            },
                        },
                    }
                ],
            },
            texture_path("minecraft:block/stonecutter_saw"): make_png(),
        }
    )
    state = BlockState("minecraft:stonecutter", (("facing", "north"),))
    region = _region([AIR, state], [1], (1, 1, 1))
    mesh = builder.build(_doc([region], (1, 1, 1)))

    plain = mesh.atlas.sprite("minecraft:block/stonecutter_saw")
    assert plain is not None
    assert _sprites_of(mesh, plain).sum() == mesh.quad_count
    assert mesh.atlas.sprite("minecraft:block/stonecutter_saw", (0x91, 0xBD, 0x59)) is None


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
