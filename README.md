# lite2obj

Renderiza esquemáticos de **Minecraft Litematica** (`.litematic`) en 3D y los convierte en
un objeto importable en **Blender** (OBJ + MTL + texturas). Inspirado en
[j-mc-2-obj](https://github.com/jmc2obj/j-mc-2-obj), como app de escritorio propia en Python
(PySide6 + OpenGL de Qt).

## Estado

| Fase | Descripción | Estado |
|------|-------------|--------|
| F1 | Parser `.litematic` (NBT + bit-packing YZX) | ✅ hecho |
| F2 | Resolución de blockstates/modelos desde el client jar | ✅ hecho |
| F3 | Texturas y atlas | ✅ hecho |
| F4 | Malla, culling por `cullface`, entidades y *block entities* | ✅ hecho |
| F5 | Export OBJ + MTL + atlas | ✅ hecho |
| F6 | CLI (`info`, `list`, `export`) | ✅ hecho |
| F7 | App de escritorio (PySide6 + OpenGL) | ✅ hecho |
| F8 | Tests | ✅ 296 tests |

El plan completo está en [`PLAN.md`](PLAN.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) y Python 3.14 (uv lo instala si hace falta).
- Un client jar de Minecraft para resolver modelos y texturas (F2+). En esta máquina se
  detecta automáticamente.
- OpenGL 3.3 para la vista 3D (la CLI de export funciona sin GPU).

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
uv run lite2obj export esquema.litematic salida/ --atlas-scale 2 --scale 0.5
# Sólo bloques, sin entidades ni block entities
uv run lite2obj export esquema.litematic salida/ --no-entities
# Añade criaturas a las demás entidades (por defecto no se dibujan)
uv run lite2obj export esquema.litematic salida/ --mobs

# Abre la aplicación de escritorio
uv run lite2obj gui
uv run lite2obj gui esquema.litematic
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

Blender dibuja por defecto las dos caras de cada polígono, mientras que Minecraft sólo dibuja
la cara delantera. En bloques transparentes eso se nota: a través del cristal se ven las caras
traseras. El OBJ/MTL no puede guardar esa opción; actívala en el material importado
(**Material Properties → Settings → Backface Culling**) o, desde la consola de Python de
Blender:

```python
bpy.data.materials["atlas"].use_backface_culling = True
```

Para verla también en el modo *Solid*, activa **Backface Culling** en el desplegable de
sombreado del visor. En *Cycles* esa casilla no filtra los rayos: hay que mezclar un nodo
*Geometry → Backfacing* con un *Transparent BSDF*. El visor de lite2obj ya lo aplica siempre,
como Minecraft.

Notas de fidelidad:

- **Variantes de terreno**: `stone`, `dirt` o `grass_block` traen varias rotaciones y Minecraft
  elige una por posición; el export las reparte igual (con un *hash* estable), así que el suelo
  no sale repetitivo.
- **Caras superpuestas**: si dos caras del mismo bloque comparten plano (la hierba y su
  *overlay* lateral) se hornean en una sola textura. Blender descarta caras repetidas, así que
  de otro modo perdería el *overlay*.
- **Caras repetidas entre bloques**: losas, escaleras, cristales y alfombras comparten caras
  con sus vecinos porque éstos no son cubos opacos; Minecraft también las dibuja dos veces.
  Blender puede descartarlas al importar (misma textura, sin diferencia visible).
- **UV del modelo**: se respeta el `uv` explícito de cada cara (y el derivado de la caja si
  falta), como Minecraft. Es lo que hace que macetas, faroles, vallas y plantas —varias piezas
  dibujadas en un mismo PNG— no estiren la textura entera en cada cara.
- **Cristal**: dos bloques del mismo cristal no dibujan la cara común
  (`AbstractGlassBlock.skipRendering`), aunque el cristal no sea un cubo opaco. Así una pared
  de cristal no enseña la retícula de caras internas que Minecraft oculta; dos cristales
  tintados de distinto color sí conservan la unión.
- **Vidrieras y vallas**: los `.litematic` guardan las conexiones tal como estaban en el mundo,
  y a veces una vidriera colocada antes que su vecina se queda con `north/south/east/west` en
  `false`. Se normalizan como haría Minecraft: se añaden los enlaces entre vidrieras/barras y
  entre vallas de la misma familia, y se quitan los que apuntan a aire o fuera de la región.
  Además se oculta la cara de unión entre vidrieras del mismo tipo, como hace
  `IronBarsBlock.skipRendering`: sin eso, cada bloque de un panel continuo dejaba una línea
  vertical con la textura `*_pane_top`.
- **Tintes de bioma**: se usa el color de *plains* para césped, hojas y agua. La hojarasca
  (`leaf_litter`) usa la colormap `dry_foliage`, así que sale marrón y no con el verde del
  césped. Un bloque con `tintindex` pero sin color de bloque registrado (la sierra del
  cortapiedras, el bambú, un caldero de lava...) se deja sin teñir, como vanilla.
- **Fluidos**: el agua se tiñe con el color por defecto (bioma *plains*) y las caras entre
  bloques del mismo fluido se descartan. Sin ese descarte, el agua translúcida dibujaba todas
  sus paredes internas y se veía como una rejilla casi opaca. La superficie no es plana por
  bloque: se calculan cuatro alturas de esquina como `FluidRenderer.calculateAverageHeight`,
  promediando la altura propia con los vecinos y la diagonal (las alturas a partir de `0.8`
  pesan diez veces más). Bloques contiguos comparten exactamente el borde, así que una
  corriente de niveles `0, 1, 2…` forma una rampa continua en vez de escalones de `1/9`. La
  altura propia sigue siendo `8/9` para una fuente y baja `1/9` por nivel; con el mismo fluido
  encima el bloque se llena hasta `1`. Los bloques *waterlogged* (losas, escaleras, vallas,
  plantas…) conservan su geometría y además dibujan el agua que contienen; sus caras de
  fluido se marcan aparte para que el descarte entre fluidos no borre el canto del bloque.
  Las caras laterales usan siempre la textura de flujo (`water_flow`/`lava_flow`), como
  vanilla, con la V recorriendo la altura de la caída: así una cascada muestra rayas
  verticales y no la textura quieta. La superficie usa `water_still`/`lava_still` cuando no
  hay corriente y la textura de flujo girada hacia la dirección de la corriente cuando la hay.
  La superficie y el fondo se desplazan `0.001`, como `FluidRenderer`; las caras laterales se
  meten `0.001` hacia dentro por el mismo motivo.
- **Entidades y *block entities***: además de la rejilla se dibujan los cofres (simples,
  dobles, atrapados, de ender y de cobre), estandartes, cajas de shulker, macetas decoradas,
  calaveras, conductos, campanas, marcos de objeto (con su objeto y su giro), cuadros,
  soportes de armadura, objetos tirados, flechas/tridentes, barcos y balsas (también con
  cofre), vagonetas (con el bloque que llevan: cofre, tolva, horno, TNT, bloque de comandos
  o generador) y las criaturas. Los modelos de
  entidad no están en `assets/.../models`, sino en clases Java, así que se incluyen ya
  horneados en `entity_models.json`; `entity_textures.json` guarda la textura de cada capa.
  La colocación copia las matrices de vanilla (`LivingEntityRenderer`, `ChestRenderer`,
  `ItemFrameRenderer`…), incluyendo bebés, variantes de color y las poses NBT del soporte de
  armadura. Los objetos de los marcos se resuelven con el árbol `items/<id>.json` y las
  transformaciones `display.fixed` del modelo. Las criaturas están **desactivadas por
  defecto**: `--mobs` (o la casilla *Dibujar mobs* de la app) las añade. `--no-entities` (o
  la casilla *Dibujar entidades*) deja sólo los bloques y sigue apagando también las
  criaturas. Las calaveras y cabezas se reconstruyen desde su blockstate cuando el
  `.litematic` no trae `TileEntities`; el modelo `block/skull` no tiene geometría y sin
  esto se veía el cubo de respaldo con la textura de partículas. Los estandartes y la
  campana también se reconstruyen desde el blockstate: en el estandarte, de momento sólo
  se pinta su color base y los patrones se ignoran; la campana recupera así su cuerpo,
  que no está en el modelo de bloque.
  Limitaciones: no se dibujan armaduras ni equipación, los patrones de estandarte quedan
  para más adelante y los modelos de objeto con renderer especial (`special`) se omiten.
  Los cuadros usan el `variant` del NBT y caen a `kebab` si falta.
- **Entidades cojín**: `minecraft:cushion` no es un bloque, sino una entidad que Litematica
  guarda en `Region.Entities`. Se renderiza con la geometría real de `CushionModel`
  (caja de `1 x 0.25 x 1`, `CubeDeformation(-0.005)`), su textura
  `entity/cushion/<color>_cushion.png` y el giro de `CushionRenderer`. `Pos` se interpreta
  relativo a la esquina 1 de la selección (`Position`), no a la esquina mínima del recorte.
  Así aparecen tanto en el visor como en el OBJ.
- **`uvlock`**: implementado como Minecraft. Antes de aplicar el giro del variant se giran las
  UV absolutas alrededor del centro de la textura (píxel `8,8`), así que la textura no gira con
  el bloque. Es lo que mantiene derecha la contrahuella de las escaleras boca abajo
  (`half=top`, `x: 180`) y la orientación de las esquinas `inner_*`/`outer_*`; sin ello esa
  cara mostraba la mitad inferior de la textura invertida.

### Aplicación de escritorio

```bash
uv run lite2obj gui esquema.litematic
# lanzador directo (mismo resultado):
lite2obj-gui esquema.litematic
```

La ventana reúne todo el flujo, al estilo de j-mc-2-obj:

- **Esquemático**: desplegable con los `.litematic` detectados, botón *Abrir…* y arrastrar y
  soltar sobre la ventana.
- **Resource packs**: lista con casillas; arriba tiene más prioridad y los assets que falten
  caen al vanilla.
- **Vista y render**: descartar caras ocultas, *backface culling* como Minecraft, tintes de
  bioma, entidades y *block entities*, criaturas (desactivadas por defecto), resolución del
  atlas (×1/×2/×4), texturas suavizadas, rejilla, ejes, wireframe y corte por capas Y.
- **Exportar a Blender**: carpeta, nombre, agrupación, escala y `Z arriba` (nativo de Blender).

La vista 3D se renderiza con el OpenGL nativo de Qt (PySide6), no con el navegador: órbita con
el botón izquierdo, desplazar con el central/derecho (o `Shift`+izquierdo), zoom con la rueda
y doble clic para reencuadrar. Atajos: `F5` renderizar, `Ctrl+E` exportar, `Ctrl+Shift+S`
captura, `F` reencuadrar, `G` rejilla, `W` wireframe.

En Linux con NVIDIA bajo Wayland la app usa `QT_QPA_PLATFORM=xcb` (XWayland) automáticamente
porque la creación del contexto EGL falla; se puede desactivar definiendo esa variable antes.

### CLI sin interfaz

Todo lo que hace la app está disponible por línea de comandos: `info`, `list`, `export` y
`gui`. Sirve para automatizar exportaciones en máquinas sin escritorio (la vista 3D no se
usa, sólo la malla y el OBJ).

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
  mesh.py        malla del esquemático (caras, UV, culling, fluidos)
  entities.py    entidades y block entities (matrices y poses de vanilla)
  items.py       modelos de objeto para marcos y objetos tirados
  entity_models.json / entity_textures.json  instantánea de modelos de entidad
  export.py      OBJ + MTL + atlas
  pipeline.py    alto nivel (CLI y app) + búfer binario del visor
  cli.py         interfaz de línea de comandos
  gui/           app de escritorio (viewports OpenGL, cámara, ventana Qt)
tests/
  test_nbt.py                 lector NBT contra un escritor de tests
  test_litematic.py           bit-packing, tamaños negativos, multi-región, fixture real
  test_models.py              rotaciones, variants/multipart, parent, texturas
  test_textures.py            empaquetado del atlas, tintes, transparencia
  test_mesh.py                orientación de caras/UV, culling, entidades, malla real
  test_export.py              OBJ/MTL, winding, deduplicado, agrupación
  test_gui.py                 cámara, buffers del viewport y arranque offscreen
  test_cli.py                 comandos de la CLI
  test_schematics_integration.py  valida todos los .litematic de la máquina
  test_coverage_integration.py    cobertura de geometría sobre los esquemáticos
```

Los triángulos se ordenan por modo de transparencia (opaco, *cutout*, translúcido) dentro de
la sopa de vértices, de modo que el visor dibuja un grupo por modo sin ordenar nada en tiempo
real.
