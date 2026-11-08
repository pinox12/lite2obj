"""Exportación a OBJ + MTL + atlas (F5).

Todo el esquemático sale como **un solo objeto con un solo material** cuyo ``map_Kd`` es el
atlas. Así Blender importa una única malla con una única textura, que es lo que se quiere para
pintar, iluminar o seguir editando encima.

Detalles del formato que importan:

* **Índices**: OBJ permite índices distintos para vértice/UV/normal. Se deduplican posiciones y
  UVs con :func:`numpy.unique`, lo que reduce bastante el tamaño del archivo.
* **V**: el origen de ``vt`` en OBJ está abajo a la izquierda y en Minecraft arriba a la
  izquierda, así que se invierte (``flip_v``).
* **Orientación**: por defecto se exporta con el eje Y hacia arriba (como Minecraft). Con
  ``up_axis="z"`` se aplica una rotación propia (determinante +1, no cambia el sentido de las
  caras) para obtener coordenadas nativas de Blender.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .i18n import _
from .mesh import Mesh

__all__ = [
    "ExportOptions",
    "ExportResult",
    "export_obj",
    "obj_geometry",
]

_CHUNK = 50_000

#: Decimales que se conservan al escribir el OBJ (0.0625 es la rejilla de 1/16 de píxel).
_DECIMALS = 6


@dataclass(slots=True)
class ExportOptions:
    """Opciones de exportación."""

    name: str = "model"
    up_axis: str = "y"  # "y" (Minecraft) | "z" (Blender)
    group_by: str = "none"  # none | block | alpha
    flip_v: bool = True
    scale: float = 1.0
    atlas_name: str = "atlas.png"
    write_atlas: bool = True
    write_mtl: bool = True
    comments: bool = True


@dataclass(slots=True)
class ExportResult:
    """Rutas y cifras del resultado."""

    obj: Path
    mtl: Path | None
    atlas: Path | None
    vertices: int
    uvs: int
    normals: int
    faces: int
    bytes_written: int

    def as_dict(self) -> dict[str, object]:
        return {
            "obj": str(self.obj),
            "mtl": str(self.mtl) if self.mtl else None,
            "atlas": str(self.atlas) if self.atlas else None,
            "vertices": self.vertices,
            "uvs": self.uvs,
            "normals": self.normals,
            "faces": self.faces,
            "bytes": self.bytes_written,
        }


def _apply_axis(array: np.ndarray, up_axis: str) -> np.ndarray:
    """Rota (x, y, z) -> (x, z, -y) para pasar de Y-arriba a Z-arriba."""
    if up_axis == "y":
        return array
    if up_axis != "z":
        raise ValueError(_("up_axis desconocido: {axis!r}").format(axis=up_axis))
    rotated = np.empty_like(array)
    rotated[..., 0] = array[..., 0]
    rotated[..., 1] = array[..., 2]
    rotated[..., 2] = -array[..., 1]
    return rotated


def obj_geometry(
    mesh: Mesh, options: ExportOptions | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Calcula los arrays deduplicados del OBJ.

    Devuelve ``(posiciones, uvs, normales, índices de posición, índices de UV, índices de
    normal)``. Los índices ya son 1-based y tienen forma ``(Q, 4)`` (la normal, ``(Q,)``).
    """
    options = options or ExportOptions()
    positions = _apply_axis(mesh.positions, options.up_axis).astype(np.float64)
    if options.scale != 1.0:
        positions = positions * options.scale
    # Redondear antes de deduplicar: los vértices compartidos se calculan con sumas distintas
    # y, sin redondear, aparecen duplicados con ruido de coma flotante.
    positions = np.round(positions, _DECIMALS)

    unique_positions, position_inverse = np.unique(positions, axis=0, return_inverse=True)
    unique_uvs, uv_inverse = np.unique(
        np.round(mesh.uvs.astype(np.float64), _DECIMALS), axis=0, return_inverse=True
    )
    normals = np.round(_apply_axis(mesh.normals[::4], options.up_axis), _DECIMALS)
    unique_normals, normal_inverse = np.unique(normals, axis=0, return_inverse=True)

    position_index = position_inverse.reshape(-1, 4).astype(np.int64) + 1
    uv_index = uv_inverse.reshape(-1, 4).astype(np.int64) + 1
    normal_index = normal_inverse.astype(np.int64) + 1

    if options.flip_v:
        unique_uvs = unique_uvs.copy()
        unique_uvs[:, 1] = 1.0 - unique_uvs[:, 1]

    return (
        unique_positions,
        unique_uvs,
        unique_normals,
        position_index,
        uv_index,
        normal_index,
    )


