# Creator hub: the contract with content modules

The editor ("Create" in the P8Plus launcher) is the hub for levels, people, quests, story/cutscenes and script mods.
This page is the contract between the editor and the modules that provide content areas (goals, people, story,
script mods, the live link).

## Toolkit

One app: the launcher, built on **imgui-bundle 1.92.900** (hello_imgui docking, ImGuizmo, imgui-node-editor,
ImGuiColorTextEdit, implot) with **moderngl 5.12.0** for 3D viewports. The theme, fonts, icons and widgets come from the
`p8ui` design system; do not hand-roll styles or colors. Panels are Python modules drawn every frame.

## 1. The document is a modkit mod

An editor project is a modkit mod folder (`modkit.json` + `assets/`, [packaging.md](../modding/packaging.md)). The
editor reads and writes `modkit.json` directly; there is no second scene file. Everything a creator makes is one or
more ops in `ops`, so a mod made in the editor and a mod written by hand are the same thing, and the editor opens both.

Editor bookkeeping lives in `x_editor` keys, which the build strips before an op runs:

- top level: `"x_editor": {"format": 1, "zone": "z_houses", "camera": {...}, "test": {"start": {"restart": "<node>"}
  | {"pos": [x, y, z], "yaw": deg}}, "folders": [...]}`
- per op: `"x_editor": {"uid": "e7f3a1", "entity": "e7f3a1", "kind": "prim.quarterpipe", "params": {...},
  "label": "QP north", "folder": "Bowl", "hidden": false, "locked": false}`
  - `entity`: ops generated from one editor object share it (a quarter pipe = `static.add_object` +
    `collision.add_mesh` + `nodes.add_rail`); the editor regenerates them together from `params`.
  - Ops without `x_editor` are "raw ops": listed, editable as JSON, kept as they are.
- Save order: by phase (texture 10, static 20, collision 30, nodes 40, peds 50, goals 60, story 70, script 80), stable
  inside a phase. An ops module may declare `PHASE = <int>`.

Generated assets go to `assets/gen/<uid>.*` (meshes from parametric pieces) and are rewritten on save.

## 2. Op areas

| area | module (tools/modkit/) | reference |
|---|---|---|
| `static`, `texture`, `skin`, `collision`, `nodes` | ops_static, ops_texture, ops_skin, ops_collision, ops_nodes | [operations.md](../modding/operations.md) |
| `goals` | ops_goals.py | [goals.md](../modding/goals.md) |
| `peds` | ops_peds.py | [peds.md](../modding/peds.md) |
| `story` | ops_story.py | [story.md](../modding/story.md) |
| `script` | ops_script.py (Lua files in the mod, events/hooks) | [scripting.md](../modding/scripting.md) |

Same rules as the existing ops: `OPS = {"goals.add": fn(op, ctx)}`, every user error through `ctx.fail`, budgets through
`ctx.budget`, paths through `ctx.asset`, no game data in a mod. World space: Y up, right-handed, meters; yaw in degrees
about +Y, local +Z becomes (sin yaw, 0, cos yaw). Names you create are prefixed by the creator (`<prefix>_...`).

## 3. What the editor reads from an ops module (optional attributes)

### `SCHEMA`: property panels and gizmos

```python
SCHEMA = {"goals.add": {
    "label": "Goal", "category": "Quests", "icon": "flag", "doc": "One line for the tooltip",
    "fields": [
        {"key": "zone", "type": "zone"},
        {"key": "name", "type": "id", "required": True},
        {"key": "pos", "type": "vec3", "role": "position"},
        {"key": "yaw", "type": "float", "unit": "deg", "role": "yaw", "default": 0},
        {"key": "path", "type": "vec3[]", "role": "path", "closed_key": "loop"},
        {"key": "title", "type": "str", "max": 40},
        {"key": "kind", "type": "enum", "values": ["score", "combo", "collect"]},
        {"key": "time", "type": "float", "min": 10, "max": 600, "default": 120, "unit": "s"},
        {"key": "giver", "type": "ref", "ref": "peds.add"},
        {"key": "script", "type": "asset", "ext": [".lua"]},
        {"key": "steps", "type": "list", "item": {"type": "object", "fields": [...]}},
    ],
    "markers": [{"at": "pos", "shape": "goal"}],
}}
```

