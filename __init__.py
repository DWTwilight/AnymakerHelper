bl_info = {
    "name": "Anymaker Helper",
    "author": "AnyMakerModding",
    "version": (0, 2, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Anymaker Helper",
    "description": "Convert Blender objects to Anymaker preview meshes and export game save files",
    "category": "Object",
}

import bpy # type: ignore
from bpy.props import FloatProperty, PointerProperty # type: ignore
from bpy.types import Operator, Panel, PropertyGroup # type: ignore

from . import utils


class AnymakerSettings(PropertyGroup):
    vertex_cube_size: FloatProperty(
        name="Vertex Cube Size",
        description="Edge length of the cube generated per Anymaker vertex",
        default=utils.AM_VERTEX_CUBE_SIZE,
        min=0.001,
        soft_max=1.0,
        unit='LENGTH',
    )


class ANYMAKER_OT_generate_preview(Operator):
    """Generate Anymaker preview mesh from selected object"""
    bl_idname = "anymaker.generate_preview_mesh"
    bl_label = "Generate Preview Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.scene.anymaker_target
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.scene.anymaker_target
        size = context.scene.anymaker_settings.vertex_cube_size
        preview = utils.create_preview_object(obj, size)
        count = len(obj.data.vertices)
        print(f"Generate Preview Mesh: {obj.name} -> {preview.name} "
              f"({count} vertices, cube size {size})")
        self.report({'INFO'}, f"{preview.name} created ({count} vertices)")
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

        box = layout.box()
        box.use_property_split = True
        box.prop(scene.anymaker_settings, "vertex_cube_size")

        layout.operator("anymaker.generate_preview_mesh",
                        icon='MESH_CUBE')
        layout.operator("anymaker.export_save_file", icon='EXPORT')


classes = (
    AnymakerSettings,
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
    bpy.types.Scene.anymaker_settings = PointerProperty(
        name="Anymaker Settings",
        type=AnymakerSettings
    )


def unregister():
    del bpy.types.Scene.anymaker_settings
    del bpy.types.Scene.anymaker_target
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
