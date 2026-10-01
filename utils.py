import bpy # type: ignore

AM_VERTEX_CUBE_SIZE = 0.08
PREVIEW_SUFFIX = "_AM_preview"

_CUBE_FACES = (
    (0, 3, 2, 1),
    (4, 5, 6, 7),
    (0, 1, 5, 4),
    (1, 2, 6, 5),
    (2, 3, 7, 6),
    (3, 0, 4, 7),
)


def preview_name_for(obj):
    return obj.name + PREVIEW_SUFFIX


def get_preview_object(obj):
    return bpy.data.objects.get(preview_name_for(obj))


def remove_preview_object(obj):
    preview = get_preview_object(obj)
    if preview is None:
        return False
    mesh = preview.data
    bpy.data.objects.remove(preview, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    return True


def _append_cube(verts, faces, center, size):
    half = size * 0.5
    cx, cy, cz = center
    base = len(verts)
    for sx, sy, sz in (
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ):
        verts.append((cx + sx * half, cy + sy * half, cz + sz * half))
    for face in _CUBE_FACES:
        faces.append(tuple(base + i for i in face))


def build_preview_mesh(source, size=AM_VERTEX_CUBE_SIZE):
    matrix = source.matrix_world
    verts = []
    faces = []
    for vertex in source.data.vertices:
        _append_cube(verts, faces, matrix @ vertex.co, size)
    mesh = bpy.data.meshes.new(source.name + PREVIEW_SUFFIX)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    return mesh


def iter_view3d_spaces():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        yield space


def _set_grid_props(holder, scale, lines):
    if not hasattr(holder, "grid_scale"):
        return False
    holder.grid_scale = scale
    if lines is not None and hasattr(holder, "grid_lines"):
        holder.grid_lines = lines
    if hasattr(holder, "show_floor"):
        holder.show_floor = True
    return True


def apply_grid_gap(space, scale, lines=None):
    if _set_grid_props(space, scale, lines):
        return "space"
    overlay = getattr(space, "overlay", None)
    if overlay is not None and _set_grid_props(overlay, scale, lines):
        return "overlay"
    return None


def create_preview_object(source, size=AM_VERTEX_CUBE_SIZE):
    remove_preview_object(source)
    mesh = build_preview_mesh(source, size)
    preview = bpy.data.objects.new(mesh.name, mesh)
    for collection in source.users_collection:
        collection.objects.link(preview)
    if not source.users_collection:
        bpy.context.scene.collection.objects.link(preview)
    return preview
