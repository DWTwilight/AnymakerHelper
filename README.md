# Anymaker Helper

A Blender add-on for building vehicles for [Anymaker].

The add-on does two things:

- **Generate Preview Mesh** builds an in-Blender approximation of how the model
  will look in game, so you can check your work before exporting.
- **Export Save File** writes the `.meta` and `.data` files the game reads.

## Installation

Download the ZIP from GitHub, then in Blender:

    Edit > Preferences > Add-ons > Install

Enable **Anymaker Helper**. The panel appears in the 3D viewport sidebar
(<kbd>N</kbd>) under the **Anymaker Helper** tab.

## Usage

| Button | What it does |
| --- | --- |
| Apply Grid Gap | Sets the viewport grid spacing to the grid unit (8 cm by default) |
| Generate Preview Mesh | Creates or replaces the in-game preview mesh: `{name}_AM_preview` |
| Export Save File | Writes `<name>.meta` and `<name>.data` |

### Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Grid Unit Size | 0.08 m | The game's base grid unit |
| Include Degenerate Faces | on | Keep zero-area faces (see below) |
| Preview Opacity | 1.0 | Transparency of the preview mesh |

It is recommended to keep the default `[Grid Unit Size]` settings so it aligns with the game.

**The general workflow:**

1. Click `[Apply Grid Gap]` to adjust the grid settings.
2. Create and model the mesh. **Remember to enable `Snap (Grid)` to keep vertices on grid.**
3. Select the mesh and click `[Generate Preview Mesh]` to see the in-game result.
4. Once you are happy with the preview, click `[Export Save File]`.
5. Move the exported files to `C:\Users\{yourUserName}\AppData\Roaming\Anymaker\creations\` to test them in game.

## Generate Preview Mesh

Anymaker models are built from cubes, edges and plates rather than points,
lines and faces, so the preview reproduces all three.

**Vertices.** Every original vertex becomes a cube of one grid unit, centred on
the vertex. Cubes are always axis-aligned in world space, so rotating or
scaling the source object does not rotate the cubes.

**Edges.** Every original edge is filled in by connecting the 12 edges of cube
A to the matching 12 of cube B, giving 12 quads per edge. Four of those are
degenerate: when the two vertices are axis-aligned, the four cube edges
running parallel to the connection collapse to zero-area quads. Turn off
**Include Degenerate Faces** to skip them, leaving 8 per axis-aligned edge.

**Plates.** Every original face that is planar gets a cap joining the farthest
corner of each of its cubes along the face normal. The cap keeps the original
face's edge count and vertex order, so plate orientation is preserved. Faces
that are not planar within tolerance are skipped and listed in the console,
and the status bar reports a warning.

## Export Save File

Opens a dialog for the vehicle ID, the vehicle name (default: the selected
object's name) and the output directory (default: the folder of the current
`.blend` file). It writes two files:

- `{name}.meta` — the vehicle ID, an identity transform, and bounds
- `{name}.data` — nodes, edges and plates plus the game's empty link groups

Both are compact single-line JSON.

### Node positions

Positions are converted from Blender to game axes and then to grid cells.
Blender is Z-up and the game is Y-up, so the conversion swaps the up axis.
Grid conversion rounds to the nearest cell, so a vertex just inside or just
outside a cell boundary lands in the same cell.

There is no ground clearance offset applied on export, so the whole mesh must
stay above 0 on Blender's Z axis to keep the creation above the ground in game.

That axis swap mirrors the model, which flips every face normal, so each
plate's node order is reversed on export to keep the normals pointing outward.

### Labels

After exporting, an empty named `node-{id}` or `plate-{id}` is created for
every node and plate, and its name is shown in the viewport. All of them live
in one collection named `{name}_AM_labels`, which is deleted and rebuilt on
every export, so repeated exports do not accumulate duplicates.

Labels need the viewport **Extras** overlay enabled to be visible.

## Notes and limitations

- Keep the mesh above 0 on Blender's Z axis. Export does not lift the model,
  so anything below 0 ends up below the ground in game.
- Only one object is exported at a time. Multi-object vehicles are not
  supported yet.
- The planarity test measures deviation from the centroid plane rather than a
  best-fit plane, so a badly warped face can pass. Try to avoid such circumstances.
