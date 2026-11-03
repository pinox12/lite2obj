# AGENTS.md

`lite2obj` parses Minecraft Litematica `.litematic` files, builds a textured mesh from
vanilla assets, and exports OBJ + MTL + atlas for Blender. Python CLI plus a native
desktop GUI (PySide6 + Qt OpenGL). A single toolchain: **uv**.

## Commands

```bash
uv sync
uv run pytest                                  # full suite
uv run pytest tests/test_mesh.py::test_name    # one test
uv run ruff format . && uv run ruff check .
uv run mypy src                                # config already limits to src
uv run lite2obj info|list|export|gui ...
uv run lite2obj assets build --jar <client.jar>|info
```

GitHub Actions runs ruff, mypy and pytest on Linux and Windows (`.github/workflows/ci.yml`);
there is no pre-commit. Python is 3.14; dependencies live in `uv.lock` (no pip/poetry).
There is no JS/Node step for the app; `docs/` is plain HTML/CSS/JS.

## Conventions

- Docs, docstrings, comments, CLI/UI strings and commit messages are **Spanish**.
  Code identifiers are English. Match this.
- Ruff: line length 100, target `py314`, rules `E,F,I,UP,B,SIM`.
- `src/` layout; package `lite2obj`; CLI entry point `lite2obj.cli:main`; GUI script
  `lite2obj-gui = "lite2obj.gui:main"`.

## Tests: a green run may have tested nothing

- Real-asset tests are **asset-gated**: they call `tests/_jar.py::jar_resources()`, which
  `pytest.skip`s when there is no `.l2oassets` file. A green suite on a fresh machine can
  mean the model/texture/mesh pipeline was never exercised.
- `tests/conftest.py` puts `tests/` on `sys.path`, so helpers are imported top-level:
  `from _jar import jar_resources`, `from nbt_writer import write_nbt` (not `tests._jar`).
- Committed fixture: `tests/fixtures/store_1x4x4.litematic`.
  `tests/test_export.py::test_real_fixture_golden_counts` hard-codes mesh/OBJ counts for
  vanilla 26.3 — update it deliberately when geometry or culling changes.
- `test_schematics_integration.py` / `test_coverage_integration.py` use the committed
  `all_blocks_and_entities.litematic` at the repo root and skip if it is missing. They do
  not scan the machine for schematics.

## Architecture and invariants

Pipeline order, each layer assuming the previous:
`paths` → `assets` → `models` → `textures` → `mesh` → `export`/`pipeline` → `cli`/`gui`.

- `paths.py` locates the `.l2oassets` without scanning the disk: `LITE2OBJ_ASSET_PACK`,
  PyInstaller bundle, executable folder and user data, in that order. `assets.py` **requires**
  that file and stacks it with resource packs passed explicitly by path (the last one wins);
  it never falls back to a client jar. `assetpack.py` builds and validates the archive;
  `lite2obj assets build` requires `--jar` or `LITE2OBJ_CLIENT_JAR`. There is no Minecraft,
  resource-pack or schematic auto-discovery anywhere.
- `models.py` resolves blockstates (variants/multipart), parent inheritance and model/
  element rotations. Variants are chosen **per position** (`position_seed`), not once per
  state. Each multipart group contributes **one** variant; emitting all weighted variants
  overlaps them. Variant `x`/`y` rotations use Minecraft's inverted signs (`rot_x`/`rot_y`),
  but an element `rotation` follows the JOML right-hand rule (`rot_axis`). Mixing the two
  folds the tripwire hook below its block.
  `ModelResolver.resolve()` caches by `(original name, normalized state)`, not just the
  normalized state: alias and canonical blocks share geometry, but `ResolvedState.block`
  must stay the one requested so `used_alias` and culling with legacy names are correct.
  The variant seed still comes from the normalized state.
