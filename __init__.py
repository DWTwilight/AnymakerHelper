bl_info = {
    "name": "Anymaker Helper",
    "author": "AnyMakerModding",
    "version": (0, 1, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Anymaker Helper",
    "description": "Convert Blender objects to Anymaker preview meshes and export game save files",
    "category": "Object",
}

import bpy # type: ignore
from bpy.props import PointerProperty # type: ignore
from bpy.types import Operator, Panel # type: ignore


class ANYMAKER_OT_generate_preview(Operator):
    """Generate Anymaker preview mesh from selected object"""
    bl_idname = "anymaker.generate_preview_mesh"
    bl_label = "Generate Preview Mesh"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.anymaker_target is not None

    def execute(self, context):
        obj = context.scene.anymaker_target
        print(f"Generate Preview Mesh: {obj.name}")
        self.report({'INFO'}, f"Generate Preview Mesh: {obj.name}")
        return {'FINISHED'}


class ANYMAKER_OT_export_save(Operator):
    """Export Anymaker vehicle save file from selected object"""
    bl_idname = "anymaker.export_save_file"
    bl_label = "Export Save File"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.anymaker_target is not None

    def execute(self, context):
        obj = context.scene.anymaker_target
        print(f"Export Save File: {obj.name}")
        self.report({'INFO'}, f"Export Save File: {obj.name}")
        return {'FINISHED'}


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

        layout.prop(scene, "anymaker_target", text="Game Object")

        row = layout.row(align=True)
        row.operator("anymaker.generate_preview_mesh", text="Generate Preview Mesh")
        row = layout.row(align=True)
        row.operator("anymaker.export_save_file", text="Export Save File")


classes = (
    ANYMAKER_OT_generate_preview,
    ANYMAKER_OT_export_save,
    VIEW3D_PT_anymaker_helper,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.anymaker_target = PointerProperty(
        name="Anymaker Target",
        description="Select the game object to convert/export",
        type=bpy.types.Object
    )


def unregister():
    del bpy.types.Scene.anymaker_target
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
