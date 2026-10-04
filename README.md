# Anymaker Helper

English | [简体中文](README.zh-CN.md)

A Blender add-on for building vehicles for [Anymaker].

Requires Blender 4.5 LTS.

## Features

- **In-game preview:** Rebuilds the mesh from cubes, edge bridges and plate
  caps, so you can see the in-game result before you export. A Mirror
  modifier is carried over, and the preview can refresh itself every time you
  leave edit mode.
- **Double-sided faces:** Mark all edges of a face as seams to make it
  double-sided. The preview renders a flipped copy of its cap, and export
  writes a secondary plate, so it shows from both sides in game.
- **Save file export:** Writes the `.meta` and `.data` JSON the game reads, plus
  in-scene labels naming every node and plate it exported.
- **Grid-based modeling:** Sets the viewport grid to the game's unit, so the
  mesh can be modeled on the same grid the game uses.
- **Automatic cleanup:** Snaps vertices onto their grid points and merges any
  that share one, so no two nodes export to the same position. Preview and
  export run the same cleanup first.

See [Usage](#usage) for more.

## Installation

Download the ZIP from GitHub, then in Blender:

    Edit > Preferences > Add-ons > Install from Disk

Enable **Anymaker Helper**. The panel appears in the 3D viewport sidebar
(<kbd>N</kbd>) under the **Anymaker Helper** tab, in object mode.

## Usage

Every button that touches geometry works on the object in the panel's **Game
Object** field, not on whatever happens to be selected. Set that field first.
The three geometry buttons stay greyed out until it holds a mesh.

| Button | What it does |
| --- | --- |
| Apply Grid Gap | Sets the viewport grid spacing to the grid unit (8 cm by default) |
| Snap To Grid Points | Moves every vertex onto its grid point and welds vertices that share one |
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

Both `[Generate Preview Mesh]` and `[Export Save File]` snap and merge grid
points first, so a mesh that was never cleaned up still exports correctly.
Both edit the mesh data, and both are undoable with <kbd>Ctrl</kbd>+<kbd>Z</kbd>.
Undo cannot remove the files an export already wrote.

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