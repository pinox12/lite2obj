"""Alias de bloques renombrados entre versiones de Minecraft.

Los esquemáticos ``.litematic`` guardan los identificadores de bloque de la versión en la
que se crearon. Si un bloque se renombró en una versión posterior (o se dividió, como
``chain`` en ``iron_chain``), el blockstate ya no existe en los assets actuales. Esta tabla
permite seguir renderizándolos.

La tabla es deliberadamente conservadora y extensible: sólo incluye renombrados inequívocos
de ``nombre -> nombre``. Los renombrados que dependen de propiedades (p. ej. los
``variant``/``color`` de 1.12) están fuera de alcance.
"""

from __future__ import annotations

__all__ = ["BLOCK_ALIASES", "normalize_block_name", "rename_properties"]

#: Renombrados de bloque: antiguo -> actual.
BLOCK_ALIASES: dict[str, str] = {
    # --- recientes (1.20 -> 26.x) ---
    "minecraft:grass": "minecraft:short_grass",
    "minecraft:chain": "minecraft:iron_chain",
    "minecraft:grass_path": "minecraft:dirt_path",
    "minecraft:sculk_sensor": "minecraft:sculk_sensor",
    # --- aplanado de 1.13 ---
    "minecraft:grass_block": "minecraft:grass_block",
    "minecraft:reeds": "minecraft:sugar_cane",
    "minecraft:waterlily": "minecraft:lily_pad",
    "minecraft:deadbush": "minecraft:dead_bush",
    "minecraft:yellow_flower": "minecraft:dandelion",
    "minecraft:red_flower": "minecraft:poppy",
    "minecraft:hardened_clay": "minecraft:terracotta",
    "minecraft:stained_hardened_clay": "minecraft:terracotta",
    "minecraft:nether_brick": "minecraft:nether_bricks",
    "minecraft:red_nether_brick": "minecraft:red_nether_bricks",
    "minecraft:sign": "minecraft:oak_sign",
    "minecraft:standing_sign": "minecraft:oak_sign",
    "minecraft:wall_sign": "minecraft:oak_wall_sign",
    "minecraft:wooden_door": "minecraft:oak_door",
    "minecraft:wooden_stairs": "minecraft:oak_stairs",
    "minecraft:wooden_slab": "minecraft:oak_slab",
    "minecraft:wooden_button": "minecraft:oak_button",
    "minecraft:wooden_pressure_plate": "minecraft:oak_pressure_plate",
    "minecraft:wooden_trapdoor": "minecraft:oak_trapdoor",
    "minecraft:fence": "minecraft:oak_fence",
    "minecraft:fence_gate": "minecraft:oak_fence_gate",
    "minecraft:trapdoor": "minecraft:oak_trapdoor",
    "minecraft:lit_furnace": "minecraft:furnace",
    "minecraft:lit_redstone_lamp": "minecraft:redstone_lamp",
    "minecraft:lit_pumpkin": "minecraft:jack_o_lantern",
    "minecraft:flowing_water": "minecraft:water",
    "minecraft:flowing_lava": "minecraft:lava",
    "minecraft:powered_repeater": "minecraft:repeater",
    "minecraft:unpowered_repeater": "minecraft:repeater",
    "minecraft:powered_comparator": "minecraft:comparator",
    "minecraft:unpowered_comparator": "minecraft:comparator",
}

#: Propiedades que se eliminan al traducir un bloque antiguo al actual (por si sobran).
DROPPED_PROPERTIES: dict[str, frozenset[str]] = {}


def normalize_block_name(name: str) -> str:
    """Traduce un identificador de bloque antiguo al actual si está en la tabla."""
    return BLOCK_ALIASES.get(name, name)


def rename_properties(name: str, properties: dict[str, str]) -> dict[str, str]:
    """Ajusta propiedades al traducir un bloque (por ahora, elimina las obsoletas)."""
    dropped = DROPPED_PROPERTIES.get(name)
    if not dropped:
        return properties
    return {k: v for k, v in properties.items() if k not in dropped}
