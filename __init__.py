# Anymaker Helper - Blender add-on for building Anymaker vehicles.
# Copyright (C) 2026 DWTwilight
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. See LICENSE for the full text.
#
# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "Anymaker Helper",
    "author": "DWTwilight",
    "version": (0, 5, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Anymaker Helper",
    "description": "Convert Blender objects to Anymaker preview meshes and export game save files",
    "category": "Object",
}

import os
import bpy # type: ignore
from bpy.app.handlers import persistent # type: ignore
from bpy.props import ( # type: ignore
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup # type: ignore
from . import utils


_EDIT_STATE = {"primed": False, "signature": None}
_HIDDEN_PREVIEW = {"name": None}


def _current_target(scene):
    """Return the object or collection the panel currently targets."""
    if scene.anymaker_target_type == 'COLLECTION':
        return scene.anymaker_target_collection
    return scene.anymaker_target


def _target_ready(scene):
    """Whether the current target can be converted or exported."""
    target = _current_target(scene)
    if target is None:
        return False
    if isinstance(target, bpy.types.Collection):
        return bool(utils.iter_mesh_objects(target))
    return target.type == 'MESH' and not utils.is_preview_object(target)


def _editing_names(target):
    """Names of the target's mesh objects currently in edit mode."""
    return tuple(sorted(
        obj.name for obj in utils.iter_mesh_objects(target)
        if obj.mode == 'EDIT'
    ))


def _target_signature(target):
    """Snapshot that changes whenever the target or its edit state changes."""
    if target is None:
        return (None, ())
    return (target.name, _editing_names(target))


def _target_changed(self, context):
    _EDIT_STATE.update(primed=False, signature=None)


def snap_note(moved, merged):
    """Suffix for a status report naming the forced grid point snap."""
    if not moved and not merged:
        return ""
    return f", {moved} vertices snapped, {merged} merged"


def _hide_preview(obj):
    preview = utils.set_preview_hidden(obj, True)
    if preview is None:
        return False
    _HIDDEN_PREVIEW["name"] = preview.name
    return True


def _restore_hidden_preview():
    """Unhide the preview this add-on hid, if it is still around."""
    name = _HIDDEN_PREVIEW["name"]
    preview = bpy.data.objects.get(name) if name else None
    if preview is not None:
        preview.hide_viewport = False
    _HIDDEN_PREVIEW["name"] = None


def _defer(func, *args):
    """Run func after the depsgraph update that scheduled it has finished.

    Editing objects from inside a depsgraph callback tags the depsgraph
    again, so the work is handed to a timer instead.
    """
    def callback():
        func(*args)
        return None

    bpy.app.timers.register(callback, first_interval=0.0)


def _refresh_preview(scene, target):
    if _editing_names(target):
        return
    settings = scene.anymaker_settings
    moved, merged, preview, _, _, skipped, _, _, _ = utils.refresh_preview(
        target,
        settings.grid_unit_size,
        settings.include_degenerate_faces,
        settings.preview_opacity,
    )
    _restore_hidden_preview()
    print(f"Refresh Preview On Edit Mode: {preview.name} rebuilt from "
          f"{target.name} ({moved} vertices snapped, {merged} merged)")
    for indices in skipped:
        print(f"  non-planar face skipped: vertices {list(indices)}")


def _refresh_preview_setting_changed(self, context):
    if not self.refresh_preview_on_edit:
        _restore_hidden_preview()


@persistent
def _anymaker_depsgraph_update(scene, depsgraph):
    """Hide the preview on entering edit mode, rebuild it on leaving.

    Only the target set in the add-on panel is watched: for a single object
    that is the object itself, and for a collection it is any of its mesh
    objects. Names are tracked instead of references, since an object edited
    and deleted in one session leaves a dangling reference behind.
    """
    target = _current_target(scene)
    signature = _target_signature(target)

    state = _EDIT_STATE
    if not state["primed"]:
        state.update(primed=True, signature=signature)
        return
    if signature == state["signature"]:
        return

    previous = state["signature"]
    state["signature"] = signature

    if not scene.anymaker_settings.refresh_preview_on_edit:
        return
    if target is None:
        return
    if signature[1] and not previous[1]:
        _hide_preview(target)
        return
    if previous[1] and not signature[1] and previous[0] == signature[0]:
        _defer(_refresh_preview, scene, target)


class AnymakerSettings(PropertyGroup):
    grid_unit_size: FloatProperty(
        name="Grid Unit Size",
        description="Anymaker grid base unit; sizes the vertex cubes and "
                    "decides how strictly faces must be planar",
        default=utils.AM_GRID_UNIT_SIZE,
        min=0.001,
        unit='LENGTH',
    )
    include_degenerate_faces: BoolProperty(
        name="Include Degenerate Faces",
        description="Keep the 4 zero-area faces per edge where the cube edge "
                    "runs parallel to the axis between the two vertices",
        default=False,
    )
    preview_opacity: FloatProperty(
        name="Preview Opacity",
        description="Transparency of the generated preview mesh",
        default=utils.AM_PREVIEW_OPACITY,
        min=0.0,
        max=1.0,
        soft_max=1.0,
    )
    refresh_preview_on_edit: BoolProperty(
        name="Refresh Preview On Edit Mode",
        description="Hide the preview mesh while the target is in edit mode, "
                    "then rebuild it when leaving edit mode",
        default=False,
        update=_refresh_preview_setting_changed,
    )


class ANYMAKER_OT_snap_to_grid_points(Operator):
    """Snap vertices onto their grid points and merge the shared ones"""
    bl_idname = "anymaker.snap_to_grid_points"
    bl_label = "Snap To Grid Points"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _target_ready(context.scene)

    def execute(self, context):
        target = _current_target(context.scene)
        unit = context.scene.anymaker_settings.grid_unit_size
        moved, merged, edges, faces = utils.snap_target_to_grid_points(
            target, unit
        )
        verts = utils.target_counts(target)[0]
        print(f"Snap To Grid Points: {target.name} grid unit {unit} -> "
              f"{moved} vertices snapped, {merged} merged, {edges} edges "
              f"and {faces} faces removed, {verts} vertices left")
        if not moved and not merged:
            self.report({'INFO'},
                        f"{target.name}: all {verts} vertices already on grid "
                        f"points")
            return {'FINISHED'}
        self.report({'INFO'},
                    f"{target.name}: {moved} vertices snapped, {merged} merged "
                    f"({verts} left)")
        return {'FINISHED'}


class ANYMAKER_OT_generate_preview(Operator):
    """Generate Anymaker preview mesh from the selected target"""
    bl_idname = "anymaker.generate_preview_mesh"
    bl_label = "Generate Preview Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _target_ready(context.scene)

    def execute(self, context):
        target = _current_target(context.scene)
        settings = context.scene.anymaker_settings
        size = settings.grid_unit_size
        degenerate = settings.include_degenerate_faces
        opacity = settings.preview_opacity
        moved, merged, preview, bridges, caps, skipped, kept, verts, edges = (
            utils.refresh_preview(target, size, degenerate, opacity)
        )
        print(f"Generate Preview Mesh: {target.name} -> {preview.name} "
              f"({moved} vertices snapped, {merged} merged, "
              f"{verts} vertices, {edges} edges, {bridges} bridge faces, "
              f"{caps} caps, {kept} faces kept, grid unit {size}, "
              f"degenerate={degenerate}, opacity={opacity})")
        for indices in skipped:
            print(f"  non-planar face skipped: vertices {list(indices)}")
        if skipped:
            self.report(
                {'WARNING'},
                f"{preview.name}: {caps} caps, {len(skipped)} faces "
                f"skipped (not planar)"
            )
        else:
            self.report(
                {'INFO'},
                f"{preview.name}: {verts}v {edges}e {bridges} bridges "
                f"{caps} caps" + snap_note(moved, merged)
            )
        return {'FINISHED'}


class ANYMAKER_OT_apply_grid_gap(Operator):
    """Set the viewport grid gap to the Anymaker vertex cube size"""
    bl_idname = "anymaker.apply_grid_gap"
    bl_label = "Apply Grid Gap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        size = context.scene.anymaker_settings.grid_unit_size
        applied = 0
        for space in utils.iter_view3d_spaces():
            if utils.apply_grid_gap(space, size, lines=10):
                applied += 1
        print(f"Apply Grid Gap: {size} applied to {applied} viewport(s)")
        if not applied:
            self.report({'WARNING'}, "No viewport grid property found")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Grid gap set to {size} ({applied} viewports)")
        return {'FINISHED'}


class ANYMAKER_OT_export_save(Operator):
    """Export Anymaker vehicle save file from the selected target"""
    bl_idname = "anymaker.export_save_file"
    bl_label = "Export Save File"
    bl_options = {'REGISTER', 'PRESET', 'UNDO'}

    vehicle_id: IntProperty(
        name="Vehicle ID",
        description="Game id written into the meta and data files",
        default=99,
        min=0,
    )
    vehicle_name: StringProperty(
        name="Vehicle Name",
        description="Base file name for the exported .meta and .data",
        default="",
    )
    output_dir: StringProperty(
        name="Output Dir",
        description="Directory the .meta and .data files are written to",
        subtype='DIR_PATH',
        default="",
    )

    @classmethod
    def poll(cls, context):
        return _target_ready(context.scene)

    def execute(self, context):
        target = _current_target(context.scene)
        unit = context.scene.anymaker_settings.grid_unit_size
        try:
            result = utils.export_vehicle(
                target,
                self.vehicle_id,
                self.output_dir,
                self.vehicle_name,
                unit,
            )
        except OSError as error:
            self.report({'ERROR'}, f"Export failed: {error}")
            return {'CANCELLED'}

        collection = result["collection"]
        moved = result["moved"]
        merged = result["merged"]
        count = len(collection.objects)
        print(f"Export Save File: {target.name} id={self.vehicle_id} "
              f"-> {result['meta_path']}, {result['data_path']}, {count} "
              f"labels in {collection.name}, {moved} vertices snapped, "
              f"{merged} merged")
        self.report({'INFO'},
                    f"Exported {self.vehicle_name} "
                    f"({result['nodes']} nodes, "
                    f"{result['plates']} plates, {count} labels)"
                    + snap_note(moved, merged))
        return {'FINISHED'}

    def invoke(self, context, event):
        # Set default values based on current selection and blend file
        target = _current_target(context.scene)
        if target is None:
            # Try to pick an eligible mesh
            for candidate in context.selected_objects:
                if candidate.type == 'MESH' and not utils.is_preview_object(candidate):
                    target = candidate
                    break
        if target is not None:
            if not self.vehicle_name:
                self.vehicle_name = target.name
        if not self.output_dir:
            blend_path = bpy.data.filepath
            if blend_path:
                self.output_dir = os.path.dirname(blend_path)
            else:
                self.output_dir = bpy.path.abspath("//")
        return context.window_manager.invoke_props_dialog(self)


class VIEW3D_PT_anymaker_helper(Panel):
    bl_label = "Anymaker Helper"
    bl_idname = "VIEW3D_PT_anymaker_helper"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Anymaker Helper"
    bl_context = "objectmode"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "anymaker_target_type", expand=True)
        if scene.anymaker_target_type == 'COLLECTION':
            layout.prop(scene, "anymaker_target_collection", text="Collection")
        else:
            layout.prop(scene, "anymaker_target", text="Game Object")

        box = layout.box()
        box.use_property_split = True
        box.prop(scene.anymaker_settings, "grid_unit_size")
        box.prop(scene.anymaker_settings, "include_degenerate_faces")
        box.prop(scene.anymaker_settings, "preview_opacity")
        box.prop(scene.anymaker_settings, "refresh_preview_on_edit")

        layout.operator("anymaker.apply_grid_gap", icon='SNAP_GRID')
        layout.operator("anymaker.snap_to_grid_points", icon='SNAP_VERTEX')
        layout.operator("anymaker.generate_preview_mesh",
                        icon='MESH_CUBE')
        layout.operator("anymaker.export_save_file", icon='EXPORT')


