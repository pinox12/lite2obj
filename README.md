# lite2obj

Renderiza esquemáticos de **Minecraft Litematica** (`.litematic`) en 3D y los convierte en
un objeto importable en **Blender** (OBJ + MTL + texturas). Inspirado en
[j-mc-2-obj](https://github.com/jmc2obj/j-mc-2-obj), pero como proyecto propio en Python + web.

## Estado

| Fase | Descripción | Estado |
|------|-------------|--------|
| F1 | Parser `.litematic` (NBT + bit-packing YZX) | ✅ hecho |
| F2 | Resolución de blockstates/modelos desde el client jar | ✅ hecho |
| F3 | Texturas y atlas | ✅ hecho |
| F4 | Malla y culling por `cullface` | ✅ hecho |
| F5 | Export OBJ + MTL + atlas | ✅ hecho |
| F6 | API (FastAPI) + CLI | ✅ hecho |
| F7 | Visor web Three.js | ✅ hecho |
| F8 | Tests | 🚧 179 tests |

El plan completo está en [`PLAN.md`](PLAN.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) y Python 3.14 (uv lo instala si hace falta).
- Un client jar de Minecraft para resolver modelos y texturas (F2+). En esta máquina se
  detecta automáticamente.

## Instalación

```bash
uv sync
```

## Uso

```bash
# Metadatos y recuento de bloques de un esquemático
uv run lite2obj info esquema.litematic

# Lista los .litematic y los resource packs encontrados en la máquina
uv run lite2obj list --packs

# Exporta a OBJ + MTL + atlas.png (para Blender)
uv run lite2obj export esquema.litematic salida/ --name casa
uv run lite2obj export esquema.litematic salida/ --z-up --group-by block
uv run lite2obj export esquema.litematic salida/ --packs "Mizunos 16 Craft JE_1.20.1-1.0_230720"

# Abre el visor web en http://127.0.0.1:8000
uv run lite2obj view --open
```

Ejemplo de salida de `info`:

```
almacen bonito 1x4x4
  autor        pinox12
  tamaño       2 x 4 x 8
  regiones     1
  bloques      42
  data version 4790 (formato v7.1)
```

### Exportar a Blender

`export` escribe tres archivos:

| Archivo | Contenido |
|---|---|
| `casa.obj` | toda la geometría, un solo objeto, caras cuadriláteras |
| `casa.mtl` | un único material `atlas` con `map_Kd casa` → `atlas.png` |
| `atlas.png` | todas las texturas (y sus versiones teñidas) en una imagen |

Al importar en Blender (`File > Import > Wavefront (.obj)`) deja los tres archivos en la misma
carpeta y mantén `Split by Object` desactivado para obtener una sola malla. Si desactivas la
conversión de ejes del importador usa `--z-up`.

### Visor web

```bash
uv run lite2obj view --port 8000 --open
```

El visor no necesita compilación: `web/index.html` resuelve Three.js con un *importmap* del
CDN. Permite elegir esquemático, arrastrar uno propio, activar/desactivar resource packs,
descartar caras ocultas, ver estadísticas y descargar el OBJ. También acepta
`?path=/ruta/al/esquema.litematic` para enlazar directamente a uno.

Para desarrollar el visor con recarga en caliente (dos entornos: `uv` para Python, `npm` para
JS):

```bash
uv run lite2obj view --port 8000     # backend
cd web && npm install && npm run dev # visor en :5173 con proxy a /api
```

## Desarrollo

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # formato
uv run mypy src        # tipos
```

## Detección de assets

`lite2obj` localiza el client jar, los `assets` y las carpetas de resource packs y
schematics en las rutas habituales (vanilla, Modrinth, Prism, MultiMC, ATLauncher, Flatpak).
Se puede forzar con variables de entorno: `LITE2OBJ_CLIENT_JAR`, `LITE2OBJ_ASSETS`,
`LITE2OBJ_RESOURCEPACKS`, `LITE2OBJ_SCHEMATICS`, `LITE2OBJ_MINECRAFT`.

## Estructura

```
src/lite2obj/
  nbt.py         lector NBT (sin dependencias)
  litematic.py   parser .litematic -> regiones, paleta, voxels
  paths.py       detección de instalaciones de Minecraft
  assets.py      pila de recursos (jar + resource packs)
  models.py      blockstates y modelos (variants/multipart, rotaciones)
  fallbacks.py   geometría sustituta (fluidos, bloques de entidad)
  aliases.py     bloques renombrados entre versiones
  textures.py    atlas de texturas, sprites teñidos
  mesh.py        malla del esquemático (caras, UV, culling)
  export.py      OBJ + MTL + atlas
  pipeline.py    alto nivel (CLI y API) + búfer binario del visor
  server.py      FastAPI: API + visor estático
  cli.py         interfaz de línea de comandos
web/             visor Three.js (index.html, src/main.js, style.css)
tests/
  test_nbt.py                 lector NBT contra un escritor de tests
  test_litematic.py           bit-packing, tamaños negativos, multi-región, fixture real
  test_models.py              rotaciones, variants/multipart, parent, texturas
  test_textures.py            empaquetado del atlas, tintes, transparencia
  test_mesh.py                orientación de caras/UV, culling, malla real
  test_export.py              OBJ/MTL, winding, deduplicado, agrupación
  test_server.py              API, caché de mallas, búfer binario
  test_cli.py                 comandos de la CLI
  test_schematics_integration.py  valida todos los .litematic de la máquina
  test_coverage_integration.py    cobertura de geometría sobre los esquemáticos
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | versión y jar detectado |
| `GET` | `/api/packs` | resource packs disponibles |
| `GET` | `/api/schematics` | `.litematic` encontrados en el disco |
| `POST` | `/api/schematics` | sube un `.litematic` (multipart) |
| `POST` | `/api/render` | renderiza (`path` o `upload_id`, packs, opciones) |
| `GET` | `/api/mesh/{id}` | metadatos: tamaño, partes del búfer, grupos, estadísticas |
| `GET` | `/api/mesh/{id}/buffer` | búfer binario de vértices e índices |
| `GET` | `/api/mesh/{id}/atlas.png` | atlas de texturas |
| `GET` | `/api/mesh/{id}/export.obj` | OBJ al vuelo |

Los triángulos llegan ordenados por modo de transparencia (opaco, *cutout*, translúcido) en un
único búfer: el visor sólo tiene que crear los `BufferAttribute` con los desplazamientos que
indica el JSON y dibujar un grupo por modo.
