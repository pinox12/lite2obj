"""Viewport OpenGL del visor de escritorio.

Usa las clases OpenGL de Qt (`QOpenGLShaderProgram`, `QOpenGLBuffer`, `QOpenGLTexture`...)
porque son las que garantizan compartir el contexto del :class:`QOpenGLWidget`; con un
contexto externo al de Qt el dibujo puede acabar en un framebuffer que nunca se
presenta. Los triángulos se suben como *soup* ordenada por modo de alfa, de modo que cada
pase se dibuja con un simple ``glDrawArrays``.

El fragment shader hace el recorte por capas (uniform ``u_y_min``/``u_y_max``) y el
*alpha test* de los materiales *cutout*.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMatrix4x4,
    QMouseEvent,
    QOpenGLFunctions,
    QVector4D,
    QWheelEvent,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..mesh import Mesh
from .camera import OrbitCamera

__all__ = ["Viewport", "edge_vertices", "interleave", "sorted_triangles", "triangle_soup"]

# Constantes GL que Qt no reexporta como enums.
_GL_FLOAT = 0x1406
_GL_TRIANGLES = 0x0004
_GL_LINES = 0x0001
_GL_COLOR_BUFFER_BIT = 0x4000
_GL_DEPTH_BUFFER_BIT = 0x0100
_GL_DEPTH_TEST = 0x0B71
_GL_BLEND = 0x0BE2
_GL_SRC_ALPHA = 0x0302
_GL_ONE_MINUS_SRC_ALPHA = 0x0303

_VERTEX = """
#version 330 core
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in vec2 in_uv;
uniform mat4 u_view_proj;
out vec3 v_normal;
out vec2 v_uv;
out vec3 v_world;
void main() {
    v_world = in_pos;
    v_normal = in_normal;
    v_uv = in_uv;
    gl_Position = u_view_proj * vec4(in_pos, 1.0);
}
"""

_FRAGMENT = """
#version 330 core
uniform sampler2D u_atlas;
uniform int u_mode;
uniform float u_y_min;
uniform float u_y_max;
uniform float u_depth_bias;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_world;
out vec4 f_color;
void main() {
    if (v_world.y < u_y_min || v_world.y > u_y_max) discard;
    vec4 texel = texture(u_atlas, v_uv);
    if (u_mode == 1 && texel.a < 0.5) discard;
    if (u_mode == 2 && texel.a < 0.01) discard;
    vec3 n = normalize(v_normal);
    float key = max(dot(n, normalize(vec3(0.35, 1.0, 0.55))), 0.0);
    float fill = max(dot(n, normalize(vec3(-0.55, 0.25, -0.7))), 0.0);
    float light = 0.78 + 0.42 * key + 0.22 * fill;
    f_color = vec4(texel.rgb * min(light, 1.12), texel.a);
    gl_FragDepth = gl_FragCoord.z + u_depth_bias;
}
"""

_LINE_VERTEX = """
#version 330 core
layout(location = 0) in vec3 in_pos;
uniform mat4 u_view_proj;
out vec3 v_world;
void main() {
    v_world = in_pos;
    gl_Position = u_view_proj * vec4(in_pos, 1.0);
    gl_Position.z -= 0.0006 * gl_Position.w;
}
"""

_LINE_FRAGMENT = """
#version 330 core
uniform vec4 u_color;
uniform int u_clip;
uniform float u_y_min;
uniform float u_y_max;
in vec3 v_world;
out vec4 f_color;
void main() {
    if (u_clip == 1 && (v_world.y < u_y_min || v_world.y > u_y_max)) discard;
    f_color = u_color;
}
"""


def _matrix_qmatrix(matrix: np.ndarray) -> QMatrix4x4:
    """Convierte una matriz fila-mayor de numpy a ``QMatrix4x4`` (que también es fila-mayor)."""
    return QMatrix4x4(*[float(value) for value in matrix.reshape(-1).tolist()])


def interleave(mesh: Mesh) -> np.ndarray:
    """Entrelaza posición, normal y UV en un único array ``(N, 8)`` float32."""
    data = np.empty((mesh.positions.shape[0], 8), dtype=np.float32)
    data[:, 0:3] = mesh.positions
    data[:, 3:6] = mesh.normals
    data[:, 6:8] = mesh.uvs
    return data


def sorted_triangles(mesh: Mesh) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Triángulos ordenados por modo de alfa y rangos ``(modo, inicio, cantidad)``."""
    triangles = mesh.triangle_indices().astype(np.uint32)
    alpha = np.concatenate([mesh.alpha_mode, mesh.alpha_mode]).astype(np.int8)
    order = np.argsort(alpha, kind="stable")
    triangles = triangles[order]
    alpha = alpha[order]

    groups: list[tuple[int, int, int]] = []
    start = 0
    for mode in range(3):
        count = int(np.count_nonzero(alpha == mode))
        if count:
            groups.append((mode, start, count))
        start += count
    return triangles, groups


