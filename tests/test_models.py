"""Tests de resolución de blockstates y modelos (F2).

Los tests unitarios usan una fuente en memoria con blockstates/modelos sintéticos, de modo
que no dependen del client jar. Los tests de integración (marcados ``jar``) comprueban la
cobertura real sobre los esquemáticos de la máquina.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from _jar import jar_resources

from lite2obj.assets import (
    MemorySource,
    ResourceManager,
    blockstate_path,
    model_path,
    parse_resource_id,
    texture_path,
)
from lite2obj.fallbacks import FallbackResolver, fluid_cube, fluid_height
from lite2obj.litematic import BlockState
from lite2obj.models import (
    BLOCK_CENTER,
    BlockModelError,
    ElementRotation,
    ModelResolver,
    default_uv,
    parse_condition,
    rot_x,
    rot_y,
    rot_z,
    variant_matrix,
)


def make_manager(entries: dict[str, object]) -> ResourceManager:
    """Crea un ResourceManager con un blockstate/modelo/formato dado en dicts."""
    encoded: dict[str, bytes] = {}
    for path, value in entries.items():
        if isinstance(value, bytes):
            encoded[path] = value
        else:
            encoded[path] = json.dumps(value).encode()
    return ResourceManager([MemorySource(encoded)])


def _cube_model(texture: str = "minecraft:block/stone") -> dict:
    return {
        "textures": {"all": texture, "particle": "#all"},
        "elements": [
            {
                "from": [0, 0, 0],
                "to": [16, 16, 16],
                "faces": {
                    d: {"texture": "#all", "cullface": d}
                    for d in ("down", "up", "north", "south", "west", "east")
                },
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Matemática de rotación
# --------------------------------------------------------------------------- #


def test_rot_y_is_clockwise_seen_from_above():
    # La rotación y de Minecraft lleva el norte (-Z) hacia el este (+X) con y=90.
    assert np.allclose(rot_y(90) @ np.array([0, 0, -1]), [1, 0, 0])
    # y=180: norte -> sur
    assert np.allclose(rot_y(180) @ np.array([0, 0, -1]), [0, 0, 1])
    # y=270: norte -> oeste
    assert np.allclose(rot_y(270) @ np.array([0, 0, -1]), [-1, 0, 0])


def test_rot_x_matches_observer_convention():
    # Observer: facing=down usa x=90 y su frente (norte) debe quedar mirando abajo.
    assert np.allclose(rot_x(90) @ np.array([0, 0, -1]), [0, -1, 0], atol=1e-9)
    assert np.allclose(rot_x(270) @ np.array([0, 0, -1]), [0, 1, 0], atol=1e-9)


def test_rot_z_is_standard_right_handed():
    assert np.allclose(rot_z(90) @ np.array([1, 0, 0]), [0, 1, 0], atol=1e-9)


def test_element_rotation_uses_joml_right_hand_rule():
    # Los elementos no comparten la inversión de x/y de los variants: JOML gira con la
    # mano derecha. Si se invierte, el gancho del tripwire queda por debajo del bloque.
    x_rotation = ElementRotation(origin=(0.0, 0.0, 0.0), axis="x", angle=90.0)
    y_rotation = ElementRotation(origin=(0.0, 0.0, 0.0), axis="y", angle=90.0)
    assert np.allclose(x_rotation.matrix_for(np.zeros(3), np.ones(3)) @ [0, 1, 0], [0, 0, 1])
    assert np.allclose(y_rotation.matrix_for(np.zeros(3), np.ones(3)) @ [1, 0, 0], [0, 0, -1])


def test_variant_matrix_applies_x_then_y():
    # end_rod facing=east usa x=90, y=90 y la varilla (vertical) debe apuntar al este.
    matrix = variant_matrix(90, 90)
    assert np.allclose(matrix @ np.array([0, 1, 0]), [1, 0, 0], atol=1e-9)
    # Orden distinto daría norte, así que comprobamos explícitamente que no es ese.
    assert not np.allclose(matrix @ np.array([0, 1, 0]), [0, 0, -1], atol=1e-9)


def test_variant_matrix_identity():
    assert np.allclose(variant_matrix(0, 0), np.eye(3))


# --------------------------------------------------------------------------- #
# UV por defecto
# --------------------------------------------------------------------------- #


def test_default_uv_faces():
    frm = np.array([0.0, 0.0, 0.0])
    to = np.array([16.0, 16.0, 16.0])
    assert default_uv(frm, to, "up") == (0.0, 0.0, 16.0, 16.0)
    assert default_uv(frm, to, "north") == (0.0, 0.0, 16.0, 16.0)
    assert default_uv(frm, to, "down") == (0.0, 0.0, 16.0, 16.0)
    # Una caja a media altura cambia la UV vertical de las caras laterales.
    half = np.array([0.0, 0.0, 0.0])
    top = np.array([16.0, 8.0, 16.0])
    assert default_uv(half, top, "north") == (0.0, 8.0, 16.0, 16.0)


# --------------------------------------------------------------------------- #
# Selección de variants
# --------------------------------------------------------------------------- #


def test_variant_exact_match():
    manager = make_manager(
        {
            blockstate_path("test:block"): {
                "variants": {
                    "facing=north": {"model": "test:block/north"},
                    "facing=south": {"model": "test:block/south"},
                }
            }
        }
    )
    resolver = ModelResolver(manager)
    state = resolver.resolve(BlockState("test:block", (("facing", "south"),)))
    assert [v.model for v in state.variants] == ["test:block/south"]


def test_variant_picks_most_specific_and_tolerates_extra_properties():
    manager = make_manager(
        {
            blockstate_path("test:block"): {
                "variants": {
                    "": {"model": "test:block/default"},
                    "powered=true": {"model": "test:block/on"},
                }
            }
        }
    )
    resolver = ModelResolver(manager)
    # Propiedad que la definición no usa: se ignora al no encontrar coincidencia.
    state = resolver.resolve(BlockState("test:block", (("powered", "true"), ("stale", "1"))))
    assert [v.model for v in state.variants] == ["test:block/on"]


def test_multipart_without_matching_part_raises():
    resolver = ModelResolver(
        make_manager(
            {
                blockstate_path("test:block"): {
                    "multipart": [{"when": {"north": "true"}, "apply": {"model": "test:arm"}}]
                }
            }
        )
    )
    with pytest.raises(BlockModelError):
        resolver.resolve(BlockState("test:block", (("north", "false"),)))


def test_multipart_conditions_or_and():
    manager = make_manager(
        {
            blockstate_path("test:block"): {
                "multipart": [
                    {"apply": {"model": "test:block/post"}},
                    {"when": {"north": "true"}, "apply": {"model": "test:block/arm", "y": 0}},
                    {
                        "when": {"OR": [{"east": "true"}, {"west": "true"}]},
                        "apply": {"model": "test:block/side"},
                    },
                ]
            }
        }
    )
    resolver = ModelResolver(manager)
    state = resolver.resolve(
        BlockState("test:block", (("north", "false"), ("east", "true"), ("west", "false")))
    )
    models = [v.model for v in state.variants]
    assert "test:block/post" in models
    assert "test:block/side" in models
    assert "test:block/arm" not in models


def test_multipart_and_nested():
    cond = parse_condition({"AND": [{"a": "1"}, {"OR": [{"b": "2"}, {"c": "3"}]}]})
    assert cond.matches({"a": "1", "b": "2"})
    assert cond.matches({"a": "1", "c": "3"})
    assert not cond.matches({"a": "1", "b": "9"})
    assert not cond.matches({"b": "2"})


def test_variant_weighted_choice_is_deterministic():
    manager = make_manager(
        {
            blockstate_path("test:block"): {
                "variants": {
                    "": [
                        {"model": "test:block/a", "weight": 3},
                        {"model": "test:block/b", "weight": 1},
                    ]
                }
            }
        }
    )
    resolver = ModelResolver(manager)
    # La elección es determinista por estado y siempre uno de los modelos declarados.
    picks = {
        resolver.resolve(BlockState("test:block")).variants[0].model,
        resolver.resolve(BlockState("test:block")).variants[0].model,
    }
    assert picks <= {"test:block/a", "test:block/b"}
    assert len(picks) == 1


# --------------------------------------------------------------------------- #
# Cadena de parent, texturas y elementos
# --------------------------------------------------------------------------- #


def test_parent_chain_merges_textures_and_overrides_elements():
    manager = make_manager(
        {
            model_path("test:base"): {
                "textures": {"all": "minecraft:block/stone", "side": "#all"},
                "elements": [
                    {"from": [0, 0, 0], "to": [16, 16, 16], "faces": {"up": {"texture": "#all"}}}
                ],
            },
            model_path("test:child"): {
                "parent": "test:base",
                "textures": {"all": "minecraft:block/oak_planks"},
            },
            model_path("test:grandchild"): {
                "parent": "test:child",
                "elements": [
                    {"from": [0, 0, 0], "to": [16, 8, 16], "faces": {"up": {"texture": "#all"}}}
                ],
            },
            blockstate_path("test:block"): {"variants": {"": {"model": "test:grandchild"}}},
        }
    )
    resolver = ModelResolver(manager)
    model = resolver.model("test:grandchild")

    # La textura del hijo gana sobre la del abuelo.
    assert model.texture("#all") == "minecraft:block/oak_planks"
    # El alias del padre (#side -> #all) se sigue resolviendo.
    assert model.texture("#side") == "minecraft:block/oak_planks"
    # Los elementos del nieto reemplazan a los del abuelo (media altura).
    assert len(model.elements) == 1
    assert model.elements[0].to == (16.0, 8.0, 16.0)


def test_texture_dict_sprite_format():
    manager = make_manager(
        {
            model_path("test:m"): {"textures": {"all": {"sprite": "minecraft:block/glass"}}},
            blockstate_path("test:block"): {"variants": {"": {"model": "test:m"}}},
        }
    )
    resolver = ModelResolver(manager)
    assert resolver.model("test:m").texture("#all") == "minecraft:block/glass"


def test_missing_texture_reference_returns_none():
    manager = make_manager(
        {
            model_path("test:m"): {"textures": {"a": "#b"}},
            blockstate_path("test:block"): {"variants": {"": {"model": "test:m"}}},
        }
    )
    resolver = ModelResolver(manager)
    assert resolver.model("test:m").texture("#a") is None
    assert resolver.model("test:m").texture(None) is None


def test_missing_model_raises():
    manager = make_manager(
        {blockstate_path("test:block"): {"variants": {"": {"model": "test:nope"}}}}
    )
    resolver = ModelResolver(manager)
    with pytest.raises(BlockModelError):
        resolver.model("test:nope")


def test_element_rotation_applies_about_origin():
    from lite2obj.models import Element, ElementFace, ElementRotation

    element = Element(
        frm=(7.0, 0.0, 7.0),
        to=(9.0, 16.0, 9.0),
        faces={"up": ElementFace(texture="#t")},
        rotation=ElementRotation(origin=(0.0, 0.0, 8.0), axis="z", angle=-45.0),
    )
    corners = element.corners()
    # La punta superior gira hacia +X (mano derecha) alrededor del origen.
    top = corners[np.argmax(corners[:, 1])]
    assert top[0] > 9.0


def test_element_rotation_rescale_keeps_size():
    from lite2obj.models import Element, ElementFace, ElementRotation

    rotation = ElementRotation(origin=(0.0, 0.0, 8.0), axis="z", angle=-45.0, rescale=True)
    element = Element(
        frm=(7.0, 0.0, 7.0),
        to=(9.0, 16.0, 9.0),
        faces={"up": ElementFace(texture="#t")},
        rotation=rotation,
    )
    matrix = rotation.matrix_for(np.array([7.0, 0.0, 7.0]), np.array([9.0, 16.0, 9.0]))
    # La escala debe ser unitaria en Z (el eje de giro) y mayor en el plano girado.
    assert matrix[2, 2] == pytest.approx(1.0)
    assert not np.allclose(matrix[:2, :2], rot_z(-45.0)[:2, :2])
    assert element.corners().shape == (8, 3)


# --------------------------------------------------------------------------- #
# Fallbacks y alias
# --------------------------------------------------------------------------- #


def test_fallback_cube_from_particle_texture():
    manager = make_manager(
        {
            model_path("test:entity_block"): {
                "textures": {"particle": "minecraft:block/oak_planks"}
            },
            blockstate_path("test:block"): {"variants": {"": {"model": "test:entity_block"}}},
        }
    )
    resolver = ModelResolver(manager)
    geometry = resolver.geometry(BlockState("test:block"))
    assert len(geometry) == 1
    element, _ = geometry[0]
    assert element.frm == (0.0, 0.0, 0.0)
    assert element.to == (16.0, 16.0, 16.0)
    assert all(f.texture == "minecraft:block/oak_planks" for f in element.faces.values())


def test_fallback_disabled_with_none_mode():
    manager = make_manager(
        {
            model_path("test:entity_block"): {
                "textures": {"particle": "minecraft:block/oak_planks"}
            },
            blockstate_path("test:block"): {"variants": {"": {"model": "test:entity_block"}}},
        }
    )
    resolver = ModelResolver(manager, fallback_mode="none")
    assert resolver.geometry(BlockState("test:block")) == []


def test_water_fallback_uses_fluid_texture():
    manager = make_manager({blockstate_path("test:block"): {"variants": {"": {"model": "test:m"}}}})
    resolver = ModelResolver(manager)
    geometry = resolver.geometry(BlockState("minecraft:water"))
    assert len(geometry) == 1
    element, _ = geometry[0]
    assert element.faces["up"].texture == "minecraft:block/water_still"


def test_fluid_cube_height_tint_and_cullface():
    """El fluido no llena el bloque, se tiñe y participa en el descarte de caras."""
    source = fluid_cube("water")
    assert source.to[1] == pytest.approx(16.0 * 8.0 / 9.0)
    assert source.faces["up"].tintindex == 0
    assert source.faces["up"].cullface == "up"
    assert source.faces["down"].cullface == "down"

    stacked = fluid_cube("water", fluid_above=True)
    assert stacked.to[1] == 16.0

    assert fluid_height(3) == pytest.approx(16.0 * 5.0 / 9.0)
    assert fluid_height(0, fluid_above=True) == 16.0

    # La lava ya viene coloreada: no se tiñe.
    assert fluid_cube("lava").faces["up"].tintindex is None

    # Los esquemáticos antiguos pueden traer el identificador ``flowing_water``.
    assert FallbackResolver().is_fluid("flowing_water") == "water"
    # La columna de burbujas se dibuja como agua aunque no sea un bloque de fluido.
    assert FallbackResolver().is_fluid("bubble_column") == "water"


def test_alias_chain_becomes_iron_chain():
    from lite2obj.aliases import normalize_block_name

    assert normalize_block_name("minecraft:chain") == "minecraft:iron_chain"
    assert normalize_block_name("minecraft:stone") == "minecraft:stone"


def test_alias_applied_during_resolution():
    manager = make_manager(
        {
            blockstate_path("minecraft:iron_chain"): {
                "variants": {"": {"model": "minecraft:block/chain"}}
            },
            model_path("minecraft:block/chain"): _cube_model("minecraft:block/chain"),
        }
    )
    resolver = ModelResolver(manager)
    state = resolver.resolve(BlockState("minecraft:chain"))
    assert state.model_name == "minecraft:iron_chain"
    assert state.used_alias
    assert resolver.geometry(BlockState("minecraft:chain"))


def test_alias_resolution_does_not_depend_on_cache_order():
    manager = make_manager(
        {
            blockstate_path("minecraft:iron_chain"): {
                "variants": {"": {"model": "minecraft:block/chain"}}
            },
            model_path("minecraft:block/chain"): _cube_model("minecraft:block/chain"),
        }
    )
    resolver = ModelResolver(manager)
    # Resolver primero el nombre actual no debe contaminar al alias cacheado.
    canonical = resolver.resolve(BlockState("minecraft:iron_chain"))
    alias = resolver.resolve(BlockState("minecraft:chain"))

    assert not canonical.used_alias
    assert canonical.block.name == "minecraft:iron_chain"
    assert alias.used_alias
    assert alias.block.name == "minecraft:chain"
    assert alias.model_name == canonical.model_name


# --------------------------------------------------------------------------- #
# Utilidades de rutas
# --------------------------------------------------------------------------- #


def test_resource_path_helpers():
    assert parse_resource_id("block/stone") == ("minecraft", "block/stone")
    assert parse_resource_id("mod:block/x") == ("mod", "block/x")
    assert parse_resource_id(None) == ("minecraft", "")
    assert blockstate_path("minecraft:oak_stairs") == "assets/minecraft/blockstates/oak_stairs.json"
    assert model_path("block/cube") == "assets/minecraft/models/block/cube.json"
    assert texture_path("minecraft:block/stone") == "assets/minecraft/textures/block/stone.png"
    assert texture_path("minecraft:block/stone.png").endswith("stone.png.png") is False


# --------------------------------------------------------------------------- #
# Integración con el jar real
# --------------------------------------------------------------------------- #


def test_real_jar_cube_and_stairs():
    resolver = ModelResolver(jar_resources())
    stone = resolver.geometry(BlockState("minecraft:stone"))
    assert len(stone) == 1
    assert len(stone[0][0].faces) == 6
    assert resolver.model("minecraft:block/stone").texture("#all") == "minecraft:block/stone"

    stairs = BlockState(
        "minecraft:oak_stairs",
        (("facing", "east"), ("half", "bottom"), ("shape", "straight"), ("waterlogged", "false")),
    )
    geometry = resolver.geometry(stairs)
    assert len(geometry) == 2
    # La parte alta de la escalera está en el lado este (x 8..16).
    upper = max(geometry, key=lambda item: item[0].to[1])
    assert upper[0].frm[0] == 8.0


def test_real_jar_tripwire_hook_folds_up_inside_the_block():
    """El gancho sin conectar se pliega hacia arriba, no hacia abajo del bloque."""
    resolver = ModelResolver(jar_resources())
    hook_state = BlockState(
        "minecraft:tripwire_hook",
        (("attached", "false"), ("facing", "north"), ("powered", "false")),
    )
    geometry = resolver.geometry(hook_state)
    hook = max(
        (element for element, _ in geometry if element.rotation is not None),
        key=lambda element: np.prod(np.array(element.to) - np.array(element.frm)),
    )
    corners = hook.corners()
    assert corners[:, 1].min() >= 0.0
    assert corners[:, 1].max() <= 16.0
    assert corners[:, 1].max() > 8.0


def test_real_jar_end_rod_rotation_points_east():
    resolver = ModelResolver(jar_resources())
    geometry = resolver.geometry(BlockState("minecraft:end_rod", (("facing", "east"),)))
    rod = max(geometry, key=lambda item: item[0].to[1])[0]
    variant = max(geometry, key=lambda item: item[0].to[1])[1]
    matrix = variant_matrix(variant.x, variant.y)
    frm, to = rod.box()
    tip = matrix @ (to - BLOCK_CENTER) + BLOCK_CENTER
    assert tip[0] == pytest.approx(16.0)
    assert tip[1] == pytest.approx(9.0)


def test_real_jar_multipart_fence_and_glass_pane():
    resolver = ModelResolver(jar_resources())
    fence = BlockState(
        "minecraft:oak_fence",
        (
            ("east", "false"),
            ("north", "false"),
            ("south", "false"),
            ("waterlogged", "false"),
            ("west", "true"),
        ),
    )
    variants = resolver.resolve(fence).variants
    assert any(v.model.endswith("fence_post") for v in variants)
    assert any(v.model.endswith("fence_side") and v.y == 270 for v in variants)

    pane = BlockState(
        "minecraft:glass_pane",
        (
            ("east", "true"),
            ("north", "false"),
            ("south", "false"),
            ("waterlogged", "false"),
            ("west", "false"),
        ),
    )
    assert len(resolver.resolve(pane).variants) >= 2


def test_real_jar_zero_element_fallback_covers_block_entities():
    resolver = ModelResolver(jar_resources())
    # Cofre: modelo sin elementos -> cubo con la textura particle.
    chest = resolver.model("minecraft:block/chest")
    assert chest.elements == ()
    assert chest.particle_texture() == "minecraft:block/oak_planks"
    assert len(resolver.geometry(BlockState("minecraft:chest"))) == 1


def test_real_jar_texture_refs_resolve_to_existing_pngs():
    resources = jar_resources()
    resolver = ModelResolver(resources)
    for name in ("minecraft:stone", "minecraft:oak_planks", "minecraft:glass", "minecraft:water"):
        for element, variant in resolver.geometry(BlockState(name)):
            model = resolver.model(variant.model)
            for face in element.faces.values():
                texture = model.texture(face.texture)
                assert texture, f"{name}: cara sin textura"
                assert resources.read_texture(texture) is not None, f"{name}: falta {texture}"
