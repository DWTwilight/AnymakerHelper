# Anymaker Helper - Blender add-on for building Anymaker vehicles.
# Copyright (C) 2026 DWTwilight
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. See LICENSE for the full text.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import math
import os

import bmesh # type: ignore
import bpy # type: ignore
from mathutils import Vector # type: ignore

AM_GRID_UNIT_SIZE = 0.08
PREVIEW_SUFFIX = "_AM_preview"
PREVIEW_MATERIAL_SUFFIX = "_AM_preview_mat"
LABEL_COLLECTION_SUFFIX = "_AM_labels"
AM_PLANAR_TOLERANCE_RATIO = 0.05
AM_PREVIEW_OPACITY = 1.0
GRID_EPSILON_RATIO = 0.001
PREVIEW_MARKER = "anymaker_preview_of"
LABEL_MARKER = "anymaker_label_for"

_IDENTITY_TRANSFORM = {
    "m": [0, 0, 0, 0, 0, 0, 0, 0, 0],
    "t": [0, 0, 0],
}

_EMPTY_LINK_GROUPS = (
    ("plate_paint", []),
    ("grids", []),
    ("electric_links", []),
    ("mechanical_links", []),
    ("liquid_links", []),
    ("gas_links", []),
    ("belt_links", []),
    ("data_links", []),
    ("loot_locations", []),
    ("creature_locations", []),
)

_CUBE_CORNERS = (
    (-1.0, -1.0, -1.0), (1.0, -1.0, -1.0), (1.0, 1.0, -1.0),
    (-1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0),
)

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


def is_preview_object(obj):
    return PREVIEW_MARKER in obj.keys()


def _is_preview_mesh_name(name, base):
    return name == base or name.startswith(base + ".")


def find_preview_objects(obj):
    """Return every preview object generated for obj, whatever its name.

    A preview is matched by the marker it carries rather than by name, so a
    preview that picked up a .001 suffix when Blender had to make room for it
    is still found.
    """
    return [
        candidate for candidate in bpy.data.objects
        if candidate != obj and is_preview_object(candidate)
        and candidate.get(PREVIEW_MARKER) == obj.name
    ]


def get_preview_object(obj):
    """Return the preview object generated for obj, or None."""
    return next(iter(find_preview_objects(obj)), None)


def remove_preview_object(obj):
    """Delete every preview object generated for obj, and its orphan meshes.

    Matching by marker sweeps up previews that older runs left behind under a
    .001 style name, so repeated generation stops piling up new suffixes.
    Returns whether anything was removed.
    """
    removed = 0
    for preview in find_preview_objects(obj):
        mesh = preview.data
        bpy.data.objects.remove(preview, do_unlink=True)
        if isinstance(mesh, bpy.types.Mesh) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        removed += 1

    base = preview_name_for(obj)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0 and _is_preview_mesh_name(mesh.name, base):
            bpy.data.meshes.remove(mesh)
    return removed > 0


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


def _farthest_corner(normal):
    """Index into the cube's 8 corner verts for the corner furthest along
    the given unit normal, measured from the cube center."""
    best_index = 0
    best_dot = None
    for index, (sx, sy, sz) in enumerate(_CUBE_CORNERS):
        dot = normal[0] * sx + normal[1] * sy + normal[2] * sz
        if best_dot is None or dot > best_dot:
            best_dot = dot
            best_index = index
    return best_index


def _polygon_normal(points):
    nx = ny = nz = 0.0
    count = len(points)
    for k in range(count):
        ax, ay, az = points[k]
        bx, by, bz = points[(k + 1) % count]
        nx += ay * bz - az * by
        ny += az * bx - ax * bz
        nz += ax * by - ay * bx
    return nx, ny, nz


def is_planar(points, tolerance):
    """Return (ok, normal) for a planar test of world-space points.

    The normal is the Newell normal of the polygon, normalized.
    Fails when the polygon is degenerate or its vertices do not lie
    on a common plane within the given tolerance.
    """
    normal = _polygon_normal(points)
    length = math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2)
    if length < 1e-12:
        return False, (0.0, 0.0, 0.0)
    unit = (normal[0] / length, normal[1] / length, normal[2] / length)

    if len(points) < 3:
        return False, unit

    centroid = [0.0, 0.0, 0.0]
    for point in points:
        centroid[0] += point[0]
        centroid[1] += point[1]
        centroid[2] += point[2]
    count = len(points)
    centroid = (centroid[0] / count, centroid[1] / count, centroid[2] / count)

    for point in points:
        dx = point[0] - centroid[0]
        dy = point[1] - centroid[1]
        dz = point[2] - centroid[2]
        distance = abs(dx * unit[0] + dy * unit[1] + dz * unit[2])
        if distance > tolerance:
            return False, unit
    return True, unit


