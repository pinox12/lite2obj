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
```

There is no CI or pre-commit: run ruff, mypy and pytest yourself before committing.
Python is 3.14; dependencies live in `uv.lock` (no pip/poetry). There is no JS/Node step.

## Conventions

- Docs, docstrings, comments, CLI/UI strings and commit messages are **Spanish**.
  Code identifiers are English. Match this.
- Ruff: line length 100, target `py314`, rules `E,F,I,UP,B,SIM`.
- `src/` layout; package `lite2obj`; CLI entry point `lite2obj.cli:main`; GUI script
  `lite2obj-gui = "lite2obj.gui:main"`.

## Tests: a green run may have tested nothing

- Real-asset tests are **jar-gated**: they call `tests/_jar.py::jar_resources()`, which
  `pytest.skip`s when no Minecraft client jar is found. A green suite on a fresh machine
  can mean the model/texture/mesh pipeline was never exercised.
- `tests/conftest.py` puts `tests/` on `sys.path`, so helpers are imported top-level:
  `from _jar import jar_resources`, `from nbt_writer import write_nbt` (not `tests._jar`).
- Committed fixture: `tests/fixtures/store_1x4x4.litematic`.
  `tests/test_export.py::test_real_fixture_golden_counts` hard-codes mesh/OBJ counts for
  vanilla 26.3 — update it deliberately when geometry or culling changes.
- `test_schematics_integration.py` / `test_coverage_integration.py` scan the machine's
  schematic folders and skip if none exist. `LITE2OBJ_SCHEMATICS` **adds** directories
  (colon-separated); it does not replace discovery.

## Architecture and invariants

Pipeline order, each layer assuming the previous:
`paths` → `assets` → `models` → `textures` → `mesh` → `export`/`pipeline` → `cli`/`gui`.

- `assets.py` stacks the vanilla jar plus resource packs, falling back **per asset** to
  vanilla. `LITE2OBJ_*` variables override auto-discovery (see the `paths.py` docstring).
- `models.py` resolves blockstates (variants/multipart), parent inheritance and model/
  element rotations. Variants are chosen **per position** (`position_seed`), not once per
  state. Each multipart group contributes **one** variant; emitting all weighted variants
  overlaps them.
- `mesh.py` is the delicate part:
  - A `cullface` face is emitted unless the neighbour is a full opaque cube. `covers` is
    computed per element; `_is_full_cube` accepts 90°-multiple rotations but rejects
    element `rotation`. Terrain blocks depend on this to cull at all.
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
  - Fluids are fallback cubes: water is tinted (`tintindex=0`), the surface sits at
    `8/9` of the block (full height when the same fluid is directly above), and same-fluid
    faces are culled explicitly (`_fluid_culls`) because fluids are translucent and `covers`
    never culls them. `fluid_above` is part of the `state_mesh` cache key. The fluid inside
    waterlogged blocks is not rendered.
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
- Known fidelity concession: `uvlock` is not implemented (inner/outer stair corners use
  model UVs). Don't silently "fix" it; document any change in README.

## GUI (`gui/`)

- Native desktop app: `gui/app.py` (window + `QThreadPool` tasks), `gui/viewport.py`
  (`QOpenGLWidget`), `gui/camera.py` (orbit camera + matrices).
- Use **Qt's own OpenGL classes** (`QOpenGLShaderProgram`, `QOpenGLBuffer`,
  `QOpenGLVertexArrayObject`, `QOpenGLTexture`, `QOpenGLFunctions`). Do not create an
  external GL context (moderngl/glcontext): it makes its own framebuffer and drawing is
  never presented.
- `viewport.triangle_soup()` builds a triangle soup already ordered by alpha mode; each group
  draws with `glDrawArrays`. Prefer `glDrawArrays` over
  `glDrawElements(..., offset)`: PySide6's indexed-offset path produced black frames.
- Release GL resources only with the context current: `Viewport.shutdown()` is called from
  `MainWindow.closeEvent` and from `QOpenGLContext.aboutToBeDestroyed`. Destroying a
  `QOpenGLTexture` without a current context warns and leaks.
- On Linux with NVIDIA + Wayland, `run()` forces `QT_QPA_PLATFORM=xcb` (EGL context creation
  fails otherwise). Don't remove it; the user can override the variable.
- Screenshot in tests/automation under X11: `QT_QPA_PLATFORM=xcb uv run python ...` and read
  back with `viewport.grabFramebuffer()`.

## Blender validation

- Blender 5.2 uses engine `BLENDER_EEVEE`; `BLENDER_EEVEE_NEXT` no longer exists.
- Blender welds by position and keeps the **first** of coincident faces. That is why
  same-block overlays are composited; a lower imported face count is not an exporter bug.
- Use `--z-up` when the importer's axis conversion is disabled.
