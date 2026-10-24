"""Tests de la aplicación de escritorio (cámara, buffers y arranque).

La parte que necesita Qt se aísla en un subproceso con la plataforma ``offscreen``, de modo
que la batería sigue funcionando en máquinas sin pantalla.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from lite2obj.cli import build_parser
from lite2obj.gui.camera import OrbitCamera, look_at, perspective
from lite2obj.gui.viewport import (
    _matrix_qmatrix,
    edge_vertices,
    interleave,
    sorted_triangles,
    triangle_soup,
)
from lite2obj.mesh import Mesh


def _mesh() -> Mesh:
    quads = np.array([[0, 1, 2, 3], [4, 5, 6, 7]], dtype=np.int32)
    positions = np.arange(24, dtype=np.float32).reshape(8, 3)
    normals = np.zeros((8, 3), dtype=np.float32)
    uvs = np.zeros((8, 2), dtype=np.float32)
    return Mesh(
        positions=positions,
        uvs=uvs,
        normals=normals,
        quads=quads,
        sprite=np.zeros(2, dtype=np.int32),
        alpha_mode=np.array([2, 0], dtype=np.int8),
        block=np.zeros(2, dtype=np.int32),
        palette=[],
        atlas=object(),  # type: ignore[arg-type]
        size=(1, 1, 1),
    )


# --------------------------------------------------------------------------- #
# Cámara
# --------------------------------------------------------------------------- #


def test_fit_centers_target_and_keeps_camera_out():
    camera = OrbitCamera()
    camera.fit((10, 4, 20))
    assert np.allclose(camera.target, [5.0, 2.0, 10.0])
    assert camera.distance > 0
    assert camera.near < camera.far


def test_view_projection_maps_target_to_center():
    camera = OrbitCamera()
    camera.fit((8, 8, 8))
    matrix = camera.view_projection(16 / 9)
    point = np.append(camera.target, 1.0)
    clip = matrix @ point
    ndc = clip[:3] / clip[3]
    assert abs(ndc[0]) < 1e-6
    assert abs(ndc[1]) < 1e-6


def test_orbit_pan_zoom_change_camera():
    camera = OrbitCamera()
    camera.fit((16, 16, 16))
    eye = camera.eye().copy()
    camera.orbit(40, -25)
    assert not np.allclose(camera.eye(), eye)
    assert -math.pi / 2 < camera.pitch < math.pi / 2
    target = camera.target.copy()
    camera.pan(25, 10, 800)
    assert not np.allclose(camera.target, target)
    distance = camera.distance
    camera.zoom(5)
    assert camera.distance < distance


def test_orbit_inverts_vertical_drag_only():
    # Vertical invertido: arrastrar hacia abajo (dy>0) sube la cámara (pitch aumenta).
    camera = OrbitCamera()
    pitch = camera.pitch
    camera.orbit(0, 30)
    assert camera.pitch > pitch
    # Horizontal sin tocar: a la derecha sigue girando igual (yaw disminuye).
    camera = OrbitCamera()
    yaw = camera.yaw
    camera.orbit(30, 0)
    assert camera.yaw < yaw
    assert camera.pitch == math.radians(28.0)


def test_qmatrix_conversion_is_column_major():
    matrix = perspective(60.0, 1.5, 0.1, 100.0)
    converted = np.array(_matrix_qmatrix(matrix).data(), dtype=np.float64).reshape(4, 4)
    assert np.allclose(converted, matrix.T)

    view = look_at(np.zeros(3), np.array([0.0, 0.0, -1.0]), np.array([0.0, 1.0, 0.0]))
    view_data = np.array(_matrix_qmatrix(view).data(), dtype=np.float64).reshape(4, 4)
    assert np.allclose(view_data, np.eye(4))


# --------------------------------------------------------------------------- #
# Buffers del viewport
# --------------------------------------------------------------------------- #


def test_interleave_packs_position_normal_uv():
    mesh = _mesh()
    data = interleave(mesh)
    assert data.shape == (8, 8)
    assert np.allclose(data[0, 0:3], mesh.positions[0])
    assert np.allclose(data[0, 3:6], mesh.normals[0])
    assert np.allclose(data[0, 6:8], mesh.uvs[0])


def test_sorted_triangles_groups_by_alpha_mode():
    triangles, groups = sorted_triangles(_mesh())
    assert groups == [(0, 0, 2), (2, 2, 2)]
    assert triangles.shape == (4, 3)
    # Los triángulos opacos (segunda cara) van primero.
    assert np.all(triangles[0] >= 4)
    assert np.all(triangles[2] < 4)


def test_triangle_soup_is_contiguous_by_group():
    soup, groups = triangle_soup(_mesh())
    assert soup.shape == (12, 8)
    assert groups == [(0, 0, 6), (2, 6, 6)]
    # El primer vértice de la sopa es el primero del triángulo opaco.
    assert np.allclose(soup[0, 0:3], _mesh().positions[4])


def test_edge_vertices_has_two_points_per_edge():
    points = edge_vertices(_mesh())
    assert points.shape == (16, 3)
    assert np.allclose(points[0], _mesh().positions[0])
    assert np.allclose(points[1], _mesh().positions[1])


# --------------------------------------------------------------------------- #
# CLI y opciones
# --------------------------------------------------------------------------- #


def test_cli_has_gui_and_not_view():
    parser = build_parser()
    assert parser.parse_args(["gui"]).func.__name__ == "cmd_gui"
    with pytest.raises(SystemExit):
        parser.parse_args(["view"])


def test_cli_export_accepts_atlas_scale_and_scale():
    parser = build_parser()
    args = parser.parse_args(["export", "a.litematic", "--atlas-scale", "4", "--scale", "0.5"])
    assert args.atlas_scale == 4
    assert args.scale == 0.5


def test_cli_export_accepts_no_entities():
    parser = build_parser()
    args = parser.parse_args(["export", "a.litematic", "--no-entities"])
    assert args.no_entities is True
    assert parser.parse_args(["export", "a.litematic"]).no_entities is False


def test_cli_export_accepts_mobs():
    parser = build_parser()
    args = parser.parse_args(["export", "a.litematic", "--mobs"])
    assert args.mobs is True
    assert parser.parse_args(["export", "a.litematic"]).mobs is False


def test_render_options_pass_atlas_scale_to_mesh():
    from lite2obj.pipeline import RenderOptions

    options = RenderOptions(cull=False, tint=False, atlas_scale=2)
    mesh_options = options.mesh_options()
    assert mesh_options.atlas_scale == 2
    assert mesh_options.cull is False
    assert mesh_options.tint is False
    assert mesh_options.entities is True


def test_render_options_can_disable_entities():
    from lite2obj.pipeline import RenderOptions

    assert RenderOptions(entities=False).mesh_options().entities is False
    assert RenderOptions(entities=False, mobs=True).mesh_options().entities is False


def test_render_options_mobs_are_off_by_default():
    from lite2obj.pipeline import RenderOptions

    assert RenderOptions().mobs is False
    assert RenderOptions().mesh_options().mobs is False
    assert RenderOptions(mobs=True).mesh_options().mobs is True