def _quad_normal(verts, quad):
    return _polygon_normal([verts[i] for i in quad])



def _quad_centroid(verts, quad):
    cx = cy = cz = 0.0
    for idx in quad:
        x, y, z = verts[idx]
        cx += x
        cy += y
        cz += z
    return cx * 0.25, cy * 0.25, cz * 0.25


def _append_face_caps(faces, source, centers, bases, tolerance):
    """Bridge the cubes of every planar face with a cap polygon.

    Each cap corner is the cube corner furthest along the face normal, so
    the cap keeps the original face's vertex order, edge count and normal.
    Returns (caps_added, skipped_vertex_indices).
    """
    caps = 0
    skipped = []
    for polygon in source.data.polygons:
        indices = tuple(polygon.vertices)
        points = [tuple(centers[i]) for i in indices]
        planar, normal = is_planar(points, tolerance)
        if not planar:
            skipped.append(indices)
            continue
        cap = []
        for vi in indices:
            corner = _farthest_corner(normal)
            cap.append(bases[vi] + corner)
        faces.append(tuple(cap))
        caps += 1
    return caps, skipped


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


def build_preview_mesh(source, size=AM_GRID_UNIT_SIZE,
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

    caps, skipped = _append_face_caps(
        faces, source, centers, bases,
        tolerance=size * AM_PLANAR_TOLERANCE_RATIO
    )

    mesh = bpy.data.meshes.new(source.name + PREVIEW_SUFFIX)
    mesh.from_pydata(verts, [], faces)
    # validate() drops faces it considers invalid, which can include the
    # deliberate zero-area bridge quads, so report what the mesh really kept.
    mesh.validate(verbose=False)
    mesh.update()
    return mesh, bridges, caps, skipped, len(mesh.polygons)


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


def to_grid_coords(co, unit=AM_GRID_UNIT_SIZE):
    """Convert a world-space position to integer Anymaker grid cells.

    Rounds to the nearest cell, so a vertex sitting just inside or just
    outside a cell boundary lands in the same cell.
    """
    return [
        int(math.floor(component / unit + 0.5)) for component in co
    ]


def to_game_coords(co):
    """Convert a Blender world position to Anymaker axes.

    Blender is Z-up, the game is Y-up, so Z maps to Y and Blender's Y becomes
    the game's Z.

    This swap mirrors the model (its determinant is -1), which flips every
    face normal, so build_vehicle_data reverses each plate's node order to
    keep the normals pointing outward.

    Note: Keep the mesh above Z=0 (Blender) to avoid intersecting the ground.
    """
    return (co[0], co[2], co[1])


def grid_position(co, unit=AM_GRID_UNIT_SIZE):
    """Return the grid point closest to a world-space position.

    A grid point is the grid cell corner a vertex lands on during export,
    so this is the export coordinate computation run in reverse: the
    vertex' grid cell, turned back into a world position.
    """
    cell = to_grid_coords(to_game_coords(co), unit)
    return (cell[0] * unit, cell[2] * unit, cell[1] * unit)


def _node_positions(source, unit=AM_GRID_UNIT_SIZE):
    matrix = source.matrix_world
    return [
        to_grid_coords(to_game_coords(matrix @ vertex.co), unit)
        for vertex in source.data.vertices
    ]


def snap_to_grid_points(source, unit=AM_GRID_UNIT_SIZE):
    """Snap every vertex onto its grid point, then merge shared ones.

    Snapping puts each vertex exactly on the grid cell the game reads, and
    merging welds the vertices that share a cell into one, so no node is
    exported twice at the same position. Positions are compared in world
    space and written back through the object's matrix, so a rotated or
    scaled object snaps the same way an untransformed one does.

    Returns how many vertices moved, how many were welded away, and how
    many edges and faces the weld removed.
    """
    mesh = source.data
    if not mesh.vertices:
        return 0, 0, 0, 0

    before_verts = len(mesh.vertices)
    before_edges = len(mesh.edges)
    before_faces = len(mesh.polygons)

    matrix = source.matrix_world
    to_local = matrix.inverted_safe()
    epsilon = unit * GRID_EPSILON_RATIO
    scale = max(max(abs(axis) for axis in matrix.to_scale()), 1.0)

    bm = bmesh.new()
    bm.from_mesh(mesh)
    moved = 0
    for vert in bm.verts:
        world = matrix @ vert.co
        grid = grid_position(world, unit)
        if any(abs(grid[axis] - world[axis]) > epsilon for axis in range(3)):
            moved += 1
        vert.co = to_local @ Vector(grid)
    bm.verts.ensure_lookup_table()
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=epsilon / scale)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return (
        moved,
        before_verts - len(mesh.vertices),
        before_edges - len(mesh.edges),
        before_faces - len(mesh.polygons),
    )