def triangle_soup(mesh: Mesh) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Vértices en orden de dibujo (sopa de triángulos) y grupos por modo de alfa.

    Los grupos son ``(modo, primer vértice, número de vértices)``, listos para
    ``glDrawArrays``.
    """
    vertices = interleave(mesh)
    triangles, groups = sorted_triangles(mesh)
    soup = np.ascontiguousarray(vertices[triangles].reshape(-1, 8))
    ranges = [(mode, start * 3, count * 3) for mode, start, count in groups]
    return soup, ranges


def edge_vertices(mesh: Mesh) -> np.ndarray:
    """Posiciones de las cuatro aristas de cada cara (para el wireframe)."""
    quads = mesh.quads.astype(np.uint32)
    edges = np.stack(
        [quads[:, [0, 1]], quads[:, [1, 2]], quads[:, [2, 3]], quads[:, [3, 0]]], axis=1
    )
    flat = edges.reshape(-1)
    return np.ascontiguousarray(mesh.positions[flat])


class _MeshGL:
    """Buffers, textura y VAOs de una malla subida a la GPU."""

    def __init__(
        self,
        mesh: Mesh,
        main: QOpenGLShaderProgram,
        line: QOpenGLShaderProgram,
        smooth: bool,
    ) -> None:
        self.groups: list[tuple[int, int, int]] = []
        self.vao = QOpenGLVertexArrayObject()
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.edge_vao = QOpenGLVertexArrayObject()
        self.edge_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.texture: QOpenGLTexture | None = None
        self.edge_count = 0

        soup, self.groups = triangle_soup(mesh)
        self.vao.create()
        self.vao.bind()
        self.vbo.create()
        self.vbo.bind()
        self.vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self.vbo.allocate(soup.tobytes(), soup.nbytes)
        main.bind()
        main.enableAttributeArray(0)
        main.setAttributeBuffer(0, _GL_FLOAT, 0, 3, 32)
        main.enableAttributeArray(1)
        main.setAttributeBuffer(1, _GL_FLOAT, 12, 3, 32)
        main.enableAttributeArray(2)
        main.setAttributeBuffer(2, _GL_FLOAT, 24, 2, 32)
        self.vao.release()

        points = edge_vertices(mesh)
        self.edge_count = int(points.shape[0])
        if self.edge_count:
            self.edge_vao.create()
            self.edge_vao.bind()
            self.edge_vbo.create()
            self.edge_vbo.bind()
            self.edge_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            self.edge_vbo.allocate(points.tobytes(), points.nbytes)
            line.bind()
            line.enableAttributeArray(0)
            line.setAttributeBuffer(0, _GL_FLOAT, 0, 3, 12)
            self.edge_vao.release()

        atlas = mesh.atlas.image.convert("RGBA")
        pixels = np.ascontiguousarray(np.asarray(atlas, dtype=np.uint8))
        image = QImage(
            pixels.data, atlas.width, atlas.height, atlas.width * 4, QImage.Format.Format_RGBA8888
        ).copy()
        self.texture = QOpenGLTexture(image)
        self.texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self.apply_filter(smooth)

    def apply_filter(self, smooth: bool) -> None:
        if self.texture is None:
            return
        if smooth:
            self.texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
            self.texture.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
            self.texture.generateMipMaps()
        else:
            self.texture.setMagnificationFilter(QOpenGLTexture.Filter.Nearest)
            self.texture.setMinificationFilter(QOpenGLTexture.Filter.Nearest)

    def release(self) -> None:
        if self.texture is not None:
            self.texture.destroy()
            self.texture = None
        for buffer in (self.vbo, self.edge_vbo):
            if buffer.isCreated():
                buffer.destroy()
        for vao in (self.vao, self.edge_vao):
            if vao.isCreated():
                vao.destroy()


class Viewport(QOpenGLWidget):
    """Vista 3D orbital con rejilla, ejes, capas y wireframe."""

    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.camera = OrbitCamera()
        self.background = (0.09, 0.10, 0.12)
        self.show_grid = True
        self.show_axes = True
        self.wireframe = False
        self.smooth = False
        self.layer_min = -1e9
        self.layer_max = 1e9

        self._gl: QOpenGLFunctions | None = None
        self._mu: dict[str, int] = {}
        self._lu: dict[str, int] = {}
        self._main: QOpenGLShaderProgram | None = None
        self._line: QOpenGLShaderProgram | None = None
        self._mesh: Mesh | None = None
        self._mesh_gl: _MeshGL | None = None
        self._pending: tuple[Mesh, bool] | None = None
        self._grid_vao: QOpenGLVertexArrayObject | None = None
        self._grid_vbo: QOpenGLBuffer | None = None
        self._grid_count = 0
        self._axes: list[tuple[QOpenGLVertexArrayObject, QOpenGLBuffer, QVector4D]] = []
        self._error = ""
        self._last_pos: QPointF | None = None
        self._button = Qt.MouseButton.NoButton

    # -- ciclo de vida OpenGL --------------------------------------------- #

    def initializeGL(self) -> None:
        try:
            if self._mesh_gl is not None or self._grid_vao is not None:
                self._release_mesh()
                self._release_lines()
            self._gl = self.context().functions()
            self.context().aboutToBeDestroyed.connect(self._cleanup_gl)
            self._main = QOpenGLShaderProgram(self)
            self._main.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX)
            self._main.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT)
            if not self._main.link():
                raise RuntimeError(f"shader principal: {self._main.log()}")
            self._line = QOpenGLShaderProgram(self)
            self._line.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _LINE_VERTEX)
            self._line.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _LINE_FRAGMENT)
            if not self._line.link():
                raise RuntimeError(f"shader de líneas: {self._line.log()}")
            self._mu = {
                name: self._main.uniformLocation(f"u_{name}")
                for name in ("view_proj", "y_min", "y_max", "depth_bias", "atlas", "mode")
            }
            self._lu = {
                name: self._line.uniformLocation(f"u_{name}")
                for name in ("view_proj", "y_min", "y_max", "color", "clip")
            }
            self._build_axes()
            self._build_grid((1, 1, 1))
            if self._pending is not None:
                mesh, fit = self._pending
                self._pending = None
                self._upload(mesh, fit=fit)
        except Exception as exc:  # pragma: no cover - depende de la GPU
            self._error = str(exc)
            self.failed.emit(self._error)

    def resizeGL(self, width: int, height: int) -> None:
        pass

    def paintGL(self) -> None:
        if self._gl is None or self._main is None or self._line is None:
            return
        ratio = self.devicePixelRatioF()
        width = max(1, int(self.width() * ratio))
        height = max(1, int(self.height() * ratio))
        self._gl.glViewport(0, 0, width, height)
        self._gl.glClearColor(*self.background, 1.0)
        self._gl.glClear(_GL_COLOR_BUFFER_BIT | _GL_DEPTH_BUFFER_BIT)
        self._gl.glEnable(_GL_DEPTH_TEST)
        self._gl.glDepthMask(True)
        self._gl.glDisable(_GL_BLEND)

        matrix = _matrix_qmatrix(self.camera.view_projection(width / height))
        self._line.bind()
        self._line.setUniformValue(self._lu["view_proj"], matrix)
        self._line.setUniformValue1f(self._lu["y_min"], float(self.layer_min))
        self._line.setUniformValue1f(self._lu["y_max"], float(self.layer_max))

        if self.show_grid and self._grid_vao is not None:
            self._gl.glEnable(_GL_BLEND)
            self._gl.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
            self._line.setUniformValue1i(self._lu["clip"], 0)
            self._line.setUniformValue(self._lu["color"], QVector4D(0.42, 0.45, 0.50, 0.42))
            self._grid_vao.bind()
            self._gl.glDrawArrays(_GL_LINES, 0, self._grid_count)
            self._grid_vao.release()

        if self.show_axes:
            self._gl.glEnable(_GL_BLEND)
            self._gl.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
            self._line.setUniformValue1i(self._lu["clip"], 0)
            for vao, _vbo, color in self._axes:
                self._line.setUniformValue(self._lu["color"], color)
                vao.bind()
                self._gl.glDrawArrays(_GL_LINES, 0, 2)
                vao.release()

        if self._mesh_gl is not None:
            self._draw_mesh(matrix)
            if self.wireframe and self._mesh_gl.edge_count:
                self._gl.glEnable(_GL_BLEND)
                self._gl.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
                self._line.bind()
                self._line.setUniformValue(self._lu["view_proj"], matrix)
                self._line.setUniformValue1i(self._lu["clip"], 1)
                self._line.setUniformValue(self._lu["color"], QVector4D(0.02, 0.02, 0.03, 0.55))
                self._mesh_gl.edge_vao.bind()
                self._gl.glDrawArrays(_GL_LINES, 0, self._mesh_gl.edge_count)
                self._mesh_gl.edge_vao.release()

        self._gl.glDisable(_GL_BLEND)
        self._gl.glDepthMask(True)

    def _draw_mesh(self, matrix: QMatrix4x4) -> None:
        assert self._gl is not None and self._main is not None and self._mesh_gl is not None
        self._main.bind()
        self._main.setUniformValue(self._mu["view_proj"], matrix)
        self._main.setUniformValue1f(self._mu["y_min"], float(self.layer_min))
        self._main.setUniformValue1f(self._mu["y_max"], float(self.layer_max))
        self._main.setUniformValue1f(self._mu["depth_bias"], 2e-5)
        self._main.setUniformValue1i(self._mu["atlas"], 0)
        if self._mesh_gl.texture is not None:
            self._mesh_gl.texture.bind(0)

        self._mesh_gl.vao.bind()
        for mode, first, count in self._mesh_gl.groups:
            self._main.setUniformValue1i(self._mu["mode"], mode)
            if mode == 2:
                self._gl.glEnable(_GL_BLEND)
                self._gl.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
                self._gl.glDepthMask(False)
            else:
                self._gl.glDisable(_GL_BLEND)
                self._gl.glDepthMask(True)
            self._gl.glDrawArrays(_GL_TRIANGLES, first, count)
        self._mesh_gl.vao.release()
        self._gl.glDepthMask(True)
        self._gl.glDisable(_GL_BLEND)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Libera los recursos OpenGL con el contexto activo (llamar antes de cerrar)."""
        if self._gl is None:
            return
        self.makeCurrent()
        try:
            self._release_mesh()
            self._release_lines()
        finally:
            self.doneCurrent()
            self._gl = None
            self._main = None
            self._line = None

    def _cleanup_gl(self) -> None:
        """Libera texturas y buffers cuando Qt destruye el contexto."""
        self.shutdown()

    # -- API pública ------------------------------------------------------- #

    def set_mesh(self, mesh: Mesh, *, fit: bool = True) -> None:
        """Sube una malla a la GPU (o la deja pendiente hasta que haya contexto)."""
        self._mesh = mesh
        if self._gl is None:
            self._pending = (mesh, fit)
            return
        self.makeCurrent()
        try:
            self._upload(mesh, fit=fit)
        finally:
            self.doneCurrent()
        self.update()

    def set_layer(self, minimum: int | None, maximum: int | None) -> None:
        self.layer_min = -1e9 if minimum is None else float(minimum)
        self.layer_max = 1e9 if maximum is None else float(maximum)
        self.update()

    def set_grid(self, enabled: bool) -> None:
        self.show_grid = enabled
        self.update()

    def set_axes(self, enabled: bool) -> None:
        self.show_axes = enabled
        self.update()

    def set_wireframe(self, enabled: bool) -> None:
        self.wireframe = enabled
        self.update()

    def set_smooth(self, enabled: bool) -> None:
        self.smooth = enabled
        if self._mesh_gl is not None and self._gl is not None:
            self.makeCurrent()
            try:
                self._mesh_gl.apply_filter(enabled)
            finally:
                self.doneCurrent()
        self.update()

    def set_background(self, color: QColor) -> None:
        self.background = (color.redF(), color.greenF(), color.blueF())
        self.update()

    def fit(self) -> None:
        if self._mesh is not None:
            self.camera.fit(self._mesh.size)
            self.update()

    def screenshot(self, path) -> bool:
        image = self.grabFramebuffer()
        return bool(image.save(str(path)))

    @property
    def error(self) -> str:
        return self._error

    # -- subida y recursos ------------------------------------------------- #

    def _upload(self, mesh: Mesh, *, fit: bool) -> None:
        assert self._main is not None and self._line is not None
        self._release_mesh()
        self._mesh_gl = _MeshGL(mesh, self._main, self._line, self.smooth)
        self._build_grid(mesh.size)
        if fit:
            self.camera.fit(mesh.size)

    def _release_mesh(self) -> None:
        if self._mesh_gl is not None:
            self._mesh_gl.release()
            self._mesh_gl = None

    def _release_lines(self) -> None:
        if self._grid_vbo is not None and self._grid_vbo.isCreated():
            self._grid_vbo.destroy()
        self._grid_vbo = None
        if self._grid_vao is not None and self._grid_vao.isCreated():
            self._grid_vao.destroy()
        self._grid_vao = None
        for vao, vbo, _color in self._axes:
            if vbo.isCreated():
                vbo.destroy()
            if vao.isCreated():
                vao.destroy()
        self._axes.clear()

    def _build_grid(self, size: tuple[int, int, int]) -> None:
        assert self._line is not None
        self._release_grid()
        span = max(max(size[0], size[2]) * 0.75, 12.0) + 4.0
        step = 1.0 if span <= 48 else (4.0 if span <= 256 else 16.0)
        count = int(span / step)
        cx, cz = size[0] / 2.0, size[2] / 2.0
        y = -0.01
        vertices: list[tuple[float, float, float]] = []
        for index in range(-count, count + 1):
            offset = index * step
            vertices.append((cx + offset, y, cz - count * step))
            vertices.append((cx + offset, y, cz + count * step))
            vertices.append((cx - count * step, y, cz + offset))
            vertices.append((cx + count * step, y, cz + offset))
        data = np.ascontiguousarray(np.array(vertices, dtype=np.float32))
        self._grid_count = int(data.shape[0])
        self._grid_vao = QOpenGLVertexArrayObject()
        self._grid_vao.create()
        self._grid_vao.bind()
        self._grid_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._grid_vbo.create()
        self._grid_vbo.bind()
        self._grid_vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        self._grid_vbo.allocate(data.tobytes(), data.nbytes)
        self._line.bind()
        self._line.enableAttributeArray(0)
        self._line.setAttributeBuffer(0, _GL_FLOAT, 0, 3, 12)
        self._grid_vao.release()

    def _release_grid(self) -> None:
        if self._grid_vbo is not None and self._grid_vbo.isCreated():
            self._grid_vbo.destroy()
        self._grid_vbo = None
        if self._grid_vao is not None and self._grid_vao.isCreated():
            self._grid_vao.destroy()
        self._grid_vao = None

    def _build_axes(self) -> None:
        assert self._line is not None
        self._release_axes()
        length = 8.0
        axes = (
            ((length, 0.0, 0.0), QVector4D(0.90, 0.30, 0.30, 1.0)),
            ((0.0, length, 0.0), QVector4D(0.30, 0.90, 0.35, 1.0)),
            ((0.0, 0.0, length), QVector4D(0.35, 0.55, 0.95, 1.0)),
        )
        for end, color in axes:
            data = np.ascontiguousarray(np.array([(0.0, 0.0, 0.0), end], dtype=np.float32))
            vao = QOpenGLVertexArrayObject()
            vao.create()
            vao.bind()
            vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo.create()
            vbo.bind()
            vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
            vbo.allocate(data.tobytes(), data.nbytes)
            self._line.bind()
            self._line.enableAttributeArray(0)
            self._line.setAttributeBuffer(0, _GL_FLOAT, 0, 3, 12)
            vao.release()
            self._axes.append((vao, vbo, color))

    def _release_axes(self) -> None:
        for vao, vbo, _color in self._axes:
            if vbo.isCreated():
                vbo.destroy()
            if vao.isCreated():
                vao.destroy()
        self._axes.clear()

    # -- interacción ------------------------------------------------------- #

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._last_pos = event.position()
        self._button = event.button()
        if self._button in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_pos is None:
            return
        position = event.position()
        dx = position.x() - self._last_pos.x()
        dy = position.y() - self._last_pos.y()
        self._last_pos = position
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._button == Qt.MouseButton.LeftButton and not shift:
            self.camera.orbit(dx, dy)
        elif self._button in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or shift:
            self.camera.pan(dx, dy, self.height())
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_pos = None
        self._button = Qt.MouseButton.NoButton
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.fit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.camera.zoom(event.angleDelta().y() / 120.0)
        self.update()