- `mesh.py` is the delicate part:
  - A block whose blockstate or model is absent from the `.l2oassets` (typically a newer
    Minecraft version) is **skipped** instead of aborting the render: `MeshBuilder.build()`
    catches `BlockModelError`, records the name in `Mesh.missing_blocks`/`missing_count` and
    keeps the rest of the schematic. The GUI turns the assets ✓ into a ⚠ that lists the
    omitted blocks and asks to regenerate the pack from a newer client jar.
  - A `cullface` face is emitted unless the neighbour is a full opaque cube. `covers` is
    computed per element; `_is_full_cube` accepts 90°-multiple rotations but rejects
    element `rotation`. Terrain blocks depend on this to cull at all.
  - Equal glass blocks also cull their shared face (`AbstractGlassBlock.skipRendering`), even
    though glass is not opaque. Different stained-glass colours keep it. Without this, a glass
    wall shows an internal grid that vanilla hides.
  - The `cullface` direction is rotated by the **variant** matrix (`rotate_direction`), exactly
    like Minecraft (`Direction.rotate(modelState.transformation(), …)`). Don't skip it:
    stairs/fences/trapdoors with `x`/`y` then cull against the wrong neighbour and show holes.
    Element `rotation` does *not* rotate the cullface, in vanilla or here.
  - Panes and bars have an extra joint rule (`IronBarsBlock.skipRendering`): the end face is
    culled when the neighbour is the **same block** (or another block in `minecraft:bars`) and
    both connection properties point at each other. Without it, every block of a continuous
    pane draws its `*_pane_top` edge strip and the glass looks segmented. The rails' up/down
    faces have no `cullface`, so they keep being emitted (vanilla does the same).
  - A `.litematic` stores connection properties as they were in the world, so a pane/fence
    placed before its neighbour can keep `north/south/east/west=false`. `_recompute_connections`
    normalizes them like a vanilla neighbour update: panes/bars connect to each other, fences
    only within the same family (wooden vs nether brick), and links pointing at air or outside
    the captured region are removed. Links against other blocks are kept as stored.
  - Face UVs come from `ElementFace.uv_or_default`: the model's explicit `uv` rectangle, or
    `default_uv` derived from the element box when absent. Never map 0..1 across the whole
    sprite: textures with several pieces (flower pots, lanterns, fences, plants) then stretch
    the entire PNG onto every face.
  - Normals come from **vertex winding** (`_winding_normal`), not by rotating the face
    table: some rotated/zero-thickness models make the rescale matrix singular.
  - Coincident faces **within one block** are merged by compositing sprites
    (`TextureAtlas.combine`). Do not do this across different blocks: Minecraft draws
    them twice, and Blender dedupes them on import.
  - Fluids use four corner heights per block, like vanilla `FluidRenderer` (`getHeight` +
    `calculateAverageHeight`): the top quad and the side tops slope and adjacent blocks share
    their edge, so flowing water is a continuous ramp, not stairs. Water is tinted
    (`tintindex=0`); an unfed source is `8/9` and each level drops `1/9`. Same-fluid faces are
    culled explicitly in **every** direction (`_fluid_culls`) because fluids are translucent
    and `covers` never culls them; continuity comes from the sloped surface, not internal
    vertical walls. Side faces always use the flowing sprite (`water_flow`/`lava_flow`), as
    vanilla does, with V running from the fluid top to the middle of the sprite: a falling
    column must show vertical streaks, not the horizontal `*_still` texture. The top uses
    the still sprite when the flow is zero and the flowing sprite rotated toward the flow
    direction otherwise (`FlowingFluid.getFlow` + `FluidRenderer`). Fluid templates are
    cached by state, fluid name, the four rounded corner heights, the top/bottom insets and
    the rounded flow direction. Waterlogged blocks (`waterlogged=true`) keep their block
    geometry and add the fluid inside; mark those fluid quads with `QuadTemplate.fluid` so
    only they use `_fluid_culls` and the host slab/stair keeps its own culling rules. Apply
    vanilla's `0.001` insets/drop so the fluid is not coplanar with the host block (Blender
    keeps one of coincident faces). Fluid tint is resolved with the fluid name, not the host
    block.
  - Creatures are opt-in: `mobs` defaults to `False`, and `EntityRenderer.entity()` skips
    only the `_living()` fallthrough. Boats, minecarts, frames, paintings, armor stands,
    items and projectiles are resolved before that point and are unaffected. `entities=False`
    still removes every entity, block entity and cushion.
  - A placed skull/head always has a block entity in vanilla, but some `.litematic` files
    have an empty `TileEntities`. `MeshBuilder` synthesizes it from the blockstate;
    `block/skull` has no elements, so otherwise the fallback cube uses the particle texture.
    `_is_skull_path` must keep excluding `piston_head`. Banners get the same blockstate
    fallback (standing `rotation`, wall `facing`); only the base dye colour is tinted for
    now, patterns are intentionally ignored. Bells also get the fallback: their body lives
    in `entity/bell/bell_body`, not in `block/bell_*`, so without it only the wooden
    support appears. The bell entity model is not blockstate-dependent; the block model
    supplies the support.
  - `TintResolver.tint_for()` only tints blocks with a registered colour: grass, foliage,
    water, dry foliage and the fixed leaf/stem/redstone colours. An unknown `tintindex=0`
    (stonecutter saw, bamboo, lava cauldron...) returns `None`; defaulting it to grass
    painted those textures green.
  - `MeshBuilder.build()` calls `atlas.build()` and only then finalizes state UVs.
    Sprites are registered while building state templates; don't finalize before packing.
- Atlas: power-of-two, shelf-packed, 2 px padding, alpha modes 0/1/2
  (opaque/cutout/translucent). The viewer buffer is pre-sorted into three material groups
  by these modes.
- Geometry conventions: YZX bit packing (`idx = y*(sizeX*sizeZ) + z*sizeX + x`), palette
  index 0 is air, a negative region `Size` only moves `origin` to the min corner (the
  container is always min-corner ordered; never mirror the grid), quads are CCW seen from
  outside.
- Export: `up_axis="z"` maps `(x,y,z) -> (x,z,-y)`; positions/UVs/normals are rounded to
  6 decimals **before** dedupe. OBJ uses separate v/vt/vn indices.
