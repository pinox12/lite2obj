# Plan de proyecto: `lite2obj`

**Objetivo**: convertir un archivo `.litematic` (mod Litematica) en un render 3D en una app de
escritorio y exportarlo a un objeto importable en Blender (OBJ + MTL + texturas), con
fidelidad de modelos de bloque inspirada en [j-mc-2-obj](https://github.com/jmc2obj/j-mc-2-obj).

**Stack**: Python 3.14 gestionado con **uv** (PySide6 + Qt OpenGL + Pillow + numpy). Sin Java,
sin navegador.

---

## Estado (v1 funcional de punta a punta)

F1–F8 están implementadas y el pipeline completo funciona: `.litematic` → modelos →
texturas/atlas → malla con culling → OBJ+MTL+atlas y app de escritorio con vista 3D. 195
tests, `ruff` y `mypy` limpios.

| Fase | Estado | Nota |
|---|---|---|
| F1 Parser | ✅ | 27/27 esquemáticos; recuento exacto de bloques |
| F2 Modelos | ✅ | **99,993 %** de cobertura de geometría (100 509 instancias; sólo falla un bloque de un mod) |
| F3 Texturas | ✅ | 0 texturas ausentes en los 27 esquemáticos; atlas potencia de dos con relleno |
| F4 Malla | ✅ | caras CCW con normal hacia fuera; culling contra vecino cúbico opaco |
| F5 Export OBJ | ✅ | un material con `map_Kd`/`map_d` al atlas; índices deduplicados |
| F6 CLI | ✅ | `info`, `list`, `export` |
| F7 App de escritorio | ✅ | PySide6 + OpenGL de Qt; órbita, capas, wireframe, captura y export desde la UI |
| F8 Tests | ✅ | 195 tests; golden de conteos, `uvlock` documentado |

Módulos reales (los nombres del plan cambiaron al implementar):

```
src/lite2obj/
  nbt.py         lector NBT
  litematic.py   parser .litematic
  paths.py       detección de instalaciones
  assets.py      pila de recursos (jar + packs)
  models.py      blockstates y modelos
  fallbacks.py   geometría sustituta (fluidos, bloques de entidad)
  aliases.py     bloques renombrados
  textures.py    atlas, sprites teñidos, transparencia
  mesh.py        malla y culling
  export.py      OBJ + MTL + atlas        (el plan decía export_obj.py)
  pipeline.py    alto nivel CLI/app + búfer binario del visor
  cli.py         lite2obj info|list|export|gui
  gui/           app de escritorio: app.py (ventana), viewport.py (GL), camera.py
```

Desviaciones respecto al plan:

- **GLB**: descartado en v1 (`export_gltf.py` no existe). El OBJ cubre el objetivo de Blender.
- **Render 3D**: en vez del visor web Three.js, la app usa **OpenGL nativo de Qt** dentro de un
  `QOpenGLWidget` (PySide6). Un contexto GL externo (p. ej. moderngl/glcontext) puede crear su
  propio framebuffer y el dibujo nunca se presenta; por eso se usan las clases `QOpenGL*`.
- **`uvlock`**: los modelos con `uvlock` (esquinas de escaleras `inner_*`/`outer_*`) usan las
  UVs del modelo en lugar de bloquear el giro; es la única concesión conocida.
- **Variants por posición**: los bloques de terreno (`stone`, `dirt`, `grass_block`…) traen
  varias rotaciones y Minecraft elige una **por posición**; se replica con un *hash* estable de
  la posición (`models.position_seed`), así que el suelo no se ve repetitivo. El *hash* no es
  el de Minecraft bit a bit (sólo hace falta que reparta).
- **Normales**: se calculan del giro de los vértices cuando hay rotación (variant o elemento):
  hay 95 modelos vanilla que rotan elementos (`block/cross`, fans de coral, soportes…) y otros
  que además usan `rescale`; girar sólo las posiciones dejaba esas caras mal sombreadas.
- **Caras coincidentes**: si dos caras del *mismo* bloque comparten plano (la hierba y su
  *overlay* lateral), se hornean en una sola textura y una sola cara; así Blender y el visor
  enseñan lo mismo. Entre bloques distintos no se tocan (Minecraft también dibuja las dos).
- **Entidades y `Items` de cofres**: fuera de v1, como estaba previsto.
- **Tintes**: se aplican multiplicando la textura (sprites teñidos dentro del atlas), en vez
  de colores por vértice, para que el color viaje en el OBJ sin nada extra.

Culling medido (caras sin descartar → con descarte):

| Esquemático | Caras | Reducción | Tiempo |
|---|---|---|---|
| Diabu_coastal_house | 57 671 → 40 352 | 30 % | 0,54 s |
| Diaburita_Mediterranean_villa | 101 684 → 67 931 | 33 % | 0,85 s |
| medieval_tavern | 151 786 → 103 383 | 32 % | 0,95 s |

(No llega al >50 % que estimaba el plan porque son edificios huecos: la mayoría de caras ya
eran exteriores. El descarte sí elimina todas las caras entre bloques opacos contiguos; las
~5 800 que quedan solapadas son casos que Minecraft también dibuja dos veces: losas, escaleras,
cristales y alfombras, donde el vecino no es un cubo opaco.)

---

## Entorno verificado (datos reales de esta máquina)

- **`.litematic`**: NBT comprimido con gzip. Formato confirmado sobre
  `almacen bonito 1x4x4.litematic`:
  raíz con `Regions -> {Position, Size, BlockStatePalette, BlockStates, TileEntities, Entities}`,
  `Metadata`, `Version=7`, `SubVersion=1`, `MinecraftDataVersion`.
  Bit-packing **YZX** con `bits = ceil(log2(palette_size))`; el fixture decodifica
  **42 bloques no-aire = 42** de `Metadata.TotalBlocks`. Validado.
- **Schematics disponibles**: 27 archivos en
  `~/.local/share/ModrinthApp/profiles/Fabulously Optimized 1.0.0/schematics/`.
- **Client jar 26.3**: `~/.local/share/ModrinthApp/meta/versions/26.3-0.19.5/26.3-0.19.5.jar`
  con **1289 blockstates, 2884 modelos de bloque, 1406 texturas de bloque**.
  Formato clásico confirmado: `variants`/`multipart` -> `model` con `parent`, `textures`,
  `elements`, `cullface`, `tintindex`, rotaciones `x`/`y`/`uvlock`.
- **Assets externos**: `~/.local/share/ModrinthApp/meta/assets/` con `indexes/34.json`
  (el `assetIndex` que declara `~/.minecraft/versions/26.3/26.3.json`).
- **Resource packs**: son `.zip` con `assets/minecraft/{blockstates,models,textures}`
  (Mizuno's 16x tiene ~2965 entradas). El pack es de 1.20.1 (`pack_format 15`) mientras el
  juego es 26.3 -> hace falta **fallback por-asset a vanilla**. Texturas de Mizuno's en PNG
  modo paleta (`P`), hay que convertir a RGBA.
- **Toolchain**: `uv 0.12.18` (`~/.local/bin/uv`), Python 3.14.8, Node 26, Blender 5.2,
  Pillow 13, numpy 2.5. Red disponible para `uv add`.

### Rutas de detección automática (sobrescribibles por CLI/env)

| Qué | Ruta |
|---|---|
| jar | `~/.local/share/ModrinthApp/meta/versions/26.3-0.19.5/26.3-0.19.5.jar` |
| packs | `~/.local/share/ModrinthApp/profiles/*/resourcepacks/*.zip` |
| assets | `~/.local/share/ModrinthApp/meta/assets/` |
| schematics | `~/.local/share/ModrinthApp/profiles/*/schematics/` |

---

## Estructura (layout `src/` de uv)

```
lite2obj/
├── pyproject.toml          # [project] + dependencias + [project.scripts] + [tool.*]
├── uv.lock                 # lockfile commiteado
├── .python-version         # 3.14
├── .gitignore
├── README.md
├── src/lite2obj/
│   ├── __init__.py
│   ├── nbt.py              # lector NBT sin dependencias (gzip/zlib/raw)
│   ├── litematic.py        # regiones, paleta, voxels YZX, tile entities, metadata
│   ├── assets.py           # pila de recursos (jar + packs), indice perezoso, fallback
│   ├── models.py           # blockstate: variants/multipart, parents, rotaciones
│   ├── textures.py         # PNG->RGBA, atlas, animacion (1er frame), tint
│   ├── mesh.py             # voxel->geometria, culling por cullface, UVs al atlas
│   ├── export.py           # OBJ + MTL + atlas.png
│   ├── pipeline.py         # alto nivel CLI/app + bufer binario del visor
│   ├── cli.py              # lite2obj info|list|export|gui
│   └── gui/                # app de escritorio (PySide6 + OpenGL de Qt)
│       ├── app.py          # ventana, panel de opciones, tareas en segundo plano
│       ├── viewport.py     # QOpenGLWidget: shaders, buffers, capas, wireframe
│       └── camera.py       # camara orbital y matrices
└── tests/                  # fixtures + golden tests
```

---

## Tooling con uv

Bootstrap inicial:

```bash
cd /home/pinox12/Documentos/lite2obj
git init
uv init --lib --name lite2obj --python 3.14
uv python pin 3.14
uv add pyside6-essentials pillow numpy
uv add --dev pytest pytest-cov ruff mypy
uv lock
```

`pyproject.toml` incluye:

```toml
[project.scripts]
lite2obj = "lite2obj.cli:main"
lite2obj-gui = "lite2obj.gui:main"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

| Tarea | Comando |
|---|---|
| Instalar/sincronizar | `uv sync` |
| CLI info | `uv run lite2obj info esquema.litematic` |
| Exportar | `uv run lite2obj export esquema.litematic salida/ --packs "..."` |
| App de escritorio | `uv run lite2obj gui` |
| Tests | `uv run pytest` |
| Lint/format | `uv run ruff check . && uv run ruff format .` |
| Tipo-check | `uv run mypy src` |

Notas:
- `uv.lock` y `.python-version` se commitean.
- `.gitignore`: `.venv/`, `__pycache__/`, `dist/`, salidas `*.obj`/`*.mtl`.
- Un único entorno (`uv`); ya no hay toolchain de Node.

---

## Fases y "definicion de terminado"

### F1 · Parseo (base solida)
- `nbt.py`: todos los tags, autodeteccion de compresion.
- `litematic.py`: multi-region con `Position`/`Size` (tamaños negativos: el origen pasa a la
  esquina mínima, sin espejar el grid),
  despaquetado YZX, propiedades de bloque, `TileEntities`, `Metadata`.
- **DoD**: sobre `almacen bonito 1x4x4.litematic` reporta exactamente 42 bloques; corre
  sobre los 27 schematics sin excepciones.

### F2 · Resolucion de modelos (fidelidad)
- `assets.py`: capa vanilla (jar) + capas de packs ordenadas por prioridad; lookup
  `ruta -> (origen, entrada)` con fallback automatico si el pack 1.20.1 no tiene el asset.
- `models.py`: `variants` (match exacto y fallback por subconjunto/`""`), `multipart` con
  `when`, campos `x`/`y`/`uvlock`/`weight`, cadena de `parent` hasta `block/block`, merge de
  `textures`, `elements` con `rotation` (origin/axis/angle/rescale), `cullface`, `tintindex`.
- **DoD**: resolver >98% de los bloques de los 27 schematics; reporte de bloques no soportados.

### F3 · Texturas y atlas
- Carga PNG (incl. modo paleta `P`), animacion -> primer frame, incluye
  `textures/entity/*` (cofres, carteles).
- Atlas con packer propio + mapeo UV (0-16 -> coords de atlas).
- Tint fijo vanilla por `tintindex` (grass/foliage/water), conmutable.
- **DoD**: atlas con <5% de texturas en `missingno`; UVs sin costuras entre bloques.

### F4 · Malla y culling
- Rotar elementos segun el variant; convertir 0-16 -> metros (÷16).
- **Culling por `cullface`**: si el vecino es cubo completo opaco, se omite la cara
  (lo que hacia j-mc-2-obj). Deteccion de oclusor cacheada por `id+propiedades`;
  tratamiento de transparentes/translucidos.
- **DoD**: la casa de ejemplo pierde >50% de caras internas y renderiza sin huecos.

### F5 · Export a Blender
- `export_obj.py`: `.obj` + `.mtl` (1 material con `map_Kd atlas.png`) + atlas, caras quads.
- Opciones: `--center`, `--scale`, `--up y|z`, `--no-cull`, `--grouping none|type|region`.
- **DoD**: el OBJ abre en Blender 5.2 con UVs y textura correctas (probado con el Blender
  instalado).

### F6 · CLI
- CLI: `lite2obj info|list|export|gui`.
- `export` escribe OBJ + MTL + atlas; sin interfaz gráfica ni servidor.
- **DoD**: `uv run lite2obj export` produce un OBJ importable en Blender sin abrir la app.

### F7 · App de escritorio (PySide6 + OpenGL de Qt)
- Viewport `QOpenGLWidget` con órbita, zoom, pan, rejilla, ejes e iluminación estilo
  Minecraft (ambiente + luz principal + relleno).
- Panel al estilo j-mc-2-obj: esquemático, resource packs, culling, tintes, resolución del
  atlas, texturas suavizadas, corte por capas Y, wireframe, captura PNG y export.
- Tareas (render y export) en `QThreadPool` para no bloquear la ventana.
- **DoD**: `Diabu_coastal_house.litematic` gira fluido y exporta desde la UI.

### F8 · Tests
- Unitarios: NBT, bit-array (fixture de 42 bloques), matching de variants, cadena de parents,
  logica de culling, normales contra el giro de los vértices, cámara y buffers del viewport.
- Golden: conteos de vertices/caras del fixture.
- Integracion: lotes sobre los 27 schematics buscando errores de resolucion.
- Arranque de la app en `offscreen` dentro de un subproceso.

---

## Riesgos conocidos

| Riesgo | Mitigacion |
|---|---|
| Pack 1.20.1 != juego 26.3 | Fallback por-asset a vanilla (comportamiento nativo de MC) |
| OBJ gigante en builds grandes | Culling + `--grouping`; greedy meshing como fase futura |
| Sin datos de bioma en `.litematic` | Tints fijos conmutables |
| Entidades e `Items` dentro de cofres | Fuera de v1; fase posterior opcional |
| Cambios de formato de modelos en 26.3 | Verificado: formato clasico intacto |

---

## Orden de entrega

1. F1 + F2 (parser + modelos)
2. F3 + F4 (texturas + malla)
3. F5 + F6 (export + CLI)
4. F7 (app de escritorio)
5. F8 (tests)