classes = (
    AnymakerSettings,
    ANYMAKER_OT_snap_to_grid_points,
    ANYMAKER_OT_generate_preview,
    ANYMAKER_OT_apply_grid_gap,
    ANYMAKER_OT_export_save,
    VIEW3D_PT_anymaker_helper,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.anymaker_target = PointerProperty(
        name="Anymaker Target",
        description="Select the game object to convert/export",
        type=bpy.types.Object,
        update=_target_changed,
    )
    bpy.types.Scene.anymaker_target_collection = PointerProperty(
        name="Anymaker Target Collection",
        description="Select the collection of mesh objects to convert/export",
        type=bpy.types.Collection,
        update=_target_changed,
    )
    bpy.types.Scene.anymaker_target_type = EnumProperty(
        name="Anymaker Target Type",
        description="Whether the target is a single object or a collection",
        items=[
            ('OBJECT', "Object", "Convert a single mesh object"),
            ('COLLECTION', "Collection",
             "Convert every mesh object in a collection"),
        ],
        default='OBJECT',
        update=_target_changed,
    )
    bpy.types.Scene.anymaker_settings = PointerProperty(
        name="Anymaker Settings",
        type=AnymakerSettings
    )
    handlers = bpy.app.handlers.depsgraph_update_post
    if _anymaker_depsgraph_update not in handlers:
        handlers.append(_anymaker_depsgraph_update)


def unregister():
    _restore_hidden_preview()
    handlers = bpy.app.handlers.depsgraph_update_post
    if _anymaker_depsgraph_update in handlers:
        handlers.remove(_anymaker_depsgraph_update)
    _EDIT_STATE.update(primed=False, signature=None)
    del bpy.types.Scene.anymaker_settings
    del bpy.types.Scene.anymaker_target_type
    del bpy.types.Scene.anymaker_target_collection
    del bpy.types.Scene.anymaker_target
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
