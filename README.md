# lite2obj

Renderiza esquemáticos de **Minecraft Litematica** (`.litematic`) en 3D y los convierte en
un objeto importable en **Blender** (OBJ + MTL + texturas). Inspirado en
[j-mc-2-obj](https://github.com/jmc2obj/j-mc-2-obj), pero como proyecto propio en Python + web.

## Estado

| Fase | Descripción | Estado |
|------|-------------|--------|
| F1 | Parser `.litematic` (NBT + bit-packing YZX) | ✅ hecho |
| F2 | Resolución de blockstates/modelos desde el client jar | ✅ hecho |
| F3 | Texturas y atlas | ⏳ pendiente |
| F4 | Malla y culling por `cullface` | ⏳ pendiente |
| F5 | Export OBJ + MTL + atlas | ⏳ pendiente |
| F6 | API (FastAPI) + CLI | 🚧 CLI `info`/`list` listo |
| F7 | Visor web Three.js | ⏳ pendiente |
| F8 | Tests | 🚧 91 tests |

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

# Lista los .litematic encontrados en instalaciones de Minecraft de la máquina
uv run lite2obj list
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
  cli.py         interfaz de línea de comandos
tests/
  test_nbt.py                 lector NBT contra un escritor de tests
  test_litematic.py           bit-packing, tamaños negativos, multi-región, fixture real
  test_models.py              rotaciones, variants/multipart, parent, texturas
  test_schematics_integration.py  valida todos los .litematic de la máquina
  test_coverage_integration.py    cobertura de geometría sobre los esquemáticos
```