def build_vehicle_data(source, vehicle_id, unit=AM_GRID_UNIT_SIZE):
    positions = _node_positions(source, unit)

    nodes = [
        {"id": index + 1, "pos": pos}
        for index, pos in enumerate(positions)
    ]

    edges = [
        {"n0": edge.vertices[0] + 1, "n1": edge.vertices[1] + 1}
        for edge in source.data.edges
    ]

    # Blender stores polygon loops counter-clockwise seen from the front
    # face. The game reads the opposite winding, so the loop is reversed
    # here to make the plate normal point outward.
    plates = [
        {
            "id": index + 1,
            "nodes": [vi + 1 for vi in reversed(polygon.vertices)],
            "glass_impacts": [],
        }
        for index, polygon in enumerate(source.data.polygons)
    ]

    vehicle = {
        "id": vehicle_id,
        "transform": dict(_IDENTITY_TRANSFORM),
        "nodes": nodes,
        "edges": edges,
        "plates": plates,
    }
    for name, value in _EMPTY_LINK_GROUPS:
        vehicle[name] = list(value)
    vehicle["buoyancy_fill"] = [0.0, 0.0]

    return {
        "definitions": {},
        "vehicles": {"vehicles": [vehicle]},
    }


def build_vehicle_meta(vehicle_id):
    return {
        "vehicles": {
            "vehicles": [
                {
                    "id": vehicle_id,
                    "transform": dict(_IDENTITY_TRANSFORM),
                    "bounds": {"min": [0, 0, 0], "max": [0, 0, 0]},
                }
            ]
        }
    }


def _write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


def label_collection_for(source):
    name = source.name + LABEL_COLLECTION_SUFFIX
    return bpy.data.collections.get(name)


def remove_labels(source):
    """Delete the label collection previously created for source."""
    collection = label_collection_for(source)
    if collection is None:
        return 0
    removed = len(collection.objects)
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(collection)
    return removed


def _add_label(collection, name, location, source):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = 'PLAIN_AXES'
    obj.empty_display_size = AM_GRID_UNIT_SIZE
    obj.show_name = True
    obj.location = location
    obj[LABEL_MARKER] = source.name
    collection.objects.link(obj)
    return obj


def create_labels(source, unit=AM_GRID_UNIT_SIZE):
    """Create an in-scene empty labelled for every node and plate.

    All labels live in one collection named after the source, which is
    removed and rebuilt on every export.
    """
    remove_labels(source)
    collection = bpy.data.collections.new(
        source.name + LABEL_COLLECTION_SUFFIX
    )
    bpy.context.scene.collection.children.link(collection)

    matrix = source.matrix_world
    positions = [
        matrix @ vertex.co for vertex in source.data.vertices
    ]

    for index, position in enumerate(positions):
        _add_label(collection, f"node-{index + 1}", position, source)

    for index, polygon in enumerate(source.data.polygons):
        centres = [positions[vi] for vi in polygon.vertices]
        if centres:
            count = len(centres)
            location = (
                sum(c.x for c in centres) / count,
                sum(c.y for c in centres) / count,
                sum(c.z for c in centres) / count,
            )
        else:
            location = matrix.translation
        _add_label(collection, f"plate-{index + 1}", location, source)
    return collection


def export_vehicle(source, vehicle_id, output_dir, name="vehicle",
                   unit=AM_GRID_UNIT_SIZE):
    """Write <name>.meta and <name>.data, then label nodes and plates."""
    directory = bpy.path.abspath(output_dir)
    if not directory:
        # Fall back to the current Blender file's directory
        blend_path = bpy.data.filepath
        if blend_path:
            directory = os.path.dirname(blend_path)
        else:
            directory = bpy.path.abspath("//")

    os.makedirs(directory, exist_ok=True)

    base = os.path.join(directory, name)
    meta_path = f"{base}.meta"
    data_path = f"{base}.data"

    _write_json(meta_path, build_vehicle_meta(vehicle_id))
    _write_json(data_path, build_vehicle_data(source, vehicle_id, unit))

    collection = create_labels(source, unit)
    return meta_path, data_path, collection


