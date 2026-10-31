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
| F8 | Tests | ✅ 290 tests |

El plan completo está en [`PLAN.md`](PLAN.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) y Python 3.14 (uv lo instala si hace falta).
- El archivo `lite2obj-assets.l2oassets` para resolver modelos y texturas. El programa no
  busca instalaciones de Minecraft ni resource packs en el disco; si falta el archivo,
  muestra una alerta con las instrucciones para generarlo. Los portables ya lo incluyen.
- Un client jar de Minecraft **sólo** si quieres generar o actualizar ese archivo.
- OpenGL 3.3 para la vista 3D (la CLI de export funciona sin GPU).

## Instalación

```bash
uv sync
```

## Archivo de assets de Minecraft

lite2obj no lee el client jar ni busca instalaciones de Minecraft en el disco: renderiza con
un archivo `.l2oassets` que contiene sólo lo necesario (blockstates, modelos, texturas con
sus `.mcmeta`, modelos de objeto y variantes de cuadro). Es un zip normal con un manifiesto
`lite2obj-assets.json`; no incluye código, sonidos ni recetas.

```bash
# Genera el archivo desde un client jar concreto
uv run lite2obj assets build --jar ~/.minecraft/versions/1.21.5/1.21.5.jar

# Igual, sin repetir la ruta en cada orden
export LITE2OBJ_CLIENT_JAR=~/.minecraft/versions/1.21.5/1.21.5.jar
uv run lite2obj assets build

# Destino concreto y sobrescritura
uv run lite2obj assets build --jar ruta/client.jar \
    --output ./lite2obj-assets.l2oassets --force

# Consulta la versión y el contenido del archivo que usa el programa
uv run lite2obj assets info
```

El destino por defecto es `lite2obj-assets.l2oassets` dentro de los datos del usuario
(`~/.local/share/lite2obj` en Linux, `%LOCALAPPDATA%\lite2obj` en Windows). Sin recorrer el
disco, el programa sólo mira estas ubicaciones, por este orden: bundle de PyInstaller,
carpeta del ejecutable y datos del usuario. `LITE2OBJ_ASSET_PACK` fuerza una ruta concreta.
Los resource packs sólo se aplican si se pasan explícitamente con `--packs` o se añaden en
la app con *Añadir .zip…* / *Añadir carpeta…*.

En la app de escritorio está en **Archivo → Generar archivo de assets…** (o en el panel
lateral): pide el client jar, guarda el archivo y lo usa en el siguiente render. Si arranca
sin assets, muestra una alerta con esas instrucciones.

## Empaquetado portable

La app se puede distribuir sin que el usuario instale Python ni las dependencias. El
empaquetado usa PyInstaller y se hace **en el sistema de destino**: no hay compilación
cruzada, así que la AppImage se genera en Linux y el `.exe` en Windows.

```bash
# Dependencias de empaquetado (no hacen falta para desarrollar)
uv sync --group packaging
```

### Linux: AppImage

```bash
./packaging/build_appimage.sh
```

Genera `dist/lite2obj-<versión>-x86_64.AppImage`. Es un único archivo ejecutable:

```bash
chmod +x dist/lite2obj-*.AppImage
./dist/lite2obj-*.AppImage
```

El script descarga `appimagetool` en `build/tools` si no está en `PATH`. Si la máquina no
tiene FUSE, se puede arrancar con `APPIMAGE_EXTRACT_AND_RUN=1`. También se puede indicar una
herramienta concreta con `APPIMAGETOOL=/ruta/a/appimagetool`.

### Windows: `.exe` portable

Desde PowerShell en Windows:

```powershell
.\packaging\build_windows.ps1
```

Genera `dist\lite2obj.exe`, un único archivo que se puede copiar a cualquier carpeta y
ejecutar sin instalador. No requiere Python instalado.

### Notas

- Los scripts de empaquetado ejecutan `packaging/prepare_assets.py`, que deja
  `build/assets/lite2obj-assets.l2oassets` antes de PyInstaller. Si ya existe lo reutiliza;
  si no, copia el que usa lite2obj (datos del usuario o `LITE2OBJ_ASSET_PACK`); y si
  tampoco existe, lo genera desde el client jar indicado con `--jar` o
  `LITE2OBJ_CLIENT_JAR`. Así la AppImage y el `.exe` **llevan dentro los assets** y
  funcionan sin Minecraft instalado.
