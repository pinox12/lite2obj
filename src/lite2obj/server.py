"""Servidor web: API de render + visor Three.js (F6/F7).

Endpoints
---------

* ``GET  /api/health``               estado y versión
* ``GET  /api/packs``                resource packs detectados
* ``GET  /api/schematics``           esquemáticos encontrados en la máquina
* ``POST /api/schematics``           sube un ``.litematic`` (multipart)
* ``POST /api/render``               renderiza una ruta o un esquemático subido antes
* ``GET  /api/mesh/{mesh_id}``       metadatos de la malla (tamaño, partes, grupos)
* ``GET  /api/mesh/{mesh_id}/buffer`` búfer binario de vértices/índices
* ``GET  /api/mesh/{mesh_id}/atlas.png`` atlas de texturas
* ``GET  /`` (y el resto)            visor estático de ``web/``

El visor no necesita compilación: ``web/index.html`` usa un *importmap* que apunta a un CDN de
Three.js, de modo que el servidor de Python puede servir el visor tal cual. Con npm/Vite se
sirve el mismo ``index.html``/``src`` con Three.js de ``node_modules``.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, assets, paths, pipeline
from .litematic import Litematic, LitematicError
from .mesh import Mesh
from .models import BlockModelError
from .nbt import NBTError

__all__ = ["MeshStore", "create_app", "run"]

#: Cuántas mallas se guardan en memoria a la vez.
CACHE_SIZE = 8

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"


class RenderRequest(BaseModel):
    """Cuerpo de ``POST /api/render``."""

    path: str | None = Field(default=None, description="Ruta a un .litematic del disco")
    upload_id: str | None = Field(default=None, description="Id devuelto por POST /api/schematics")
    packs: list[str] = Field(default_factory=list)
    cull: bool = True
    tint: bool = True
    max_quads: int = Field(default=0, ge=0)


@dataclass(slots=True)
class StoredMesh:
    """Malla renderizada y cacheada."""

    id: str
    name: str
    mesh: Mesh
    buffer: bytes
    meta: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.meta["size"],
            "stats": self.meta["stats"],
        }


class MeshStore:
    """Caché LRU de mallas renderizadas, segura para varios hilos."""

    def __init__(self, capacity: int = CACHE_SIZE) -> None:
        self.capacity = capacity
        self._items: OrderedDict[str, StoredMesh] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, name: str, mesh: Mesh) -> StoredMesh:
        buffer, meta = pipeline.mesh_bundle(mesh)
        entry = StoredMesh(id=uuid.uuid4().hex, name=name, mesh=mesh, buffer=buffer, meta=meta)
        with self._lock:
            self._items[entry.id] = entry
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return entry

    def get(self, mesh_id: str) -> StoredMesh:
        with self._lock:
            entry = self._items.get(mesh_id)
            if entry is None:
                raise KeyError(mesh_id)
            self._items.move_to_end(mesh_id)
        return entry

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [entry.summary() for entry in self._items.values()]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def _render(source: str | Path | Litematic, request: RenderRequest) -> Mesh:
    options = pipeline.RenderOptions(
        packs=list(request.packs),
        cull=request.cull,
        tint=request.tint,
        max_quads=request.max_quads,
    )
    try:
        return pipeline.render(source, options)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (LitematicError, NBTError, BlockModelError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def create_app() -> FastAPI:
    """Crea la aplicación FastAPI (útil para tests y para ``uvicorn``)."""
    app = FastAPI(title="lite2obj", version=__version__, docs_url="/api/docs")
    store = MeshStore()
    uploads: dict[str, Path] = {}
    app.state.store = store

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        jar = paths.client_jar()
        return {
            "version": __version__,
            "client_jar": str(jar) if jar else None,
            "cached_meshes": len(store.list()),
        }

    @app.get("/api/packs")
    def packs() -> dict[str, Any]:
        return {
            "packs": [
                {"name": p.name, "kind": p.kind, "path": str(p.path)} for p in assets.list_packs()
            ]
        }

    @app.get("/api/schematics")
    def schematics() -> dict[str, Any]:
        found = paths.find_schematics()
        items = []
        for path in found:
            size: int | None = None
            with contextlib.suppress(OSError):
                size = path.stat().st_size
            items.append({"name": path.stem, "path": str(path), "bytes": size})
        return {"schematics": items, "cached": store.list()}

    @app.post("/api/schematics")
    async def upload(file: UploadFile) -> dict[str, Any]:
        data = await file.read()
        upload_id = uuid.uuid4().hex
        path = _upload_path(upload_id)
        path.write_bytes(data)
        uploads[upload_id] = path
        return {
            "upload_id": upload_id,
            "name": Path(file.filename or "subida").stem,
            "bytes": len(data),
        }

    @app.post("/api/render")
    def render(request: RenderRequest) -> dict[str, Any]:
        if request.upload_id:
            path = uploads.get(request.upload_id)
            if path is None:
                raise HTTPException(status_code=404, detail="upload_id desconocido")
            name = f"subida-{request.upload_id[:8]}"
        elif request.path:
            path = Path(request.path).expanduser()
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"No existe {path}")
            name = path.stem
        else:
            raise HTTPException(status_code=400, detail="Indica 'path' o 'upload_id'")
        mesh = _render(path, request)
        entry = store.put(name, mesh)
        return {"mesh_id": entry.id, "name": entry.name, "meta": entry.meta}

    @app.get("/api/mesh/{mesh_id}")
    def mesh_meta(mesh_id: str) -> dict[str, Any]:
        try:
            entry = store.get(mesh_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="mesh_id desconocido") from error
        return {"id": entry.id, "name": entry.name, **entry.meta}

    @app.get("/api/mesh/{mesh_id}/buffer")
    def mesh_buffer(mesh_id: str) -> Response:
        try:
            entry = store.get(mesh_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="mesh_id desconocido") from error
        return Response(
            content=entry.buffer,
            media_type="application/octet-stream",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/mesh/{mesh_id}/atlas.png")
    def mesh_atlas(mesh_id: str) -> Response:
        try:
            entry = store.get(mesh_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="mesh_id desconocido") from error
        return Response(
            content=entry.mesh.atlas.to_png(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/mesh/{mesh_id}/export.obj")
    def mesh_obj(mesh_id: str) -> Response:
        return _export_response(store, mesh_id, "obj")

    @app.get("/api/mesh/{mesh_id}/export.mtl")
    def mesh_mtl(mesh_id: str) -> Response:
        return _export_response(store, mesh_id, "mtl")

    if WEB_ROOT.is_dir():
        app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
    else:  # pragma: no cover - sólo si falta el directorio web/

        @app.get("/")
        def missing_viewer() -> JSONResponse:
            return JSONResponse(
                status_code=500,
                content={"detail": f"No se encontró el visor en {WEB_ROOT}"},
            )

    return app


def _export_response(store: MeshStore, mesh_id: str, kind: str) -> Response:
    """Exporta al vuelo un esquemático ya renderizado, sin escribir en disco."""
    import tempfile

    from .export import ExportOptions, export_obj

    try:
        entry = store.get(mesh_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="mesh_id desconocido") from error

    with tempfile.TemporaryDirectory() as directory:
        result = export_obj(entry.mesh, directory, ExportOptions(name=entry.name))
        if kind == "obj":
            return Response(content=result.obj.read_bytes(), media_type="text/plain")
        assert result.mtl is not None
        return Response(content=result.mtl.read_bytes(), media_type="text/plain")


def _upload_path(upload_id: str) -> Path:
    directory = Path(tempfile_directory())
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{upload_id}.litematic"


def tempfile_directory() -> str:
    import tempfile

    return str(Path(tempfile.gettempdir()) / "lite2obj" / "uploads")


def run(host: str = "127.0.0.1", port: int = 8000, *, open_browser: bool = False) -> None:
    """Arranca uvicorn con la aplicación."""
    import uvicorn

    if open_browser:  # pragma: no cover - interacción con el navegador
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
