# Anymaker Helper

A Blender add-on for building vehicles for [Anymaker].

Requires Blender 3.0 or newer.

## Features

- **In-game preview:** Rebuilds the mesh from cubes, edge bridges and plate
  caps, so you can see the in-game result before you export. A Mirror
  modifier is carried over, and the preview can refresh itself every time you
  leave edit mode.
- **Save file export:** Writes the `.meta` and `.data` JSON the game reads, plus
  in-scene labels naming every node and plate it exported.
- **Grid-based modeling:** Sets the viewport grid to the game's unit, so the
  mesh can be modeled on the same grid the game uses.
- **Automatic cleanup:** Snaps vertices onto their grip points and merges any
  that share one, so no two nodes export to the same position. Preview and
  export run the same cleanup first.

See [Usage](#usage) for more.

## Installation

Download the ZIP from GitHub, then in Blender:

    Edit > Preferences > Add-ons > Install

Enable **Anymaker Helper**. The panel appears in the 3D viewport sidebar
(<kbd>N</kbd>) under the **Anymaker Helper** tab, in object mode.

## Usage

Every button that touches geometry works on the object in the panel's **Game
Object** field, not on whatever happens to be selected. Set that field first.
The three geometry buttons stay greyed out until it holds a mesh, and a
generated preview can never be used as the game object.

| Button | What it does |
| --- | --- |
| Apply Grid Gap | Sets the viewport grid spacing to the grid unit (8 cm by default) |
| Snap To Grip Points | Moves every vertex onto its grip point and welds vertices that share one |
| Generate Preview Mesh | Creates or replaces the in-game preview mesh: `{name}_AM_preview` |
| Export Save File | Writes `<name>.meta` and `<name>.data` |

### Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Grid Unit Size | 0.08 m | The game's base grid unit: sizes the vertex cubes, the grid gap, the snap threshold and the planarity tolerance |
| Include Degenerate Faces | off | Keep zero-area faces (see below) |
| Preview Opacity | 1.0 | Transparency of the preview mesh material |
| Refresh Preview On Edit Mode | off | Hide the preview in edit mode, rebuild it on exit |

It is recommended to keep the default `[Grid Unit Size]` setting so it aligns
with the game.

**The general workflow:**

1. Click `[Apply Grid Gap]` to adjust the grid settings.
2. Set **Game Object** to the mesh you are modeling.
3. Model the mesh. **Remember to enable `Snap (Grid)` to keep vertices on grid.**
4. Click `[Generate Preview Mesh]` to see the in-game result.
5. Once you are happy with the preview, click `[Export Save File]`.
6. Move the exported files to `C:\Users\{yourUserName}\AppData\Roaming\Anymaker\creations\`(default Anymaker save dir) to test them in game.

Both `[Generate Preview Mesh]` and `[Export Save File]` snap and merge grip
points first, so a mesh that was never cleaned up still exports correctly.
Both edit the mesh data, and both are undoable with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
Undo cannot remove the files an export already wrote.

## Apply Grid Gap

Sets the grid spacing of every 3D viewport in every screen to the grid unit,
shows 10 grid lines and turns the floor on. It is a viewport display setting
only: nothing about the mesh changes.

## Snap To Grip Points

A **grip point** is the grid cell corner a vertex lands on during export: the
same rounding the node position computation uses, turned back into a world
position. The button does two things to the game object:

1. **Snap.** Every vertex more than a thousandth of a grid unit away from its
   grip point is moved onto it, so the mesh matches the grid the game reads.
2. **Merge.** Vertices that share a grip point are welded into one, so no node
   is exported twice at the same position. Faces and edges that collapse in the
   weld are removed.

Positions are compared in world space, so rotating or scaling the object does
not change which grip point a vertex belongs to. The status bar reports how
many vertices were snapped and merged, and the console logs the vertices, edges
and faces the weld removed. The snap is written into the object's mesh data, so
every object sharing that mesh data is snapped too.

Vertices that share a grip point can only be merged if the mesh allows it. A
quad whose opposite corners fall in the same cell collapses into a triangle,
and a face whose corners all fall in one cell disappears, so keep an eye on the
face count reported after a merge.

The snap is written into the object's mesh data, so every object sharing that
mesh is snapped too, and a rotated or scaled object gets its vertices moved in
local space rather than being rescaled.

## Generate Preview Mesh

Anymaker models are built from cubes, edges and plates rather than points,
lines and faces, so the preview reproduces all three. The preview object has an
identity transform and holds world-space geometry, and it is linked into the
same collections as the source object.

Generating removes the previous preview first. Previews are matched by the
marker they carry rather than by name, so an older preview that picked up a
`.001` suffix is cleaned up too and the new one gets the plain
`{name}_AM_preview` name back. A material named `{name}_AM_preview_mat` is
created if it does not exist yet, reused if it does, and its alpha follows
**Preview Opacity**.

**Vertices.** Every original vertex becomes a cube of one grid unit, centred on
the vertex. Cubes are always axis-aligned in world space, so rotating or
scaling the source object does not rotate the cubes.

**Edges.** Every original edge is filled in by connecting the 12 edges of cube
A to the matching 12 of cube B, giving 12 quads per edge. Four of those are
degenerate: when the two vertices are axis-aligned, the four cube edges
running parallel to the connection collapse to zero-area quads.
**Include Degenerate Faces** is off by default, which skips them and leaves 8
per axis-aligned edge; turn it on to keep all 12. Bridge faces are wound
outward, away from the axis between the two vertices.

**Plates.** Every original face that is planar gets a cap joining the farthest
corner of each of its cubes along the face normal. The cap keeps the original
face's edge count and vertex order, so plate orientation is preserved. Faces
that are not planar within tolerance are skipped and listed in the console,
and the status bar reports a warning. The tolerance is 5% of **Grid Unit
Size**, so a lower grid unit means a stricter test.

Blender's own mesh validation runs on the result and may drop faces it
considers invalid, including deliberate zero-area bridge quads, so the console
also logs how many faces the finished preview actually kept.

**Mirror.** Every Mirror modifier on the source is copied onto the preview, so
the mirrored half shows up in the preview too. A mirror without its own mirror
object is pointed at the source object, which keeps the mirror plane where the
source has it even though the preview works in world space. Other modifier
types are not copied.

### Refresh Preview On Edit Mode

Turn on **Refresh Preview On Edit Mode** to keep the preview in step with
edits. The preview mesh then hides as soon as the game object enters edit mode,
so it does not sit in the way of the vertices being moved, and is rebuilt —
snapped and merged first, exactly like the button — when the object leaves edit
mode. The rebuild is deferred until the edit finishes, and the console logs
each rebuild and any face it had to skip.

The *Game Object* set in the panel is the one watched, not whatever else is
selected: entering and leaving edit mode on any other object changes nothing.
Turning the option off while a preview is hidden brings that preview back.

## Export Save File

Opens a dialog for the vehicle ID (default: 99), the vehicle name (default: the
game object's name) and the output directory (default: the folder of the
current `.blend` file). It writes two files:

- `{name}.meta` — the vehicle ID, an identity transform, and bounds
- `{name}.data` — nodes, edges and plates, the game's empty link groups, and
  the buoyancy fill value

Both are compact single-line JSON. Missing output folders are created. If the
vehicle name or output directory is left empty when the dialog opens, it falls
back to the game object, then to the first selected non-preview mesh, then to
the current `.blend` directory.

Node, edge and plate ids are 1-based and follow the mesh order, so they match
the `node-{id}` and `plate-{id}` labels. The `.meta` bounds are written as zero
boxes.

### Node positions

Positions are converted from Blender to game axes and then to grid cells.
Blender is Z-up and the game is Y-up, so Blender's Z becomes the game's Y and
Blender's Y becomes the game's Z. Grid conversion rounds to the nearest cell,
so a vertex just inside or just outside a cell boundary lands in the same cell.

Because the snap runs first, the exported mesh has one vertex per grip point,
so no two nodes share a position.

There is no ground clearance offset applied on export, so the whole mesh must
stay above 0 on Blender's Z axis to keep the creation above the ground in game.

That axis swap mirrors the model, which flips every face normal, so each
plate's node order is reversed on export to keep the normals pointing outward.

### Labels

After exporting, an empty named `node-{id}` or `plate-{id}` is created for
every node and plate, and its name is shown in the viewport. Node labels sit on
the node; plate labels sit at the centre of their nodes. All of them live in
one collection named `{name}_AM_labels`, which is deleted and rebuilt on every
export, so repeated exports do not accumulate duplicates. The collection is
linked to the scene collection.

Labels need the viewport **Extras** overlay enabled to be visible.

## Notes and limitations

- This add-on is written and documented mostly by AI with manual review, so bugs are likely. Pull requests
  with fixes are very welcome.
- Keep the mesh above 0 on Blender's Z axis. Export does not lift the model,
  so anything below 0 ends up below the ground in game.
- Keep snapping to grid enabled while modeling. The snap and merge run on
  whatever the mesh happens to be, and vertices that were meant to be separate
  but land in the same cell are welded away.
- Only one object is exported at a time. Multi-object vehicles are not
  supported yet, and modifiers other than Mirror are ignored by the preview.
- The planarity test measures deviation from the centroid plane rather than a
  best-fit plane, so a badly warped face can pass. Try to avoid such
  circumstances.
- The add-on has no undo step for the files it writes, and re-exporting the
  same name overwrites the previous `.meta` and `.data`.

## License

Released under the GNU General Public License v3.0 or later. See
[LICENSE](LICENSE) for the full text.

Contributions are accepted under the same terms.