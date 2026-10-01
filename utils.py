import math

import bpy # type: ignore

AM_VERTEX_CUBE_SIZE = 0.08
PREVIEW_SUFFIX = "_AM_preview"

_CUBE_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
)

_MIN_QUAD_AREA = 1e-9

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
    return base


def _quad_normal(verts, quad):
    nx = ny = nz = 0.0
    for k in range(4):
        ax, ay, az = verts[quad[k]]
        bx, by, bz = verts[quad[(k + 1) % 4]]
        nx += ay * bz - az * by
        ny += az * bx - ax * bz
        nz += ax * by - ay * bx
    return nx, ny, nz


def _quad_area(verts, quad):
    nx, ny, nz = _quad_normal(verts, quad)
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _quad_centroid(verts, quad):
    cx = cy = cz = 0.0
    for idx in quad:
        x, y, z = verts[idx]
        cx += x
        cy += y
        cz += z
    return cx * 0.25, cy * 0.25, cz * 0.25


def _outward_direction(verts, quad, origin, axis):
    centroid = _quad_centroid(verts, quad)
    ox, oy, oz = origin
    ax, ay, az = axis
    cx, cy, cz = centroid[0] - ox, centroid[1] - oy, centroid[2] - oz
    length_sq = ax * ax + ay * ay + az * az
    if length_sq == 0.0:
        return cx, cy, cz
    t = (cx * ax + cy * ay + cz * az) / length_sq
    return cx - ax * t, cy - ay * t, cz - az * t


def _append_bridges(verts, faces, base_a, base_b, origin, axis,
                    include_degenerate):
    added = 0
    for ia, ib in _CUBE_EDGES:
        quad = (base_a + ia, base_a + ib, base_b + ib, base_b + ia)
        nx, ny, nz = _quad_normal(verts, quad)
        area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        if area < _MIN_QUAD_AREA:
            if not include_degenerate:
                continue
        else:
            ox, oy, oz = _outward_direction(verts, quad, origin, axis)
            if nx * ox + ny * oy + nz * oz < 0.0:
                quad = (quad[0], quad[3], quad[2], quad[1])
        faces.append(quad)
        added += 1
    return added


def build_preview_mesh(source, size=AM_VERTEX_CUBE_SIZE,
                       include_degenerate=True):
    matrix = source.matrix_world
    verts = []
    faces = []
    bases = []
    centers = []
    for vertex in source.data.vertices:
        center = matrix @ vertex.co
        centers.append(center)
        bases.append(_append_cube(verts, faces, center, size))

    bridges = 0
    for edge in source.data.edges:
        va, vb = edge.vertices
        center_a = centers[va]
        axis = centers[vb] - center_a
        bridges += _append_bridges(
            verts, faces, bases[va], bases[vb],
            center_a, axis, include_degenerate
        )

    mesh = bpy.data.meshes.new(source.name + PREVIEW_SUFFIX)
    mesh.from_pydata(verts, [], faces)
    mesh.validate()
    mesh.update()
    return mesh, bridges


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


def create_preview_object(source, size=AM_VERTEX_CUBE_SIZE,
                          include_degenerate=True):
    remove_preview_object(source)
    mesh, bridges = build_preview_mesh(source, size, include_degenerate)
    preview = bpy.data.objects.new(mesh.name, mesh)
    for collection in source.users_collection:
        collection.objects.link(preview)
    if not source.users_collection:
        bpy.context.scene.collection.objects.link(preview)
    return preview, bridges