def _get_preview_material(source, opacity):
    name = source.name + PREVIEW_MATERIAL_SUFFIX
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (0.8, 0.8, 0.8, opacity)
    if hasattr(material, "blend_method"):
        material.blend_method = 'BLEND'
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = 'BLENDED'
    if hasattr(material, "show_transparent_back"):
        material.show_transparent_back = False

    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        for name_ in ("Alpha", "Base Color"):
            socket = bsdf.inputs.get(name_)
            if socket is None:
                continue
            if name_ == "Alpha":
                socket.default_value = opacity
            elif len(socket.default_value) == 4:
                socket.default_value = (0.8, 0.8, 0.8, opacity)
    return material


def _copy_prop(target, source, name):
    """Copy one modifier property, handling arrays and vectors elementwise."""
    if not (hasattr(source, name) and hasattr(target, name)):
        return
    value = getattr(source, name)
    if hasattr(value, "__len__") and not isinstance(value, str):
        target_value = getattr(target, name)
        for index in range(len(value)):
            target_value[index] = value[index]
    else:
        setattr(target, name, value)


_MIRROR_COPY_PROPS = (
    "use_axis",
    "use_bisect_axis",
    "use_bisect_flip_axis",
    "use_clip",
    "use_mirror_merge",
    "merge_threshold",
    "use_mirror_vertex_groups",
    "use_mirror_u",
    "use_mirror_v",
    "mirror_u_offset",
    "mirror_v_offset",
    "offset",
    "show_viewport",
    "show_in_editmode",
    "show_on_cage",
)


def copy_mirror_modifiers(source, preview):
    """Copy every Mirror modifier from source onto the preview object.

    The preview object has an identity transform and holds world-space
    geometry, while a Mirror modifier works in the owner's local space. A
    modifier that has no mirror object would therefore mirror about the world
    origin instead of the source's own centre, so it is pointed at the source
    to put the mirror plane back where the source has it. Returns how many
    were copied.
    """
    copied = 0
    for modifier in source.modifiers:
        if modifier.type != 'MIRROR':
            continue
        target = preview.modifiers.new(modifier.name, 'MIRROR')
        for name in _MIRROR_COPY_PROPS:
            _copy_prop(target, modifier, name)
        if modifier.mirror_object is None:
            target.mirror_object = source
        else:
            target.mirror_object = modifier.mirror_object
        copied += 1
    return copied


def create_preview_object(source, size=AM_GRID_UNIT_SIZE,
                          include_degenerate=True, opacity=0.5):
    remove_preview_object(source)
    mesh, bridges, caps, skipped, kept = build_preview_mesh(
        source, size, include_degenerate
    )
    preview = bpy.data.objects.new(mesh.name, mesh)
    preview[PREVIEW_MARKER] = source.name
    material = _get_preview_material(source, opacity)
    mesh.materials.append(material)
    for collection in source.users_collection:
        collection.objects.link(preview)
    if not source.users_collection:
        bpy.context.scene.collection.objects.link(preview)
    copy_mirror_modifiers(source, preview)
    return preview, bridges, caps, skipped, kept


def set_preview_hidden(source, hidden):
    """Hide or show the preview object generated for source.

    Returns the preview object, or None when source has no preview, so a
    missing preview is never mistaken for a visibility change.
    """
    preview = get_preview_object(source)
    if preview is None:
        return None
    preview.hide_viewport = hidden
    return preview


def refresh_preview(source, size=AM_GRID_UNIT_SIZE,
                    include_degenerate=True, opacity=AM_PREVIEW_OPACITY):
    """Snap to grid points, then rebuild the preview object.

    Shared by the Generate Preview Mesh button and the edit mode watcher, so
    both go through the same pipeline. Returns (moved, merged, preview,
    bridges, caps, skipped, kept).
    """
    moved, merged, _, _ = snap_to_grid_points(source, size)
    preview, bridges, caps, skipped, kept = create_preview_object(
        source, size, include_degenerate, opacity
    )
    return moved, merged, preview, bridges, caps, skipped, kept
