"""Pipeline completo: ``.litematic`` -> malla -> OBJ/JSON (F6).

Reúne las piezas de las fases anteriores en funciones de alto nivel que usan tanto la CLI como
el servidor web. Aquí también vive el formato binario que consume el visor: los vértices van en
un único búfer contiguo y el JSON sólo lleva el mapa de partes (nombre, tipo, desplazamiento),
de modo que el navegador crea los ``BufferAttribute`` sin copiar nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .assets import ResourceManager
from .export import ExportOptions, ExportResult, export_obj
from .litematic import Litematic, load
from .mesh import Mesh, MeshBuilder, MeshOptions
from .models import ModelResolver

__all__ = [
    "ALPHA_MODES",
    "RenderOptions",
    "build_resources",
    "export",
    "mesh_bundle",
    "mesh_meta",
    "mesh_parts",
    "render",
]

#: Orden de los pases de dibujado del visor.
ALPHA_MODES = ("opaque", "cutout", "translucent")

#: Partes del búfer binario: ``(nombre, dtype, componentes)``.
_PARTS: tuple[tuple[str, str, int], ...] = (
    ("position", "float32", 3),
    ("normal", "float32", 3),
    ("uv", "float32", 2),
    ("index", "uint32", 1),
)


@dataclass(slots=True)
class RenderOptions:
    """Opciones de render compartidas por la CLI y la API."""

    packs: list[str] = field(default_factory=list)
    cull: bool = True
    tint: bool = True
    padding: int = 2
    max_quads: int = 0
    fallback_mode: str = "cube"

    def mesh_options(self) -> MeshOptions:
        return MeshOptions(
            cull=self.cull,
            tint=self.tint,
            padding=self.padding,
            max_quads=self.max_quads,
            fallback_mode=self.fallback_mode,
        )


def build_resources(options: RenderOptions | None = None) -> ResourceManager:
    """Resource manager con el jar vanilla y los resource packs indicados."""
    options = options or RenderOptions()
    return ResourceManager.discover(packs=list(options.packs))


def render(
    source: str | Path | Litematic,
    options: RenderOptions | None = None,
    *,
    resources: ResourceManager | None = None,
    resolver: ModelResolver | None = None,
) -> Mesh:
    """Carga (si hace falta) y convierte el esquemático en una malla."""
    options = options or RenderOptions()
    document = source if isinstance(source, Litematic) else load(source)
    if resolver is None:
        resolver = ModelResolver(
            resources or build_resources(options), fallback_mode=options.fallback_mode
        )
    builder = MeshBuilder(resolver, options=options.mesh_options())
    return builder.build(document)


def export(
    source: str | Path | Litematic,
    directory: str | Path,
    options: RenderOptions | None = None,
    *,
    name: str | None = None,
    up_axis: str = "y",
    group_by: str = "none",
    resources: ResourceManager | None = None,
) -> ExportResult:
    """Renderiza y escribe ``<name>.obj``, ``<name>.mtl`` y ``atlas.png``."""
    options = options or RenderOptions()
    document = source if isinstance(source, Litematic) else load(source)
    mesh = render(document, options, resources=resources)
    export_options = ExportOptions(
        name=name or _safe_name(document.name or getattr(source, "stem", None) or "model"),
        up_axis=up_axis,
        group_by=group_by,
    )
    return export_obj(mesh, directory, export_options)


def _safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in name.strip()).strip("_")
    return cleaned or "model"


# --------------------------------------------------------------------------- #
# Formato binario para el visor
# --------------------------------------------------------------------------- #


def mesh_parts(mesh: Mesh) -> tuple[bytes, list[dict[str, Any]], list[dict[str, Any]]]:
    """Búfer binario contiguo + descripción de partes + grupos de dibujado.

    Los triángulos se reordenan por modo de alfa (opaco, cutout, translúcido) para que el visor
    pueda dibujar los translúcidos al final, que es lo que exige una transparencia correcta.
    """
    triangles = mesh.triangle_indices().astype(np.uint32)
    triangle_alpha = np.concatenate([mesh.alpha_mode, mesh.alpha_mode]).astype(np.int8)
    order = np.argsort(triangle_alpha, kind="stable")
    triangles = triangles[order]
    triangle_alpha = triangle_alpha[order]

    arrays: dict[str, np.ndarray] = {
        "position": np.ascontiguousarray(mesh.positions, dtype=np.float32).reshape(-1),
        "normal": np.ascontiguousarray(mesh.normals, dtype=np.float32).reshape(-1),
        "uv": np.ascontiguousarray(mesh.uvs, dtype=np.float32).reshape(-1),
        "index": np.ascontiguousarray(triangles, dtype=np.uint32).reshape(-1),
    }

    chunks: list[bytes] = []
    parts: list[dict[str, Any]] = []
    offset = 0
    for name, dtype, components in _PARTS:
        array = arrays[name]
        data = array.tobytes()
        count = array.size // components
        parts.append(
            {
                "name": name,
                "dtype": dtype,
                "components": components,
                "count": count,
                "offset": offset,
                "byteLength": len(data),
            }
        )
        chunks.append(data)
        offset += len(data)

    groups: list[dict[str, Any]] = []
    start = 0
    for mode, label in enumerate(ALPHA_MODES):
        count = int(np.count_nonzero(triangle_alpha == mode))
        if count:
            groups.append({"mode": label, "start": start, "count": count})
        start += count

    return b"".join(chunks), parts, groups


def mesh_meta(mesh: Mesh) -> dict[str, Any]:
    """Metadatos del esquemático para el visor (sin la geometría)."""
    _, parts, groups = mesh_parts(mesh)
    return _meta(mesh, parts, groups)


def mesh_bundle(mesh: Mesh) -> tuple[bytes, dict[str, Any]]:
    """Búfer y metadatos en una sola pasada (lo que usa el servidor)."""
    buffer, parts, groups = mesh_parts(mesh)
    return buffer, _meta(mesh, parts, groups)


def _meta(mesh: Mesh, parts: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "size": list(mesh.size),
        "origin": list(mesh.origin),
        "partial": mesh.partial,
        "atlas": {"width": mesh.atlas.size[0], "height": mesh.atlas.size[1]},
        "parts": parts,
        "groups": groups,
        "stats": mesh.stats(),
        "palette": sorted({block.name for block in mesh.palette}),
    }
