# lite2obj

[Español](README.es.md) · **English**

Render **Minecraft Litematica** (`.litematic`) schematics in 3D and export them to **Blender**
as OBJ + MTL + a texture atlas. Native desktop app (PySide6 + Qt OpenGL) and command-line
interface, written in Python. Inspired by
[j-mc-2-obj](https://github.com/jmc2obj/j-mc-2-obj).

[![CI](https://github.com/pinox12/lite2obj/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/pinox12/lite2obj/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.14-blue)
![Version](https://img.shields.io/badge/version-1.1.0-orange)
![License](https://img.shields.io/badge/license-MIT-green)

![lite2obj desktop application](docs/assets/screenshot-en.png)

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Desktop application](#desktop-application)
  - [Command line](#command-line)
  - [Exporting to Blender](#exporting-to-blender)
  - [File association](#file-association)
- [Configuration](#configuration)
  - [Language](#language)
  - [Minecraft asset file](#minecraft-asset-file)
  - [Resource packs](#resource-packs)
  - [Environment variables](#environment-variables)
- [Development](#development)
  - [Tests and linting](#tests-and-linting)
  - [Portable packaging](#portable-packaging)
  - [CI/CD and website](#cicd-and-website)
  - [Project structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

## Features

- **`.litematic` parser**: NBT reader with no external dependencies, YZX bit packing, negative
  region sizes and multiple regions per file.
- **Faithful block models**: blockstate variants and multipart selected per position, parent
  inheritance, element and variant rotations, `uvlock`, and legacy block aliases.
- **Textures and atlas**: power-of-two atlas, biome tints, animated textures cropped to their
  first frame, and explicit resource packs stacked on top of vanilla.
- **Culling like Minecraft**: `cullface` against full opaque cubes, shared faces for equal
  glass, panes and fences, and sloped fluids with `waterlogged` support.
- **Entities and block entities**: chests, banners, shulker boxes, decorated pots, skulls,
  bells, item frames, paintings, armor stands, dropped items, projectiles, boats, minecarts and
  cushions. Mobs are opt-in.
- **Blender-ready export**: OBJ + MTL + `atlas.png`, grouped by block or transparency, optional
  Z-up axis and geometry scale.
- **Desktop viewer**: orbit camera, Y-layer slicing, wireframe, grid, axes, screenshots and
  drag and drop.
- **Headless CLI**: `info`, `list`, `assets` and `export` for automated pipelines.
- **Bilingual interface**: Spanish and English, detected from the system language.

The phase-by-phase roadmap lives in [`PLAN.md`](PLAN.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) and **Python 3.14** (uv installs it if needed).
- The `lite2obj-assets.l2oassets` file, which contains the blockstates, models and textures
  used to render. The program does not scan the disk for Minecraft installs or resource packs;
  if the file is missing it shows an alert with the instructions to generate it. The portable
  builds already include it.
- A Minecraft **client jar** only if you want to generate or update that file.
- **OpenGL 3.3** for the 3D view (the export CLI works without a GPU).

## Installation

```bash
git clone https://github.com/pinox12/lite2obj.git
cd lite2obj
uv sync
uv run lite2obj --version
```

To use the desktop app without installing Python, download the AppImage or the `.exe` from
[Releases](https://github.com/pinox12/lite2obj/releases) and see
[Portable packaging](#portable-packaging).

## Usage

### Desktop application

```bash
uv run lite2obj gui
uv run lite2obj gui schematic.litematic
lite2obj-gui schematic.litematic   # direct launcher, same result
```

The window brings the whole flow together, in the style of j-mc-2-obj:

- **Schematic**: *Open…* and *Reload* buttons, plus drag and drop onto the window. `.litematic`
  files are not looked up on disk.
- **Minecraft assets**: a green ✓ means the base assets are configured, and shows the Minecraft
  version they were generated from. If they are missing or invalid it says so and lets you
  generate them by choosing the client jar. If the schematic contains blocks that are not in
  the `.l2oassets` (for example from a newer version), the ✓ becomes a ⚠ with the omitted
  blocks and a warning to update the assets from a recent client jar.
- **Resource packs**: optional; added manually with *Add .zip…* (several at once) or *Add
  folder…* (one pack or a folder with several), enabled with checkboxes and ordered with
  *Up*/*Down*. Top = highest priority, and packs are never scanned on disk.
- **View and render**: cull hidden faces, backface culling like Minecraft, biome tints,
  entities and block entities, mobs (disabled by default), atlas resolution (×1/×2/×4),
  smoothed textures, grid, axes, wireframe and Y-layer slicing.
- **Export to Blender**: folder, name, grouping, scale and *Z up* (Blender native).
- **File → Generate asset pack…**: asks for the client jar, extracts the assets into a
  `.l2oassets` and uses it for the next render.
- **File → Associate .litematic files…**: makes double-clicking open the schematic in lite2obj.
- **Help → Render test litematic**: opens `all_blocks_and_entities.litematic`, the reference
  schematic with all blocks, combinations and entities.

The 3D view uses Qt's native OpenGL (PySide6), not a browser: orbit with the left button, pan
with the middle/right button (or `Shift`+left), zoom with the wheel and double-click to
reframe. Shortcuts: `F5` render, `Ctrl+E` export, `Ctrl+Shift+S` screenshot, `F` reframe, `G`
grid, `W` wireframe.

On Linux with NVIDIA under Wayland the app uses `QT_QPA_PLATFORM=xcb` (XWayland) automatically
because EGL context creation fails; you can override it by setting that variable beforehand.

### Command line

```bash
# Metadata and block count of a schematic (--blocks 0 lists every type)
uv run lite2obj info schematic.litematic

# Show the active asset file (does not look for schematics or packs)
uv run lite2obj list

# Generate the asset file and inspect its manifest
uv run lite2obj assets build --jar path/client.jar
uv run lite2obj assets info

# Export to OBJ + MTL + atlas.png (for Blender)
uv run lite2obj export schematic.litematic output/ --name house
uv run lite2obj export schematic.litematic output/ --z-up --group-by block
uv run lite2obj export schematic.litematic output/ --packs "Mizunos 16 Craft JE_1.20.1-1.0_230720"
uv run lite2obj export schematic.litematic output/ --atlas-scale 2 --scale 0.5
# Blocks only, without entities or block entities
uv run lite2obj export schematic.litematic output/ --no-entities
# Add mobs to the other entities (they are not drawn by default)
uv run lite2obj export schematic.litematic output/ --mobs

# Open the desktop application
uv run lite2obj gui
```

Sample `info` output:

```
almacen bonito 1x4x4
  file         schematic.litematic
  author       pinox12
  size         2 x 4 x 8
  regions      1
  blocks       42
  volume       64
  data version 4790 (format v7.1)
```

The CLI is bilingual: it follows the system language and accepts `--lang en|es`. Everything
the app does is available from the command line, which is useful for automating exports on
machines without a desktop (the 3D view is not used, only the mesh and the OBJ).

### Exporting to Blender

`export` writes three files:

| File | Contents |
|---|---|
| `house.obj` | all the geometry, a single object, quad faces |
| `house.mtl` | a single `atlas` material with `map_Kd house` → `atlas.png` |
| `atlas.png` | every texture (and its tinted versions) in one image |

When importing into Blender (`File > Import > Wavefront (.obj)`) keep the three files in the
same folder and leave *Split by Object* disabled to get a single mesh. If you disable the
importer's axis conversion, use `--z-up`.

Blender draws both sides of every polygon by default, while Minecraft only draws the front
face. That is noticeable on transparent blocks: back faces can be seen through glass. OBJ/MTL
cannot store that option; enable it on the imported material (**Material Properties → Settings
→ Backface Culling**) or from Blender's Python console:

```python
bpy.data.materials["atlas"].use_backface_culling = True
```

To see it in *Solid* mode too, enable *Backface Culling* in the viewport shading dropdown. In
*Cycles* that checkbox does not filter rays: you have to mix a *Geometry → Backfacing* node with
a *Transparent BSDF*. The lite2obj viewer already applies it always, like Minecraft.

<details>
<summary><strong>Fidelity notes</strong></summary>

- **Terrain variants**: `stone`, `dirt` or `grass_block` ship several rotations and Minecraft
  picks one per position; the export distributes them the same way (with a stable hash), so the
  ground does not look repetitive.
- **Overlapping faces**: if two faces of the same block share a plane (grass and its side
  overlay) they are baked into a single texture. Blender discards repeated faces, so otherwise
  the overlay would be lost.
- **Repeated faces between blocks**: slabs, stairs, glass and carpets share faces with their
  neighbours because those are not opaque cubes; Minecraft draws them twice too. Blender may
  discard them on import (same texture, no visible difference).
- **Model UVs**: each face's explicit `uv` is respected (and the one derived from the box when
  missing), like Minecraft. That is what keeps flower pots, lanterns, fences and plants —
  several pieces drawn in a single PNG — from stretching the whole texture on every face.
- **Animated textures**: they are cropped to the first frame only if their `.mcmeta` declares
  an animation, using the stated frame size (`width`/`height`). It is not guessed from the
  shape: a tall texture that does not animate (the witch or the strider, `64x128`) keeps its
  full height and its UVs do not shift.
- **Glass**: two blocks of the same glass do not draw the shared face
  (`AbstractGlassBlock.skipRendering`), even though glass is not an opaque cube. Thus a glass
  wall does not show the grid of internal faces that Minecraft hides; two stained glasses of
  different colours do keep the seam.
- **Panes and fences**: `.litematic` files store connections as they were in the world, and
  sometimes a pane placed before its neighbour keeps `north/south/east/west` at `false`. They
  are normalized the way Minecraft would: links between panes/bars and between fences of the
  same family are added, and links pointing at air or outside the region are removed. The joint
  face between panes of the same type is also hidden, like `IronBarsBlock.skipRendering`:
  without it, every block of a continuous pane left a vertical line with the `*_pane_top`
  texture.
- **Biome tints**: the plains colour is used for grass, leaves and water. Leaf litter
  (`leaf_litter`) uses the `dry_foliage` colormap, so it comes out brown and not grass green. A
  block with a `tintindex` but no registered block colour (the stonecutter saw, bamboo, a lava
  cauldron…) is left untinted, like vanilla.
- **Fluids**: water is tinted with the default colour (plains biome) and faces between blocks
  of the same fluid are discarded. Without that culling, translucent water drew all its
  internal walls and looked like an almost opaque grid. The surface is not flat per block: four
  corner heights are computed like `FluidRenderer.calculateAverageHeight`, averaging the
  block's own height with its neighbours and the diagonal (heights from `0.8` weigh ten times
  more). Contiguous blocks share the edge exactly, so a stream with levels `0, 1, 2…` forms a
  continuous ramp instead of `1/9` steps. The block's own height is still `8/9` for a source and
  drops `1/9` per level; with the same fluid above, the block fills up to `1`. Waterlogged
  blocks (slabs, stairs, fences, plants…) keep their geometry and also draw the water they
  contain; their fluid faces are marked separately so fluid-to-fluid culling does not erase the
  block's edge. Side faces always use the flowing texture (`water_flow`/`lava_flow`), like
  vanilla, with V running along the fall height: a waterfall shows vertical streaks and not the
  still texture. The surface uses `water_still`/`lava_still` when there is no flow and the
  flowing texture rotated toward the flow direction when there is. The surface and bottom are
  offset by `0.001`, like `FluidRenderer`; side faces are pushed `0.001` inwards for the same
  reason.
- **Entities and block entities**: besides the grid, it draws chests (single, double, trapped,
  ender and copper), banners, shulker boxes, decorated pots, skulls, conduits, bells, item
  frames (with their item and rotation), paintings, armor stands, dropped items, arrows and
  tridents, boats and rafts (also with a chest), minecarts (with the block they carry: chest,
  hopper, furnace, TNT, command block or spawner) and mobs. Entity models are not in
  `assets/.../models` but in Java classes, so they are included pre-baked in
  `entity_models.json`; `entity_textures.json` stores each layer's texture. Placement copies
  vanilla matrices (`LivingEntityRenderer`, `ChestRenderer`, `ItemFrameRenderer`…), including
  babies, colour variants and the armor stand's NBT poses. Item frame items are resolved with
  the `items/<id>.json` tree and the model's `display.fixed` transformations. Mobs are
  **disabled by default**: `--mobs` (or the app's *Draw mobs* checkbox) adds them.
  `--no-entities` leaves only blocks and also keeps mobs off. Skulls and heads are rebuilt from
  their blockstate when the `.litematic` has no `TileEntities`; the `block/skull` model has no
  geometry and without this the fallback cube with the particle texture was shown. Banners and
  the bell are also rebuilt from the blockstate: for banners, only the base colour is painted
  for now and patterns are ignored; the bell thus recovers its body, which is not in the block
  model. Limitations: armor and equipment are not drawn, banner patterns are left for later and
  item models with a special renderer (`special`) are skipped. Paintings use the NBT `variant`
  and fall back to `kebab` if missing.
- **Cushion entities**: `minecraft:cushion` is not a block but an entity that Litematica stores
  in `Region.Entities`. It is rendered with the real `CushionModel` geometry (a
  `1 x 0.25 x 1` box, `CubeDeformation(-0.005)`), its `entity/cushion/<color>_cushion.png`
  texture and the `CushionRenderer` rotation. `Pos` is interpreted relative to corner 1 of the
  selection (`Position`), not the minimum corner of the crop. That way they appear both in the
  viewer and in the OBJ.
- **`uvlock`**: implemented like Minecraft. Before applying the variant rotation, the absolute
  UVs are rotated around the texture centre (pixel `8,8`), so the texture does not rotate with
  the block. That is what keeps the riser of upside-down stairs upright (`half=top`, `x: 180`)
  and the orientation of the `inner_*`/`outer_*` corners; without it that face showed the lower
  half of the texture upside down.

</details>

### File association

From the app: **File → Associate .litematic files…**. The association is stored only for the
current user and does not require administrator permissions.

- **Linux**: creates `~/.local/share/applications/lite2obj.desktop` and
  `~/.local/share/mime/packages/lite2obj.xml`, updates the caches and sets the
  `application/x-litematic` MIME type. If you move the AppImage, run the action again so the
  launcher points to the new path. *Remove association* deletes those files and the
  `mimeapps.list` entry.
- **Windows**: writes the `HKCU\Software\Classes` keys for `.litematic`, so nothing needs to be
  installed. Windows may ask for confirmation the first time you open a file; choose lite2obj
  and tick *Always use*.

The AppImage includes the `.desktop` file and the MIME XML in `usr/share`, but an AppImage does
not integrate itself: the app action installs the association in the user's profile.

## Configuration

### Language

lite2obj follows the system language: Spanish (`es`) or English (`en`). Any other language
falls back to English. You can force one with `LITE2OBJ_LANG` or the global `--lang` option:

```bash
LITE2OBJ_LANG=en uv run lite2obj info file.litematic
uv run lite2obj --lang es info file.litematic
```

The same variable applies to the desktop app (`LITE2OBJ_LANG=es uv run lite2obj-gui`). Adding
another language only requires a new catalog in `src/lite2obj/i18n.py`.

### Minecraft asset file

lite2obj does not read the client jar or scan the disk for Minecraft installs: it renders with
a `.l2oassets` file that contains only what it needs (blockstates, models, textures with their
`.mcmeta`, item models and painting variants). It is a regular zip with a
`lite2obj-assets.json` manifest; it contains no code, sounds or recipes.

```bash
# Generate the file from a specific client jar
uv run lite2obj assets build --jar ~/.minecraft/versions/1.21.5/1.21.5.jar

# Same, without repeating the path on every command
export LITE2OBJ_CLIENT_JAR=~/.minecraft/versions/1.21.5/1.21.5.jar
uv run lite2obj assets build

# Custom destination and overwrite
uv run lite2obj assets build --jar path/client.jar \
    --output ./lite2obj-assets.l2oassets --force

# Show the version and contents of the file the program uses
uv run lite2obj assets info
```

The default destination is `lite2obj-assets.l2oassets` inside the user data directory
(`~/.local/share/lite2obj` on Linux, `%LOCALAPPDATA%\lite2obj` on Windows). Without scanning
the disk, the program only looks in these locations, in this order: PyInstaller bundle,
executable folder and user data. `LITE2OBJ_ASSET_PACK` forces a specific path.

In the desktop app it lives under **File → Generate asset pack…** (or in the side panel): it
asks for the client jar, saves the file and uses it for the next render. If it starts without
assets, it shows an alert with those instructions.

### Resource packs

Resource packs are optional and are only applied when passed explicitly with `--packs` or added
in the app with *Add .zip…* / *Add folder…*. They are stacked on top of the vanilla assets; the
last one has the highest priority. lite2obj never looks for packs on disk.

### Environment variables

| Variable | Used by | Purpose |
|---|---|---|
| `LITE2OBJ_LANG` | app and CLI | Forces the interface language: `en` or `es`. |
| `LITE2OBJ_ASSET_PACK` | app and CLI | Explicit path to the `.l2oassets` file. |
| `LITE2OBJ_CLIENT_JAR` | `assets build` | Client jar used when `--jar` is not given. |
| `LITE2OBJ_SKIP_ASSETS` | packaging | Builds the portable without the asset pack. |
| `LITE2OBJ_ONEFILE` | packaging | Forces a single binary on Linux too. |
| `QT_QPA_PLATFORM` | app | Set it to `xcb` to avoid the NVIDIA + Wayland EGL issue. |

## Development

### Tests and linting

```bash
uv run pytest          # test suite
uv run ruff check .    # lint
uv run ruff format .   # formatting
uv run mypy src        # type checking
```

Real-asset tests are skipped when no `.l2oassets` is available, so a green run on a fresh
machine may not have exercised the model/texture/mesh pipeline. See [`AGENTS.md`](AGENTS.md)
for the architecture invariants and test helpers.

<a id="portable-packaging"></a>

<details>
<summary><strong>Portable packaging</strong></summary>

The app can be distributed without the user installing Python or its dependencies. Packaging
uses PyInstaller and is done **on the target system**: there is no cross-compilation, so the
AppImage is built on Linux and the `.exe` on Windows.

```bash
# Packaging dependencies (not needed for development)
uv sync --group packaging
```

**Linux: AppImage**

```bash
./packaging/build_appimage.sh
```

Produces `dist/lite2obj-<version>-x86_64.AppImage`. It is a single executable file:

```bash
chmod +x dist/lite2obj-*.AppImage
./dist/lite2obj-*.AppImage
```

The script downloads `appimagetool` into `build/tools` if it is not on `PATH`. If the machine
has no FUSE, you can run it with `APPIMAGE_EXTRACT_AND_RUN=1`. You can also point to a specific
tool with `APPIMAGETOOL=/path/to/appimagetool`.

**Windows: portable `.exe`**

From PowerShell on Windows:

```powershell
.\packaging\build_windows.ps1
```

Produces `dist\lite2obj.exe`, a single file that can be copied to any folder and run without an
installer. It does not require Python to be installed.

**Builds without textures**

GitHub releases are published **without** `lite2obj-assets.l2oassets` or Minecraft textures. To
reproduce that build locally:

```bash
LITE2OBJ_SKIP_ASSETS=1 ./packaging/build_appimage.sh
```

```powershell
$env:LITE2OBJ_SKIP_ASSETS = "1"
.\packaging\build_windows.ps1
```

The variable makes `prepare_assets.py` prepare nothing and `lite2obj.spec` include no pack, even
if one exists in `build/assets`. The app still starts and asks for the client jar from
**File → Generate asset pack…**. `prepare_assets.py --optional` is an alternative: it allows
packaging without a pack, but includes it if available.

**Notes**

- The packaging scripts run `packaging/prepare_assets.py`, which leaves
  `build/assets/lite2obj-assets.l2oassets` before PyInstaller. If it already exists it reuses
  it; if not, it copies the one lite2obj uses (user data or `LITE2OBJ_ASSET_PACK`); and if that
  one does not exist either, it generates it from the client jar passed with `--jar` or
  `LITE2OBJ_CLIENT_JAR`. This way the AppImage and the `.exe` **carry the assets inside** and
  work without Minecraft installed.
- The file is ignored by git (`*.l2oassets`). Keep in mind that the resulting portable
  redistributes Minecraft assets; check Mojang's terms before publishing it.
- If there is no asset file and no client jar, packaging stops with a message explaining how to
  generate it, unless `--skip`, `--optional` or `LITE2OBJ_SKIP_ASSETS=1` is used. After
  updating Minecraft, regenerate the file with
  `uv run python packaging/prepare_assets.py --force --jar <path-to-client.jar>` before
  packaging.
- CI/CD builds use `LITE2OBJ_SKIP_ASSETS=1`; the workflows check that the result contains no
  `.l2oassets`.
- The 3D view needs OpenGL 3.3; the target machine must have graphics drivers.
- The icon is regenerated with `uv run python packaging/make_icon.py`.
- Artifacts (`dist/`, `build/`) are in `.gitignore`.
- To force a single binary on Linux too: `LITE2OBJ_ONEFILE=1 uv run --group packaging
  pyinstaller packaging/lite2obj.spec`.

</details>

### CI/CD and website

The repository includes three GitHub Actions workflows:

| Workflow | When | What it does |
|---|---|---|
| `.github/workflows/ci.yml` | push to `main`/`master` and pull requests | `ruff format --check`, `ruff check`, `mypy` and `pytest` on Linux and Windows. On push it also builds the AppImage and `.exe` **without** `.l2oassets` and checks that the pack was not included by mistake. |
| `.github/workflows/release.yml` | `v*` tags or a manual run with a tag | Verifies that the tag matches `__version__`, builds the AppImage and the `.exe` without textures and publishes both plus `SHA256SUMS` on GitHub Releases. |
| `.github/workflows/pages.yml` | changes in `docs/` | Publishes the landing page to GitHub Pages. |

The landing page is in [`docs/`](docs/): it is HTML, CSS and a little JavaScript, with no build
or external dependencies. It is available in Spanish (`docs/index.html`) and English
(`docs/en/index.html`).

To publish a version:

```bash
git tag v1.1.0
git push origin v1.1.0
```

The tag must match `__version__` in `src/lite2obj/__init__.py`. Release builds do not include
textures: the user generates their `.l2oassets` when they open the app.

<a id="project-structure"></a>

<details>
<summary><strong>Project structure</strong></summary>

```
src/lite2obj/
  nbt.py         NBT reader (no dependencies)
  litematic.py   .litematic parser -> regions, palette, voxels
  paths.py       asset file location (without scanning the disk)
  assetpack.py   .l2oassets file generation/reading
  associations.py .litematic association (Linux .desktop/MIME, Windows registry)
  assets.py      resource stack (asset file + explicit resource packs)
  models.py      blockstates and models (variants/multipart, rotations)
  fallbacks.py   substitute geometry (fluids, block entities)
  aliases.py     blocks renamed between versions
  textures.py    texture atlas, tinted sprites
  mesh.py        schematic mesh (faces, UVs, culling, fluids)
  entities.py    entities and block entities (vanilla matrices and poses)
  items.py       item models for frames and dropped items
  entity_models.json / entity_textures.json  entity model snapshot
  export.py      OBJ + MTL + atlas
  pipeline.py    high level (CLI and app) + viewer binary buffer
  cli.py         command-line interface
  gui/           desktop app (OpenGL viewport, camera, Qt window)
tests/
  test_nbt.py                 NBT reader against a test writer
  test_litematic.py           bit packing, negative sizes, multi-region, real fixture
  test_models.py              rotations, variants/multipart, parent, textures
  test_textures.py            atlas packing, tints, transparency
  test_mesh.py                face/UV orientation, culling, entities, real mesh
  test_export.py              OBJ/MTL, winding, dedupe, grouping
  test_gui.py                 camera, viewport buffers and offscreen startup
  test_associations.py        .litematic association (Linux/Windows)
  test_packaging.py           builds with and without the asset file
  test_cli.py                 CLI commands
  test_i18n.py                language detection and translation catalog
  test_schematics_integration.py  validates the reference schematic
  test_coverage_integration.py    geometry coverage over the reference schematic
docs/                         static landing page for GitHub Pages
packaging/                    PyInstaller, AppImage, .exe and asset preparation
.github/workflows/            CI, release and Pages
```

Triangles are sorted by transparency mode (opaque, cutout, translucent) inside the vertex
soup, so the viewer draws one group per mode without sorting anything at runtime.

</details>

## Contributing

1. Fork the repository.
2. Create a branch: `git checkout -b feature/my-feature`.
3. Make your changes and add tests when possible.
4. Run the checks: `uv run ruff format . && uv run ruff check . && uv run mypy src && uv run pytest`.
5. Commit your changes (`git commit -m 'Add my feature'`).
6. Push the branch and open a Pull Request.

Keep the conventions described in [`AGENTS.md`](AGENTS.md): docs, docstrings, comments, CLI/UI
strings and commit messages are written in Spanish, code identifiers in English, and new
user-facing strings must be added to the `_EN` catalog in `src/lite2obj/i18n.py`
(`tests/test_i18n.py` fails otherwise).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
