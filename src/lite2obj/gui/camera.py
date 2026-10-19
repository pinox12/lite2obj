"""Cámara orbital y matrices de proyección para el visor de escritorio.

Las matrices se construyen en formato fila-mayor (como numpy) y el viewport las convierte a
``QMatrix4x4`` (también fila-mayor); Qt se encarga de la conversión a columna-mayor que
espera OpenGL. Así el código de la cámara se lee como un libro de matemáticas y hay un solo
punto donde se cruza la convención.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = ["OrbitCamera", "look_at", "perspective"]

#: Límite del cabeceo para no cruzar el polo y perder la vertical.
_MAX_PITCH = math.radians(89.0)


def _normalize(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    return vector / length if length else vector


def look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    """Matriz de vista fila-mayor que mira de ``eye`` a ``target``."""
    forward = _normalize(target - eye)
    right = _normalize(np.cross(forward, up))
    true_up = np.cross(right, forward)
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, :3] = right
    matrix[0, 3] = -float(np.dot(right, eye))
    matrix[1, :3] = true_up
    matrix[1, 3] = -float(np.dot(true_up, eye))
    matrix[2, :3] = -forward
    matrix[2, 3] = float(np.dot(forward, eye))
    return matrix


def perspective(fov_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    """Matriz de proyección en perspectiva fila-mayor."""
    aspect = max(aspect, 1e-6)
    focal = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    matrix = np.zeros((4, 4), dtype=np.float64)
    matrix[0, 0] = focal / aspect
    matrix[1, 1] = focal
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = (2.0 * far * near) / (near - far)
    matrix[3, 2] = -1.0
    return matrix


@dataclass(slots=True)
class OrbitCamera:
    """Cámara que orbita alrededor de un objetivo con zoom y desplazamiento."""

    target: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.float64))
    yaw: float = math.radians(45.0)
    pitch: float = math.radians(28.0)
    distance: float = 32.0
    fov: float = 50.0
    near: float = 0.05
    far: float = 20000.0

    def fit(self, size: tuple[int, int, int]) -> None:
        """Encaja un esquemático de ``size`` bloques en la vista."""
        sx, sy, sz = (max(float(value), 1.0) for value in size)
        radius = math.sqrt(sx * sx + sy * sy + sz * sz) / 2.0
        self.target = np.array([sx / 2.0, sy / 2.0, sz / 2.0], dtype=np.float64)
        self.distance = max(radius / math.tan(math.radians(self.fov) / 2.0) * 1.25, 4.0)
        self.near = max(self.distance / 1000.0, 0.01)
        self.far = max(self.distance * 40.0, radius * 20.0 + 100.0)

    def eye(self) -> np.ndarray:
        cos_pitch = math.cos(self.pitch)
        offset = np.array(
            [
                cos_pitch * math.sin(self.yaw),
                math.sin(self.pitch),
                cos_pitch * math.cos(self.yaw),
            ],
            dtype=np.float64,
        )
        return self.target + offset * self.distance

    def basis(self) -> tuple[np.ndarray, np.ndarray]:
        """Vectores ``derecha`` y ``arriba`` de la cámara en coordenadas de mundo."""
        forward = _normalize(self.target - self.eye())
        right = _normalize(np.cross(forward, np.array([0.0, 1.0, 0.0])))
        up = np.cross(right, forward)
        return right, _normalize(up)

    def orbit(self, dx: float, dy: float) -> None:
        self.yaw -= dx * 0.008
        self.pitch = float(np.clip(self.pitch - dy * 0.008, -_MAX_PITCH, _MAX_PITCH))

    def pan(self, dx: float, dy: float, viewport_height: int) -> None:
        """Desplaza el objetivo para que el modelo siga al cursor."""
        right, up = self.basis()
        scale = 2.0 * self.distance * math.tan(math.radians(self.fov) / 2.0)
        scale /= max(viewport_height, 1)
        self.target -= right * (dx * scale)
        self.target += up * (dy * scale)

    def zoom(self, steps: float) -> None:
        self.distance = float(np.clip(self.distance * math.exp(-steps * 0.12), 0.5, 1e6))

    def view(self) -> np.ndarray:
        return look_at(self.eye(), self.target, np.array([0.0, 1.0, 0.0]))

    def projection(self, aspect: float) -> np.ndarray:
        return perspective(self.fov, aspect, self.near, self.far)

    def view_projection(self, aspect: float) -> np.ndarray:
        return self.projection(aspect) @ self.view()