- El archivo queda ignorado por git (`*.l2oassets`). Ten en cuenta que el portable
  resultante redistribuye assets de Minecraft; revisa las condiciones de Mojang antes de
  publicarlo.
- Si no hay archivo de assets ni client jar indicado, el empaquetado se detiene con un
  mensaje que indica cómo generarlo. Tras actualizar Minecraft, regenera el archivo con
  `uv run python packaging/prepare_assets.py --force --jar <ruta-al-client.jar>` antes de
  empaquetar.
- La vista 3D necesita OpenGL 3.3; el equipo de destino debe tener drivers gráficos.
- El icono se regenera con `uv run python packaging/make_icon.py`.
- Los artefactos (`dist/`, `build/`) están en `.gitignore`.
- Para forzar un único binario también en Linux: `LITE2OBJ_ONEFILE=1 uv run --group
  packaging pyinstaller packaging/lite2obj.spec`.

## Uso

```bash
# Metadatos y recuento de bloques de un esquemático
uv run lite2obj info esquema.litematic

# Muestra el archivo de assets activo (no busca esquemáticos ni packs)
uv run lite2obj list

# Genera el archivo de assets y consulta su manifiesto
uv run lite2obj assets build --jar ruta/client.jar
uv run lite2obj assets info

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
- **Texturas animadas**: se recortan al primer fotograma sólo si su `.mcmeta` declara una
  animación, usando el tamaño de fotograma indicado (`width`/`height`). No se adivina por la
  forma: una textura alta que no anima (la bruja o el strider, `64x128`) conserva toda su
  altura y sus UV no se desplazan.
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

- **Esquemático**: botón *Abrir…*, *Recargar* y arrastrar y soltar sobre la ventana. No se
  buscan `.litematic` por el disco.
- **Assets de Minecraft**: muestra con un ✓ verde que los assets base están configurados y la
  versión de Minecraft desde la que se generaron. Si faltan o no son válidos, lo indica y
  permite generarlos eligiendo el client jar.
- **Resource packs**: opcional; se añaden a mano con *Añadir .zip…* (varios a la vez) o
  *Añadir carpeta…* (un pack o una carpeta con varios), se activan con casillas y se
  ordenan con *Subir*/*Bajar*. Arriba = mayor prioridad y no se buscan packs en el disco.
- **Vista y render**: descartar caras ocultas, *backface culling* como Minecraft, tintes de
  bioma, entidades y *block entities*, criaturas (desactivadas por defecto), resolución del
  atlas (×1/×2/×4), texturas suavizadas, rejilla, ejes, wireframe y corte por capas Y.
- **Exportar a Blender**: carpeta, nombre, agrupación, escala y `Z arriba` (nativo de Blender).
- **Archivo → Generar archivo de assets…**: pide el client jar, extrae los assets a un
  `.l2oassets` y lo usa en el siguiente render.
- **Ayuda → Renderizar litemática de prueba**: abre `all_blocks_and_entities.litematic`, el
  esquemático de referencia con todos los bloques, combinaciones y entidades.

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

## Ubicación de los assets

`lite2obj` sólo usa el archivo `.l2oassets`; no detecta instalaciones de Minecraft,
resource packs ni esquemáticos. Sin recorrer el disco, lo busca en el bundle de PyInstaller,
junto al ejecutable y en los datos del usuario. Variables de entorno:
`LITE2OBJ_ASSET_PACK` (ruta explícita al archivo) y `LITE2OBJ_CLIENT_JAR` (client jar que
usa `assets build`). Los resource packs se indican con `--packs` o se añaden en la app; en
ningún caso se buscan por el disco.

## Estructura

```
src/lite2obj/
  nbt.py         lector NBT (sin dependencias)
  litematic.py   parser .litematic -> regiones, paleta, voxels
  paths.py       localización del archivo de assets (sin escanear el disco)
  assetpack.py   generación/lectura del archivo .l2oassets
  assets.py      pila de recursos (archivo de assets + resource packs explícitos)
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
  test_schematics_integration.py  valida el esquemático de referencia
  test_coverage_integration.py    cobertura de geometría sobre el esquemático de referencia
```

Los triángulos se ordenan por modo de transparencia (opaco, *cutout*, translúcido) dentro de
la sopa de vértices, de modo que el visor dibuja un grupo por modo sin ordenar nada en tiempo
real.