def _group_label(mesh: Mesh, index: int, group_by: str) -> str | None:
    if group_by == "block":
        return _sanitize(mesh.palette[int(mesh.block[index])].name)
    if group_by == "alpha":
        return {0: "opaque", 1: "cutout", 2: "translucent"}[int(mesh.alpha_mode[index])]
    return None


def _sanitize(name: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_" for character in name
    )


def _face_order(mesh: Mesh, group_by: str) -> np.ndarray:
    if group_by == "block":
        return np.argsort(mesh.block, kind="stable")
    if group_by == "alpha":
        return np.argsort(mesh.alpha_mode, kind="stable")
    if group_by != "none":
        raise ValueError(_("group_by desconocido: {value!r}").format(value=group_by))
    return np.arange(mesh.quad_count, dtype=np.int64)


def _format_row(values: np.ndarray, precision: int = _DECIMALS) -> str:
    """Formatea números sin notación científica ni ceros sobrantes (``0`` y no ``-1e-16``)."""
    parts = []
    for value in values.tolist():
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        if text in ("", "-0"):
            text = "0"
        parts.append(text)
    return " ".join(parts)


def export_obj(
    mesh: Mesh,
    directory: str | Path,
    options: ExportOptions | None = None,
) -> ExportResult:
    """Escribe ``<name>.obj``, ``<name>.mtl`` y ``atlas.png`` en ``directory``."""
    options = options or ExportOptions()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    obj_path = directory / f"{options.name}.obj"
    mtl_path = directory / f"{options.name}.mtl" if options.write_mtl else None
    atlas_path = directory / options.atlas_name if options.write_atlas else None

    positions, uvs, normals, position_index, uv_index, normal_index = obj_geometry(mesh, options)
    order = _face_order(mesh, options.group_by)

    with obj_path.open("w", encoding="utf-8", newline="\n") as handle:
        if options.comments:
            handle.write(
                _("# lite2obj - {faces} caras, {vertices} vertices\n").format(
                    faces=mesh.quad_count, vertices=len(positions)
                )
            )
            handle.write(
                _("# origen {origin}  tamano {size}\n").format(origin=mesh.origin, size=mesh.size)
            )
        if mtl_path is not None:
            handle.write(f"mtllib {mtl_path.name}\n")

        handle.writelines(f"v {_format_row(row)}\n" for row in positions)
        handle.writelines(f"vt {_format_row(row)}\n" for row in uvs)
        handle.writelines(f"vn {_format_row(row)}\n" for row in normals)

        if options.write_mtl:
            handle.write("usemtl atlas\n")

        buffer: list[str] = []
        current_group: str | None = None
        for index in order:
            group = _group_label(mesh, int(index), options.group_by)
            if group != current_group:
                if group is not None:
                    buffer.append(f"g {group}\n")
                current_group = group
            quad = position_index[index]
            uv = uv_index[index]
            normal = int(normal_index[index])
            buffer.append(
                f"f {quad[0]}/{uv[0]}/{normal} {quad[1]}/{uv[1]}/{normal} "
                f"{quad[2]}/{uv[2]}/{normal} {quad[3]}/{uv[3]}/{normal}\n"
            )
            if len(buffer) >= _CHUNK:
                handle.writelines(buffer)
                buffer.clear()
        handle.writelines(buffer)

    if mtl_path is not None:
        _write_mtl(mtl_path, options)
    if atlas_path is not None:
        mesh.atlas.image.save(atlas_path, format="PNG", optimize=True)

    return ExportResult(
        obj=obj_path,
        mtl=mtl_path,
        atlas=atlas_path,
        vertices=len(positions),
        uvs=len(uvs),
        normals=len(normals),
        faces=mesh.quad_count,
        bytes_written=obj_path.stat().st_size,
    )


def _write_mtl(path: Path, options: ExportOptions) -> None:
    lines = [
        "# lite2obj\n",
        "newmtl atlas\n",
        "Ka 1.000 1.000 1.000\n",
        "Kd 1.000 1.000 1.000\n",
        "Ks 0.000 0.000 0.000\n",
        "Ke 0.000 0.000 0.000\n",
        "Ns 0.000\n",
        "d 1.000\n",
        "illum 1\n",
        f"map_Kd {options.atlas_name}\n",
        f"map_d {options.atlas_name}\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")