- `uvlock` is implemented like vanilla: rotate the **absolute** model UVs (0..16) around
  texture pixel `(8,8)` with the x/y angle and direction-cycle rules, then divide to sprite
  coordinates. Face `rotation` is applied first (both are rotations around UV space, but their
  centers differ). Without it, `half=top` stairs (`x:180`) show the lower-half texture upside
  down. Keep this behavior if you touch UV baking and update README if it changes.

## GUI (`gui/`)

- Native desktop app: `gui/app.py` (window + `QThreadPool` tasks), `gui/viewport.py`
  (`QOpenGLWidget`), `gui/camera.py` (orbit camera + matrices).
- The GUI does not list schematics, packs or Minecraft installs: schematics come from the
  file dialog or drag-and-drop, resource packs are added explicitly with the file/folder
  dialog (multi-select for `.zip`, a folder can expand to all packs inside it; persisted in
  `QSettings`, no disk scan) and assets come from the `.l2oassets`. `_asset_status()`
  drives the ✓/✗/⚠ panel and the manifest's `minecraft_version`; the ⚠ appears when the
  last mesh has `missing_blocks`. `_ensure_assets()` shows the instructions alert and offers
  to generate the pack from a client jar chosen by the user.
- File association lives in `associations.py` and is only written when the user chooses
  **Archivo → Asociar archivos .litematic…**. Linux writes `lite2obj.desktop`, a MIME XML
  (`application/x-litematic`) and the `mimeapps.list` default under the user's data/config
  dirs; Windows writes `HKCU\Software\Classes` (no admin). The command is the `APPIMAGE` path
  when running from an AppImage, so moving the file requires re-associating. Keep
  `gui/__main__.py` so the development fallback `python -m lite2obj.gui` works.
- `packaging/appimage/lite2obj.desktop` must keep `Exec=lite2obj %f` and
  `MimeType=application/x-litematic;`, and `build_appimage.sh` must copy
  `packaging/appimage/lite2obj-mime.xml` into `usr/share/mime/packages/`. An AppImage does
  not register itself: the app action installs the association in the user profile.
- Use **Qt's own OpenGL classes** (`QOpenGLShaderProgram`, `QOpenGLBuffer`,
  `QOpenGLVertexArrayObject`, `QOpenGLTexture`, `QOpenGLFunctions`). Do not create an
  external GL context (moderngl/glcontext): it makes its own framebuffer and drawing is
  never presented.
- `viewport.triangle_soup()` builds a triangle soup already ordered by alpha mode; each group
  draws with `glDrawArrays`. Prefer `glDrawArrays` over
  `glDrawElements(..., offset)`: PySide6's indexed-offset path produced black frames.
- The mesh is drawn with `GL_CULL_FACE`/`GL_BACK` because quads are CCW outward and Minecraft
  culls backfaces. Without it, transparent blocks show their back faces through the front.
  Don't apply it to the line/grid passes.
- Release GL resources only with the context current: `Viewport.shutdown()` is called from
  `MainWindow.closeEvent` and from `QOpenGLContext.aboutToBeDestroyed`. Destroying a
  `QOpenGLTexture` without a current context warns and leaks.
- On Linux with NVIDIA + Wayland, `run()` forces `QT_QPA_PLATFORM=xcb` (EGL context creation
  fails otherwise). Don't remove it; the user can override the variable.
- Screenshot in tests/automation under X11: `QT_QPA_PLATFORM=xcb uv run python ...` and read
  back with `viewport.grabFramebuffer()`.

## Packaging, CI and docs

- Local portable builds include the `.l2oassets` prepared by
  `packaging/prepare_assets.py`; GitHub release builds must **not** include it. The supported
  switch is `LITE2OBJ_SKIP_ASSETS=1`: `prepare_assets.py` does nothing, the spec omits the
  pack even if `build/assets/lite2obj-assets.l2oassets` exists, and both packaging scripts
  print that they are building without assets. `--skip`/`--optional` are the CLI equivalents;
  do not remove the env check from `lite2obj.spec`.
- `.github/workflows/ci.yml` runs `ruff format --check`, `ruff check`, `mypy src` and
  `pytest` on Ubuntu and Windows. On push it also builds both portables without assets and
  fails if any `.l2oassets` ends up under `dist/`.
- `.github/workflows/release.yml` runs on tags `v*` (or a manual tag), requires the tag to
  equal `v` + `__version__`, builds the AppImage and `.exe` without assets and publishes
  them plus `SHA256SUMS`. Do not upload the asset pack or textures.
- `.github/workflows/pages.yml` publishes `docs/` with GitHub Pages. The landing is static
  (no build step) and bilingual: `docs/index.html` is Spanish, `docs/en/index.html` is
  English, and both share `styles.css`/`main.js`. Each page has
  `data-repo="TU-USUARIO/lite2obj"` as the single placeholder for GitHub/Releases/Issues
  links. Update both before enabling Pages.

## Blender validation

- Blender 5.2 uses engine `BLENDER_EEVEE`; `BLENDER_EEVEE_NEXT` no longer exists.
- Blender welds by position and keeps the **first** of coincident faces. That is why
  same-block overlays are composited; a lower imported face count is not an exporter bug.
- Use `--z-up` when the importer's axis conversion is disabled.