- Types: `bool int float str text id enum vec3 vec3[] color zone node ref asset list object`. `node` takes
  `"class": "Restart"` etc. and offers the zone's nodes; `ref` offers the document's entities made by that op.
- Roles drive the viewport: `position` (translate gizmo, click-to-place), `yaw`, `rotation` (vec3 degrees), `scale`,
  `path` (editable polyline), `points` (separate points, each drawn with the field's `marker`), `radius` (sphere),
  `box` (center + size), `camera` (pos + yaw/pitch/roll + fov: a camera frustum, "snap to view").
  Fields inside `object` fields and inside `list` items carry their own roles; the gizmo path is dotted
  (`start.pos`, `camera.3.pos`), so keyframes and actor marks get gizmos from the generic code.
- Viewport camera access: `ed.viewport.get_camera()` -> `{pos, yaw, pitch, roll, fov}`, `ed.viewport.set_camera(...)`.
- `markers` shapes: `goal`, `ped`, `restart`, `camera`, `trigger`, `pickup`, `gameobject`, `generic`.

The editor builds a generic property panel and gizmos from this. Most areas need nothing else.

### `QUERIES`: read-only data from the player's game

```python
QUERIES = {"goals.stock": fn(args: dict, ctx) -> json-serialisable}
```

`ctx` is a read-only BuildCtx over the files the overlay would serve, plus `ctx.cache_dir` (a per-user folder for
caches). The editor uses queries to show stock content: a level's goals with positions, ped models and animations, ped
paths, dialogue lines, cutscenes, the script API reference. Keep them under about 1 s, or cache.

### `PHASE`: see section 1.

## 4. Custom panels

When the generic panel is not enough (the story graph, a dialogue tree, a goal flow, the Lua editor), an area provides
`tools/editor/panels/<area>.py`:

```python
class Panel:
    title = "Story"; icon = "book"; dock = "right"        # left | right | bottom | center
    def __init__(self, ed): ...                           # ed: EditorAPI, below
    def gui(self): ...                                    # draw the window body (imgui calls); every frame when visible
    def viewport_overlay(self, vp): ...                   # optional: vp.line(a, b, color), vp.polyline(pts), vp.marker(pos, shape), vp.label(pos, text)
```

EditorAPI (`ed`):
- `ed.doc`: the mod document (`ops`, `add_op`, `update_op`, `remove_op`, `find(uid)`; every change goes through undo).
- `with ed.undo("Move goal"):` groups changes.
- `ed.selection`, `ed.select(uid)`, `ed.zone` (the loaded zone's nodes, objects, bounds).
- `ed.query(name, args)`: returns a future; `ed.run_task(fn, label)` runs background work with progress.
- `ed.viewport.pick_point(callback)`: the next click on a surface gives a world position. `ed.viewport.focus(pos)`.
- `ed.livelink`: the live game connection, or None.
- `ed.ui`: p8ui.

Panels are discovered by module name and added to the Create workspace.

## 5. Build & Test, livelink

- Build: `modkit build` of the project into the editor's own test mods root, pack `zz_editor`, on top of the player's
  enabled packs. Budgets from the build report are shown live, and a failing budget blocks the save of a build.
- Test: launch P8Plus with `PS3_MODS_ROOT=<test root>`, `PS3_LIVELINK=1`, `PS3_LIVELINK_FILE=<run dir>\livelink.json`,
  then teleport to the editor camera or a chosen restart. With a running game, Build & Test hot-reloads instead of
  restarting (livelink `reload`).

## 6. One park format for the desktop editor and the in-game editors (Create-a-Park, Create-a-Skater)

The in-game Create-a-Park (CAP) and the deeper Create-a-Skater (CAS) of the P8Plus mods layer use the same document as
the desktop editor, so a park made in either one opens in the other.

- The format is a modkit mod's `modkit.json` (section 1). A park piece is one entity: its primary op carries
  `x_editor = {uid, entity, primary: true, kind, name, label, zone, params, xf: {pos: [x, y, z], yaw, scale}}`, and the
  ops after it are regenerated from that description. An editor only writes the description and regenerates. It never
  hand-edits generated ops or `assets/gen/*` (both are rebuilt on every save, and a save of an unchanged document
  changes nothing).
- The kinds a park uses: `prim.<kind>` (parametric pieces), `rail.path` (drawn rail, `params.points` local to
  `xf.pos`), `restart.add`, `node.set`, `stock.hide`, `level.blank` (park from scratch), `prop.gameobject`,
  `prop.model` (a game model by name), `import.mesh` (the creator's glTF / OBJ), `texture.edit`.
- Parametric pieces: `tools/editor/prims.py`. `KINDS[kind]['params']` lists every parameter with type, default,
  min / max and unit; `normalize(kind, params)` fills defaults and clamps; `build(kind, params) -> Piece` with
  `.parts` (per material: pos, nrm, uv, tris), `.collision` and `.rails` (copings and edges, with terrain). Kinds:
  quarterpipe, kicker, funbox, rail, ledge, bank, pool, stairs, platform (box, up to 100 m). Prefabs:
  `tools/editor/prefabs.py` (`PREFABS`, `place(ed, key, pos, yaw)`). The pieces are pure Python + numpy: a runtime
  that cannot run Python reads the same `params` and must give the same geometry (a port of `build`, checked against
  `tools/editor/tests/test_prims.py` output).
- What a piece becomes (`entities.PrimKind.ops`): `static.add_object` (visual glb, `lightmap` `surface`, or `ground`
  over a blanked level), `collision.add_mesh` (the same piece as solid world triangles, vert faces flagged), and one
  `rails.add` per coping / edge (`cluster` = the piece's name). The build is `tools/modkit/modkit.py build`.
- Conventions: Y up, right-handed, meters; yaw in degrees about +Y, local +Z -> (sin yaw, 0, cos yaw); vertex color
  1.0 = lit like the level; UVs stay within +-16 (half floats); at most 32,000 vertices per part.
- Limits enforced by the build (refuse, not warn): the zone video-memory link slots (8 MiB of free VRAM kept), the
  rail node heap (`RailNodeHeap`), the collision and node structural limits ([limits.md](../modding/limits.md)).
- Live game: livelink `reload` `what=zone` for geometry and collision (about 3-4 s), `what=level` when rails, goals or
  scripts changed (they only reload with the level); `node.move` moves a restart or game object at once. Protocol:
  `native/livelink/PROTOCOL.md`.
- CAS: skins and parts go through `skin.*` ops and `tools/models/import_skin.py` (glTF in, the game's skinned format
  out); texture work through `texture.replace` / `texture.recolor` / `texture.overlay` and the editor's `texture.edit`
  entity (preview = the build's own pixel code, `ops_texture.recolor_pixels` / `overlay_pixels`). Nothing from the
  game goes into a mod; game models and textures are referenced by name and read from the player's files at build.
- A new piece kind or parameter is added in `prims.py` (with its test) first; both editors then pick it up from
  `KINDS`. Changes stay backward compatible (new parameters get defaults, old documents keep opening); an
  incompatible change bumps the document's `x_editor.format` (`doc.DOC_FORMAT`) and keeps reading the old one.
- Top-level `x_editor` keys the editor owns: `format`, `zone`, `camera`. Another editor keeps its own settings
  in its own top-level `x_<name>` key (e.g. `x_cap`), and per-piece data in a sub-dict of the piece's
  `x_editor` (e.g. `x_editor.cap`): edits and saves keep it; a duplicate starts without it (its old grid cell
  would be stale), so treat a missing entry as "off the grid, at its exact `xf`".
