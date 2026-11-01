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
import shutil

import bmesh # type: ignore
import bpy # type: ignore
from mathutils import Vector # type: ignore

AM_GRID_UNIT_SIZE = 0.08
PREVIEW_SUFFIX = "_AM_preview"
PREVIEW_MATERIAL_SUFFIX = "_AM_preview_mat"
LABEL_COLLECTION_SUFFIX = "_AM_labels"
AM_PLANAR_TOLERANCE_RATIO = 0.05
AM_PREVIEW_OPACITY = 1.0
AM_WINDOW_MATERIAL_NAME = "AM_Window_MAT"
AM_WINDOW_OPACITY = 0.3
AM_DEFAULT_MATERIAL_NAME = "AM_Default_MAT"
GRID_EPSILON_RATIO = 0.001
PREVIEW_MARKER = "anymaker_preview_of"
LABEL_MARKER = "anymaker_label_for"
QUAD_CENTER_COLLECTION_SUFFIX = "_Quad_Centers"
QUAD_CENTER_MARKER = "anymaker_quad_center"
QUAD_CENTER_KEY = "anymaker_quad_center_key"
QUAD_CENTER_ENDPOINTS = "anymaker_quad_center_endpoints"
QUAD_CENTER_COLLECTION_MARKER = "anymaker_quad_centers"
QUAD_CENTER_TOLERANCE_RATIO = 0.5

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
_CORNER_TIE_EPSILON = 1e-6

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


def is_hidden_object(obj):
    """Whether obj is hidden in the viewport, by itself or its collection."""
    return obj.hide_viewport or obj.hide_get()


def iter_mesh_objects(target, include_hidden=True):
    """Yield the mesh objects a target contributes to the result.

    A target is either a single mesh object or a collection; a collection is
    read one level deep (its own objects, not nested collections). Preview
    objects are never part of a target. Hidden objects are skipped when
    include_hidden is false.
    """
    if isinstance(target, bpy.types.Collection):
        candidates = list(target.objects)
    elif isinstance(target, bpy.types.Object):
        candidates = [target]
    else:
        return []
    return [
        obj for obj in candidates
        if obj.type == 'MESH' and not is_preview_object(obj)
        and (include_hidden or not is_hidden_object(obj))
    ]


def target_counts(target):
    """Return the total (vertices, edges, faces) over a target's meshes."""
    verts = edges = faces = 0
    for obj in iter_mesh_objects(target):
        verts += len(obj.data.vertices)
        edges += len(obj.data.edges)
        faces += len(obj.data.polygons)
    return verts, edges, faces


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


def _farthest_corners(normal):
    """Indices into the cube's 8 corners furthest along the given normal."""
    best_dot = None
    best = []
    for index, (sx, sy, sz) in enumerate(_CUBE_CORNERS):
        dot = normal[0] * sx + normal[1] * sy + normal[2] * sz
        if best_dot is None or dot > best_dot + _CORNER_TIE_EPSILON:
            best_dot = dot
            best = [index]
        elif abs(dot - best_dot) <= _CORNER_TIE_EPSILON:
            best.append(index)
    return best


def _convex_hull_indices(point_indices, verts, normal):
    """Return a convex outer boundary for the given vertex indices."""
    if len(point_indices) < 3:
        return None
    direction = Vector(normal)
    if direction.length < 1e-12:
        return None
    direction.normalize()
    helper = Vector((1.0, 0.0, 0.0))
    if abs(direction.x) > 0.9:
        helper = Vector((0.0, 1.0, 0.0))
    first_axis = direction.cross(helper)
    if first_axis.length < 1e-12:
        return None
    first_axis.normalize()
    second_axis = direction.cross(first_axis)

    center = Vector((0.0, 0.0, 0.0))
    for index in point_indices:
        center += Vector(verts[index])
    center /= len(point_indices)

    coordinates = []
    for index in point_indices:
        offset = Vector(verts[index]) - center
        coordinates.append((offset.dot(first_axis), offset.dot(second_axis)))

    order = sorted(
        range(len(point_indices)), key=lambda i: coordinates[i]
    )

    def turn(origin, first, second):
        ax = coordinates[first][0] - coordinates[origin][0]
        ay = coordinates[first][1] - coordinates[origin][1]
        bx = coordinates[second][0] - coordinates[origin][0]
        by = coordinates[second][1] - coordinates[origin][1]
        return ax * by - ay * bx

    def half(sequence):
        hull = []
        for index in sequence:
            while (len(hull) >= 2
                   and turn(hull[-2], hull[-1], index) <= 1e-12):
                hull.pop()
            hull.append(index)
        return hull

    lower = half(order)
    upper = half(reversed(order))
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return None
    return [point_indices[index] for index in hull]


def _cap_face_indices(indices, bases, verts, corner_normal, face_normal):
    """Build one cap loop, merging tied farthest corners into a larger face."""
    corners = _farthest_corners(corner_normal)
    if len(corners) == 1:
        corner = corners[0]
        return tuple(bases[vi] + corner for vi in indices)

    point_indices = [
        bases[vi] + corner
        for corner in corners
        for vi in indices
    ]
    hull = _convex_hull_indices(point_indices, verts, face_normal)
    if hull is not None:
        return tuple(hull)
    corner = corners[0]
    return tuple(bases[vi] + corner for vi in indices)


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


def _polygons_double_sided(source):
    """Indices of polygons whose edges are all marked as sharp."""
    mesh = source.data
    sharp_edges = set()
    for edge in mesh.edges:
        if edge.use_edge_sharp:
            sharp_edges.add(tuple(sorted(edge.vertices)))
    doubles = set()
    for polygon in mesh.polygons:
        verts = tuple(polygon.vertices)
        if verts and all(
            tuple(sorted((verts[k], verts[(k + 1) % len(verts)])))
            in sharp_edges for k in range(len(verts))
        ):
            doubles.add(polygon.index)
    return doubles


def _is_window_material(material):
    return material is not None and (
        material.name == AM_WINDOW_MATERIAL_NAME
        or material.name.startswith(AM_WINDOW_MATERIAL_NAME + ".")
    )


def _window_polygons(source):
    """Indices of polygons assigned to the window material."""
    slots = source.material_slots
    windows = set()
    for polygon in source.data.polygons:
        index = polygon.material_index
        if index >= len(slots):
            continue
        if _is_window_material(slots[index].material):
            windows.add(polygon.index)
    return windows


def _export_plate_entries(source, fan_plates=None):
    """Vertex loops in plate export order, with their window flag.

    A plate whose edges are all marked as sharp is exported twice, the
    second copy wound the opposite way, so the game shows it from both
    sides. Both copies keep the source polygon's window flag. Fan plates
    replace their source sectors with one loop starting at the fan center,
    or two loops when the fan itself is double-sided.
    """
    doubles = _polygons_double_sided(source)
    windows = _window_polygons(source)
    fan_faces = set()
    fan_by_first = {}
    for plate in fan_plates or ():
        faces = plate["faces"]
        fan_faces.update(faces)
        fan_by_first[faces[0]] = plate

    entries = []
    for polygon in source.data.polygons:
        if polygon.index in fan_faces:
            plate = fan_by_first.get(polygon.index)
            if plate is not None:
                center = plate["center"]
                boundary = tuple(plate["boundary"])
                loop = (center,) + tuple(reversed(boundary))
                entries.append((loop, plate["is_window"]))
                if plate.get("double_sided"):
                    back_center = plate.get("back_center")
                    blender_loop = (center,) + boundary
                    if back_center is not None and back_center in blender_loop:
                        index = blender_loop.index(back_center)
                        back_loop = (
                            blender_loop[index:] + blender_loop[:index]
                        )
                    else:
                        back_loop = blender_loop
                    entries.append((back_loop, plate["is_window"]))
            continue
        loop = tuple(reversed(polygon.vertices))
        is_window = polygon.index in windows
        entries.append((loop, is_window))
        if polygon.index in doubles:
            entries.append((tuple(reversed(loop)), is_window))
    return entries


def _export_plate_loops(source, fan_plates=None):
    """Vertex loops in plate export order."""
    return [loop for loop, _ in _export_plate_entries(source, fan_plates)]


def _append_face_caps(faces, source, centers, bases, verts, tolerance,
                      double_faces=None, single_faces=None):
    """Bridge the cubes of every planar face with a cap polygon.

    Each cap corner is the cube corner furthest along the face normal, so
    the cap keeps the original face's vertex order, edge count and normal.
    Returns (caps_added, skipped_vertex_indices, window_caps), where
    window_caps has one flag per added cap, in the same order.
    """
    caps = 0
    skipped = []
    window_caps = []
    doubles = _polygons_double_sided(source)
    if double_faces:
        doubles.update(double_faces)
    if single_faces:
        doubles.difference_update(single_faces)
    windows = _window_polygons(source)
    for polygon in source.data.polygons:
        indices = tuple(polygon.vertices)
        points = [tuple(centers[i]) for i in indices]
        planar, normal = is_planar(points, tolerance)
        if not planar:
            skipped.append(indices)
            continue
        is_window = polygon.index in windows
        cap = _cap_face_indices(indices, bases, verts, normal, normal)
        faces.append(cap)
        window_caps.append(is_window)
        caps += 1
        if polygon.index in doubles:
            flipped = (-normal[0], -normal[1], -normal[2])
            flip_corner_cap = _cap_face_indices(
                indices, bases, verts, flipped, normal
            )
            faces.append(tuple(reversed(flip_corner_cap)))
            window_caps.append(is_window)
            caps += 1
    return caps, skipped, window_caps


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
                       include_degenerate=True, name=None,
                       preview_material=None, window_material=None,
                       fan_edges=None, double_faces=None,
                       single_faces=None):
    matrix = source.matrix_world
    verts = []
    faces = []
    bases = []
    centers = []
    for vertex in source.data.vertices:
        center = matrix @ vertex.co
        centers.append(center)
        bases.append(_append_cube(verts, faces, center, size))

    fan_edges = fan_edges or set()
    bridges = 0
    for edge in source.data.edges:
        va, vb = edge.vertices
        if _edge_key(va, vb) in fan_edges:
            continue
        center_a = centers[va]
        axis = centers[vb] - center_a
        bridges += _append_bridges(
            verts, faces, bases[va], bases[vb],
            center_a, axis, include_degenerate
        )

    cap_start = len(faces)
    caps, skipped, window_caps = _append_face_caps(
        faces, source, centers, bases, verts,
        tolerance=size * AM_PLANAR_TOLERANCE_RATIO,
        double_faces=double_faces, single_faces=single_faces,
    )

    mesh = bpy.data.meshes.new(name or (source.name + PREVIEW_SUFFIX))
    has_windows = window_material is not None and any(window_caps)
    if preview_material is not None:
        mesh.materials.append(preview_material)
    elif has_windows:
        mesh.materials.append(None)
    if has_windows:
        mesh.materials.append(window_material)
        window_index = len(mesh.materials) - 1
    else:
        window_index = None
    mesh.from_pydata(verts, [], faces)
    if window_index is not None:
        for offset, is_window in enumerate(window_caps):
            if is_window:
                mesh.polygons[cap_start + offset].material_index = window_index
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


def _split_edges_at_vertices(bm, matrix, epsilon):
    """Split every edge that passes through another vertex.

    A vertex sitting in the middle of an edge leaves a T-junction when the
    shared position is welded. Splitting the edge there first makes both
    sides share the vertex, so the weld can join them and no overlapping
    edge is left behind. Returns how many splits were made.
    """
    verts = list(bm.verts)
    if len(verts) < 3:
        return 0
    world = {vert: matrix @ vert.co for vert in verts}
    splits = []

    for edge in list(bm.edges):
        start, end = edge.verts
        start_pos = world[start]
        end_pos = world[end]
        axis = end_pos - start_pos
        length_sq = axis.length_squared
        if length_sq <= epsilon * epsilon:
            continue
        length = math.sqrt(length_sq)

        hits = []
        for vert in verts:
            if vert is start or vert is end:
                continue
            point = world[vert]
            if ((point - start_pos).length <= epsilon
                    or (point - end_pos).length <= epsilon):
                continue
            factor = (point - start_pos).dot(axis) / length_sq
            if factor <= 0.0 or factor >= 1.0:
                continue
            closest = start_pos + axis * factor
            if (point - closest).length <= epsilon:
                hits.append((factor, vert))

        if not hits:
            continue
        hits.sort(key=lambda hit: hit[0])
        unique = []
        factor_epsilon = epsilon / length
        for hit in hits:
            if unique and abs(hit[0] - unique[-1][0]) <= factor_epsilon:
                continue
            unique.append(hit)
        splits.append((edge, start, end, unique))

    count = 0
    for edge, start, end, hits in splits:
        current_edge = edge
        current_start = start
        previous = 0.0
        for factor, _ in hits:
            step = (factor - previous) / (1.0 - previous)
            if step <= 0.0 or step >= 1.0:
                continue
            new_edge, split_vert = bmesh.utils.edge_split(
                current_edge, current_start, step
            )
            if end not in new_edge.verts:
                for candidate in split_vert.link_edges:
                    if end in candidate.verts:
                        new_edge = candidate
                        break
                else:
                    break
            current_edge = new_edge
            current_start = split_vert
            previous = factor
            count += 1
    return count


def _remove_collinear_vertices(bm, matrix, epsilon):
    """Dissolve vertices whose two edges form one straight line.

    Such a vertex adds nothing to the shape, so it is dissolved and its two
    edges become one. Returns how many vertices were removed.
    """
    removed = 0
    while True:
        positions = {vert: matrix @ vert.co for vert in bm.verts}
        candidates = []
        for vert in bm.verts:
            if len(vert.link_edges) != 2:
                continue
            first, second = vert.link_edges
            before = first.other_vert(vert)
            after = second.other_vert(vert)
            if before is after:
                continue
            to_before = positions[before] - positions[vert]
            to_after = positions[after] - positions[vert]
            if (to_before.length <= epsilon
                    or to_after.length <= epsilon):
                continue
            if to_before.dot(to_after) >= 0.0:
                continue
            axis = to_after - to_before
            if axis.length <= epsilon:
                continue
            distance = to_before.cross(to_after).length / axis.length
            if distance <= epsilon:
                candidates.append(vert)

        if not candidates:
            break
        before_verts = len(bm.verts)
        bmesh.ops.dissolve_verts(bm, verts=candidates)
        removed += before_verts - len(bm.verts)
        if len(bm.verts) == before_verts:
            break
    return removed


def snap_to_grid_points(source, unit=AM_GRID_UNIT_SIZE):
    """Snap every vertex onto its grid point, then merge shared ones.

    Snapping puts each vertex exactly on the grid cell the game reads, and
    merging welds the vertices that share a cell into one, so no node is
    exported twice at the same position. Edges that passed through another
    vertex are split there before the weld, and vertices that only continue
    a straight edge are dissolved after it. Positions are compared in world
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
    _split_edges_at_vertices(bm, matrix, epsilon)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=epsilon / scale)
    _remove_collinear_vertices(bm, matrix, epsilon)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return (
        moved,
        before_verts - len(mesh.vertices),
        before_edges - len(mesh.edges),
        before_faces - len(mesh.polygons),
    )


def snap_target_to_grid_points(target, unit=AM_GRID_UNIT_SIZE,
                               include_hidden=True):
    """Snap every mesh object a target contributes, summing the results.

    A single object target goes through one snap; a collection target snaps
    each of its mesh objects independently. Hidden objects are skipped when
    include_hidden is false.
    """
    moved = merged = edges = faces = 0
    for obj in iter_mesh_objects(target, include_hidden):
        obj_moved, obj_merged, obj_edges, obj_faces = snap_to_grid_points(
            obj, unit
        )
        moved += obj_moved
        merged += obj_merged
        edges += obj_edges
        faces += obj_faces
    return moved, merged, edges, faces


def build_vehicle_data(source, vehicle_id, unit=AM_GRID_UNIT_SIZE,
                       fan_plates=None, fan_edges=None):
    positions = _node_positions(source, unit)

    nodes = [
        {"id": index + 1, "pos": pos}
        for index, pos in enumerate(positions)
    ]

    fan_edges = fan_edges or set()
    edges = [
        {"n0": edge.vertices[0] + 1, "n1": edge.vertices[1] + 1}
        for edge in source.data.edges
        if _edge_key(*edge.vertices) not in fan_edges
    ]

    plates = []
    for index, (loop, is_window) in enumerate(
            _export_plate_entries(source, fan_plates)):
        plate = {
            "id": index + 1,
            "nodes": [vi + 1 for vi in loop],
        }
        if is_window:
            plate["type"] = "window"
        plate["glass_impacts"] = []
        plates.append(plate)

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


def create_labels(source, unit=AM_GRID_UNIT_SIZE, geometry=None,
                  fan_plates=None):
    """Create an in-scene empty labelled for every node and plate.

    All labels live in one collection named after the source, which is
    removed and rebuilt on every export. Labels are placed from geometry,
    which defaults to source but can be a modifier-baked copy whose nodes
    and plates match what is exported.
    """
    remove_labels(source)
    collection = bpy.data.collections.new(
        source.name + LABEL_COLLECTION_SUFFIX
    )
    bpy.context.scene.collection.children.link(collection)

    geometry = geometry or source
    matrix = geometry.matrix_world
    positions = [
        matrix @ vertex.co for vertex in geometry.data.vertices
    ]

    for index, position in enumerate(positions):
        _add_label(collection, f"node-{index + 1}", position, source)

    for index, loop in enumerate(
            _export_plate_loops(geometry, fan_plates)):
        centres = [positions[vi] for vi in loop]
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
                   unit=AM_GRID_UNIT_SIZE, include_hidden=True):
    """Write <name>.meta and <name>.data, then label nodes and plates.

    source may be a single mesh object or a collection. Hidden objects in a
    collection are included when include_hidden is true. Snapping to grid runs
    twice: once on each contributing object before its modifiers are baked, so
    the source vertices are on grid going in, and once on the baked, combined
    mesh, so the exported nodes are on grid going out even when a modifier
    (such as Mirror) moved geometry off those vertices.

    Returns a dict describing the written files, the snap, and the node and
    plate counts of the combined mesh.
    """
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

    pre_moved, pre_merged, _, _ = snap_target_to_grid_points(
        source, unit, include_hidden
    )
    temp = _combined_temp_object(source, include_hidden, unit)
    try:
        post_moved, post_merged, _, _ = snap_to_grid_points(temp, unit)
        moved = pre_moved + post_moved
        merged = pre_merged + post_merged
        fan_plates, fan_edges = analyze_fan_plates(temp)
        fan_plates = apply_quad_center_tags(source, temp, fan_plates, unit)
        _write_json(meta_path, build_vehicle_meta(vehicle_id))
        _write_json(
            data_path,
            build_vehicle_data(
                temp, vehicle_id, unit, fan_plates, fan_edges
            ),
        )
        collection = create_labels(source, unit, temp, fan_plates)
        nodes = len(temp.data.vertices)
        plates = len(_export_plate_entries(temp, fan_plates))
    finally:
        _remove_temp_object(temp)
    return {
        "meta_path": meta_path,
        "data_path": data_path,
        "collection": collection,
        "moved": moved,
        "merged": merged,
        "nodes": nodes,
        "plates": plates,
    }


def update_vehicle_data_file(source, data_path, vehicle_id,
                           unit=AM_GRID_UNIT_SIZE, include_hidden=True):
    """Update one vehicle's nodes, edges and plates in an existing .data file.

    A backup is written next to the original as <name>_bak.data before the
    original is overwritten. All other vehicle fields are left untouched.
    """
    path = bpy.path.abspath(data_path)
    if not path or not os.path.isfile(path):
        raise ValueError(f"Data file not found: {data_path}")

    pre_moved, pre_merged, _, _ = snap_target_to_grid_points(
        source, unit, include_hidden
    )
    temp = _combined_temp_object(source, include_hidden, unit)
    try:
        post_moved, post_merged, _, _ = snap_to_grid_points(temp, unit)
        moved = pre_moved + post_moved
        merged = pre_merged + post_merged
        fan_plates, fan_edges = analyze_fan_plates(temp)
        fan_plates = apply_quad_center_tags(source, temp, fan_plates, unit)
        new_data = build_vehicle_data(
            temp, vehicle_id, unit, fan_plates, fan_edges
        )
        new_vehicle = new_data["vehicles"]["vehicles"][0]
        nodes = len(temp.data.vertices)
        plates = len(_export_plate_entries(temp, fan_plates))
    finally:
        _remove_temp_object(temp)

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    vehicles_section = payload.get("vehicles")
    vehicles = (
        vehicles_section.get("vehicles")
        if isinstance(vehicles_section, dict) else None
    )
    if not isinstance(vehicles, list):
        raise ValueError("Data file has no vehicles.vehicles list")
    target = next(
        (vehicle for vehicle in vehicles
         if isinstance(vehicle, dict)
         and vehicle.get("id") == vehicle_id),
        None,
    )
    if target is None:
        raise ValueError(f"Vehicle id {vehicle_id} not found")
    for key in ("nodes", "edges", "plates"):
        target[key] = new_vehicle[key]

    root, extension = os.path.splitext(path)
    backup_path = f"{root}_bak{extension or '.data'}"
    shutil.copyfile(path, backup_path)
    _write_json(path, payload)

    return {
        "data_path": path,
        "backup_path": backup_path,
        "moved": moved,
        "merged": merged,
        "nodes": nodes,
        "plates": plates,
    }


def _set_material_opacity(material, opacity):
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


def _get_preview_material(source, opacity):
    name = source.name + PREVIEW_MATERIAL_SUFFIX
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    return _set_material_opacity(material, opacity)


def ensure_window_material():
    """Return AM_Window_MAT, creating it with window opacity if missing."""
    material = bpy.data.materials.get(AM_WINDOW_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(AM_WINDOW_MATERIAL_NAME)
    return _set_material_opacity(material, AM_WINDOW_OPACITY)


def ensure_default_material():
    """Return AM_Default_MAT, creating it if missing."""
    material = bpy.data.materials.get(AM_DEFAULT_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(AM_DEFAULT_MATERIAL_NAME)
        material.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    return material


def ensure_target_materials(target):
    """Give every mesh object in target a material in its first slot.

    Without a first material, the window material would take slot zero and
    every face would inherit it, making the whole object look like glass.
    """
    added = 0
    material = None
    for obj in iter_mesh_objects(target, include_hidden=True):
        slots = obj.material_slots
        if slots and slots[0].material is not None:
            continue
        if material is None:
            material = ensure_default_material()
        if not slots:
            obj.data.materials.append(material)
        elif slots[0].link == 'OBJECT':
            slots[0].material = material
        else:
            obj.data.materials[0] = material
        added += 1
    return added


def _faces_form_planar_face(first, second, matrix, tolerance):
    """Whether two faces share one plane, regardless of their winding."""
    first_points = [matrix @ vertex.co for vertex in first.verts]
    second_points = [matrix @ vertex.co for vertex in second.verts]
    normal = _polygon_normal(first_points)
    length = math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2)
    if length < 1e-12:
        normal = _polygon_normal(second_points)
        length = math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2)
        if length < 1e-12:
            return False
    unit = (normal[0] / length, normal[1] / length, normal[2] / length)

    centroid = [0.0, 0.0, 0.0]
    for point in first_points:
        centroid[0] += point[0]
        centroid[1] += point[1]
        centroid[2] += point[2]
    count = len(first_points)
    centroid = (centroid[0] / count, centroid[1] / count, centroid[2] / count)

    for point in first_points + second_points:
        offset = (
            point[0] - centroid[0],
            point[1] - centroid[1],
            point[2] - centroid[2],
        )
        distance = abs(
            offset[0] * unit[0]
            + offset[1] * unit[1]
            + offset[2] * unit[2]
        )
        if distance > tolerance:
            return False
    return True


def _dissolve_planar_seam_edges(mesh, matrix, tolerance):
    """Dissolve seam edges whose two faces form one planar face.

    A mirror modifier can leave a seam where the mirrored half meets the
    original. When the two faces on either side of such a seam lie on the
    same plane, the seam no longer marks a real crease and the edge is
    dissolved so the faces become one. Seams that also separate different
    materials are kept, so material boundaries such as windows survive.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        seams = []
        for edge in bm.edges:
            faces = edge.link_faces
            if not edge.seam or len(faces) != 2:
                continue
            first, second = faces
            if first.material_index != second.material_index:
                continue
            if _faces_form_planar_face(first, second, matrix, tolerance):
                seams.append(edge)
        if seams:
            bmesh.ops.dissolve_edges(
                bm, edges=seams, use_verts=False, use_face_split=False
            )
            bm.to_mesh(mesh)
            mesh.update()
    finally:
        bm.free()


def _edge_key(a, b):
    return (a, b) if a < b else (b, a)


def _fan_boundary_loop(faces, center):
    """Return (ordered boundary, double_sided), or None if invalid."""
    edge_counts = {}
    for face in faces:
        for edge in face.edges:
            edge_counts[edge] = edge_counts.get(edge, 0) + 1

    boundary = []
    for edge, count in edge_counts.items():
        if count == 1:
            boundary.append(edge)
        elif count != 2 or not edge.seam:
            return None
    if len(boundary) < 3:
        return None

    adjacency = {}
    for edge in boundary:
        first, second = edge.verts
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    if any(len(neighbours) != 2 for neighbours in adjacency.values()):
        return None

    start = next(iter(adjacency))
    loop = [start]
    previous = None
    current = start
    while True:
        neighbours = adjacency[current]
        next_vert = (
            neighbours[0] if neighbours[0] is not previous else neighbours[1]
        )
        if next_vert is start:
            break
        if next_vert in loop:
            return None
        loop.append(next_vert)
        previous, current = current, next_vert
        if len(loop) > len(adjacency):
            return None
    if len(loop) != len(adjacency):
        return None

    boundary_normal = _polygon_normal([vert.co for vert in loop])
    normal_sum = [0.0, 0.0, 0.0]
    for face in faces:
        normal_sum[0] += face.normal[0]
        normal_sum[1] += face.normal[1]
        normal_sum[2] += face.normal[2]
    if (boundary_normal[0] * normal_sum[0]
            + boundary_normal[1] * normal_sum[1]
            + boundary_normal[2] * normal_sum[2]) < 0.0:
        loop.reverse()

    if center in loop:
        index = loop.index(center)
        loop = loop[index:] + loop[:index]
        loop = loop[1:]
    if len(loop) < 3:
        return None

    double_sided = all(not edge.smooth for edge in boundary)
    return loop, double_sided


def analyze_fan_plates(source):
    """Find non-planar fan plates from the remaining seam edges.

    A fan plate is a contiguous full or partial fan of planar faces around
    one seam-jointed central vertex; two faces sharing one seam edge is the
    smallest valid fan. Returns (plates, internal_edges), where plates
    describe the combined fan faces and internal_edges are the seam spokes
    that must be omitted from output.
    """
    slots = source.material_slots
    bm = bmesh.new()
    try:
        bm.from_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        seams = {edge for edge in bm.edges if edge.seam}
        plates = []
        internal_edges = set()

        while seams:
            counts = {}
            for edge in seams:
                for vert in edge.verts:
                    counts[vert] = counts.get(vert, 0) + 1
            ordered = sorted(
                counts.items(), key=lambda item: item[1], reverse=True
            )

            found = None
            for center, count in ordered:
                if count < 1:
                    break
                fan_faces = set()
                for edge in center.link_edges:
                    if edge in seams:
                        fan_faces.update(edge.link_faces)
                if len(fan_faces) < 2:
                    continue
                boundary_result = _fan_boundary_loop(fan_faces, center)
                if boundary_result is None:
                    continue
                boundary, double_sided = boundary_result
                found = (center, fan_faces, boundary, double_sided)
                break

            if found is None:
                break

            center, fan_faces, boundary, double_sided = found
            spokes = [edge for edge in center.link_edges if edge in seams]
            for edge in spokes:
                seams.discard(edge)
                internal_edges.add(_edge_key(
                    edge.verts[0].index, edge.verts[1].index
                ))

            faces = tuple(sorted(face.index for face in fan_faces))
            is_window = any(
                face.material_index < len(slots)
                and _is_window_material(slots[face.material_index].material)
                for face in fan_faces
            )
            back_center = None
            if len(fan_faces) == 2:
                first, second = tuple(fan_faces)
                second_edges = set(second.edges)
                seam_edge = next(
                    (edge for edge in first.edges
                     if edge in second_edges and edge.seam),
                    None,
                )
                if seam_edge is not None:
                    opposite = seam_edge.other_vert(center)
                    if opposite is not None:
                        back_center = opposite.index
            plates.append({
                "faces": faces,
                "center": center.index,
                "boundary": tuple(vert.index for vert in boundary),
                "is_window": is_window,
                "double_sided": double_sided,
                "back_center": back_center,
            })

        return plates, internal_edges
    finally:
        bm.free()


def _quad_center_parent(target):
    if isinstance(target, bpy.types.Collection):
        return target
    collections = list(target.users_collection)
    if collections:
        return collections[0]
    return bpy.context.scene.collection


def _find_quad_center_collection(target):
    parent = _quad_center_parent(target)
    name = target.name + QUAD_CENTER_COLLECTION_SUFFIX
    for child in parent.children:
        if (child.get(QUAD_CENTER_COLLECTION_MARKER)
                or child.name == name
                or child.name.startswith(name + ".")):
            return child
    return None


def _ensure_quad_center_collection(target):
    collection = _find_quad_center_collection(target)
    if collection is None:
        collection = bpy.data.collections.new(
            target.name + QUAD_CENTER_COLLECTION_SUFFIX
        )
        _quad_center_parent(target).children.link(collection)
        collection[QUAD_CENTER_COLLECTION_MARKER] = True
    return collection


def _quad_key(points, unit):
    cells = sorted(
        tuple(to_grid_coords(point, unit)) for point in points
    )
    return "|".join(
        ",".join(str(value) for value in cell) for cell in cells
    )


def _tag_endpoint_index(tag, points, tolerance):
    best_index = None
    best_distance = tolerance
    for index, point in enumerate(points):
        distance = (tag.location - point).length
        if distance <= best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _store_quad_endpoints(tag, endpoints):
    tag[QUAD_CENTER_ENDPOINTS] = [
        component for point in endpoints for component in point
    ]


def _create_quad_center_tag(collection, location, key, unit, endpoints):
    tag = bpy.data.objects.new("quad-center", None)
    tag.empty_display_type = 'PLAIN_AXES'
    tag.empty_display_size = unit * 0.5
    tag.show_name = True
    tag.show_in_front = True
    tag.location = location
    tag[QUAD_CENTER_MARKER] = True
    tag[QUAD_CENTER_KEY] = key
    _store_quad_endpoints(tag, endpoints)
    collection.objects.link(tag)
    return tag


def flip_quad_center_tag(tag, unit):
    """Move a quad center tag to the other seam endpoint. Returns success."""
    stored = tag.get(QUAD_CENTER_ENDPOINTS)
    if stored is None or len(stored) != 6:
        return False
    endpoints = [Vector(stored[0:3]), Vector(stored[3:6])]

    tolerance = unit * QUAD_CENTER_TOLERANCE_RATIO
    distances = [(tag.location - point).length for point in endpoints]
    nearest = 0 if distances[0] <= distances[1] else 1
    if distances[nearest] > tolerance:
        return False
    tag.location = endpoints[1 - nearest]
    return True


def _quad_center_tags(collection):
    return [
        obj for obj in collection.objects
        if obj.get(QUAD_CENTER_MARKER)
    ]


def update_quad_center_tags(target, source, fan_plates, unit):
    """Reconcile manual quad-center tags with the current non-planar quads."""
    matrix = source.matrix_world
    quads = {}
    for plate in fan_plates:
        back_center = plate.get("back_center")
        if len(plate["faces"]) != 2 or back_center is None:
            continue
        points = [
            matrix @ source.data.vertices[index].co
            for index in (plate["center"], back_center)
        ]
        key = _quad_key(points, unit)
        quads[key] = (points, points[0])

    collection = _find_quad_center_collection(target)
    if collection is None:
        if not quads:
            return
        collection = _ensure_quad_center_collection(target)

    tolerance = unit * QUAD_CENTER_TOLERANCE_RATIO
    seen = set()
    for tag in list(collection.objects):
        if not tag.get(QUAD_CENTER_MARKER):
            continue
        key = tag.get(QUAD_CENTER_KEY)
        quad = quads.get(key)
        if (quad is None
                or _tag_endpoint_index(tag, quad[0], tolerance) is None):
            continue
        if key in seen:
            bpy.data.objects.remove(tag, do_unlink=True)
            continue
        seen.add(key)
        stored = tag.get(QUAD_CENTER_ENDPOINTS)
        if stored is None or len(stored) != 6:
            _store_quad_endpoints(tag, quad[0])

    for key, (points, default_point) in quads.items():
        if key in seen:
            continue
        _create_quad_center_tag(
            collection, default_point, key, unit, points
        )


def apply_quad_center_tags(target, source, fan_plates, unit):
    """Return fan plates with centers overridden by matching center tags."""
    collection = _find_quad_center_collection(target)
    if collection is None:
        return fan_plates
    tags = _quad_center_tags(collection)
    if not tags:
        return fan_plates

    matrix = source.matrix_world
    tolerance = unit * QUAD_CENTER_TOLERANCE_RATIO
    plates = []
    for plate in fan_plates:
        back_center = plate.get("back_center")
        if len(plate["faces"]) != 2 or back_center is None:
            plates.append(plate)
            continue

        center = plate["center"]
        points = [
            matrix @ source.data.vertices[index].co
            for index in (center, back_center)
        ]
        key = _quad_key(points, unit)
        chosen = None
        for tag in tags:
            if tag.get(QUAD_CENTER_KEY) != key:
                continue
            chosen = _tag_endpoint_index(tag, points, tolerance)
            if chosen is not None:
                break
        if chosen is None or chosen == 0:
            plates.append(plate)
            continue

        loop = (center,) + plate["boundary"]
        if back_center not in loop:
            plates.append(plate)
            continue
        index = loop.index(back_center)
        rotated = loop[index:] + loop[:index]
        updated = dict(plate)
        updated["center"] = back_center
        updated["boundary"] = tuple(rotated[1:])
        updated["back_center"] = center
        plates.append(updated)
    return plates


def _apply_all_modifiers(obj, unit=AM_GRID_UNIT_SIZE):
    """Bake every modifier on obj into its mesh data.

    The object is evaluated through the dependency graph, which applies all
    of its modifiers in order, and the baked result is swapped in as obj's
    mesh so no modifier is left to affect the preview. Seams left by a
    Mirror modifier are then cleaned up where they no longer separate
    differently angled faces.
    """
    if obj.modifiers:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        materials = list(obj.data.materials)
        baked = bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=False, depsgraph=depsgraph
        )
        if not baked.materials:
            for material in materials:
                baked.materials.append(material)
        old_mesh = obj.data
        obj.data = baked
        obj.modifiers.clear()
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    _dissolve_planar_seam_edges(
        obj.data, obj.matrix_world, unit * AM_PLANAR_TOLERANCE_RATIO
    )


def _temp_object_with_modifiers(source, unit=AM_GRID_UNIT_SIZE):
    """Copy source and bake all of its modifiers into a temporary object.

    The copy keeps source's transform, so the flattened geometry lines up
    with the original exactly. It is linked to the scene collection so the
    dependency graph evaluates it even when source sits in an excluded
    collection. Callers must pass the result to _remove_temp_object.
    """
    temp = source.copy()
    temp.data = source.data.copy()
    temp.name = source.name + PREVIEW_SUFFIX + "_temp"
    bpy.context.scene.collection.objects.link(temp)
    _apply_all_modifiers(temp, unit)
    return temp


def _remove_temp_object(temp):
    mesh = temp.data
    bpy.data.objects.remove(temp, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _combined_temp_object(target, include_hidden=True,
                          unit=AM_GRID_UNIT_SIZE):
    """Build one temporary object holding target's final geometry.

    A single object target is just that object copied with its modifiers
    baked. A collection target bakes each of its mesh objects separately
    and combines them into a single mesh in world space, so a collection
    whose members carry different modifiers still yields one object to
    build the preview or export from. Hidden objects are skipped when
    include_hidden is false. Callers must pass the result to
    _remove_temp_object.
    """
    if not isinstance(target, bpy.types.Collection):
        return _temp_object_with_modifiers(target, unit)

    name = target.name + PREVIEW_SUFFIX + "_temp"
    combined_materials = []
    material_indices = {}

    def material_index(material):
        key = material.as_pointer() if material else None
        index = material_indices.get(key)
        if index is None:
            index = len(combined_materials)
            material_indices[key] = index
            combined_materials.append(material)
        return index

    bm = bmesh.new()
    try:
        for obj in iter_mesh_objects(target, include_hidden):
            temp = _temp_object_with_modifiers(obj, unit)
            try:
                remap = [
                    material_index(slot.material)
                    for slot in temp.material_slots
                ]
                fallback = None
                for polygon in temp.data.polygons:
                    index = polygon.material_index
                    if index < len(remap):
                        polygon.material_index = remap[index]
                    else:
                        if fallback is None:
                            fallback = material_index(None)
                        polygon.material_index = fallback
                temp.data.transform(temp.matrix_world)
                bm.from_mesh(temp.data)
            finally:
                _remove_temp_object(temp)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        for material in combined_materials:
            mesh.materials.append(material)
    finally:
        bm.free()
    return bpy.data.objects.new(name, mesh)


def _parent_collections(target):
    """Return the collections that directly contain target, if known.

    Collections do not expose their parents, so the scene hierarchy is
    walked. The result may be empty when target is not linked anywhere.
    """
    parents = []
    seen = set()

    def walk(collection):
        for child in collection.children:
            if child == target:
                if collection.name not in seen:
                    seen.add(collection.name)
                    parents.append(collection)
            walk(child)

    for scene in bpy.data.scenes:
        walk(scene.collection)
    for collection in bpy.data.collections:
        walk(collection)
    return parents


def _preview_link_collections(target):
    """Return the collections the preview object should be linked into.

    A preview sits next to its source: an object preview joins the object's
    own collections, while a collection preview joins the collection's
    parent, i.e. the same level as the collection itself.
    """
    if isinstance(target, bpy.types.Collection):
        collections = _parent_collections(target)
    else:
        collections = list(target.users_collection)
    if not collections:
        collections = [bpy.context.scene.collection]
    return collections


def create_preview_object(target, size=AM_GRID_UNIT_SIZE,
                          include_degenerate=True, opacity=0.5,
                          include_hidden=True):
    """Build the preview mesh for target, snapping before and after baking.

    Snapping runs twice, matching the export path: once on each contributing
    object before its modifiers are baked, and once on the baked, combined
    mesh, so a modifier such as Mirror that moves geometry off the original
    vertices still previews on grid. Hidden objects in a collection are
    skipped when include_hidden is false. Returns (preview, moved, merged,
    bridges, caps, skipped, kept, verts, edges), where verts and edges are the
    node and edge counts of the combined, snapped mesh.
    """
    remove_preview_object(target)
    pre_moved, pre_merged, _, _ = snap_target_to_grid_points(
        target, size, include_hidden
    )
    temp = _combined_temp_object(target, include_hidden, size)
    try:
        post_moved, post_merged, _, _ = snap_to_grid_points(temp, size)
        moved = pre_moved + post_moved
        merged = pre_merged + post_merged
        verts = len(temp.data.vertices)
        edges = len(temp.data.edges)
        fan_plates, fan_edges = analyze_fan_plates(temp)
        fan_double_faces = set()
        fan_single_faces = set()
        for plate in fan_plates:
            if plate["double_sided"]:
                fan_double_faces.update(plate["faces"])
            else:
                fan_single_faces.update(plate["faces"])
        preview_material = _get_preview_material(target, opacity)
        window_material = ensure_window_material()
        mesh, bridges, caps, skipped, kept = build_preview_mesh(
            temp, size, include_degenerate, preview_name_for(target),
            preview_material, window_material, fan_edges,
            fan_double_faces, fan_single_faces
        )
        update_quad_center_tags(target, temp, fan_plates, size)
    finally:
        _remove_temp_object(temp)
    preview = bpy.data.objects.new(mesh.name, mesh)
    preview[PREVIEW_MARKER] = target.name
    for collection in _preview_link_collections(target):
        collection.objects.link(preview)
    return preview, moved, merged, bridges, caps, skipped, kept, verts, edges


def set_preview_hidden(target, hidden):
    """Hide or show the preview object generated for target.

    Returns the preview object, or None when target has no preview, so a
    missing preview is never mistaken for a visibility change.
    """
    preview = get_preview_object(target)
    if preview is None:
        return None
    preview.hide_viewport = hidden
    return preview


def refresh_preview(target, size=AM_GRID_UNIT_SIZE,
                    include_degenerate=True, opacity=AM_PREVIEW_OPACITY,
                    include_hidden=True):
    """Rebuild the preview object from the combined, snapped mesh.

    Shared by the Generate Preview Mesh button and the edit mode watcher, so
    both go through the same pipeline as export. Works for a single object
    or a collection target. Hidden objects in a collection are skipped when
    include_hidden is false. Returns (moved, merged, preview, bridges, caps,
    skipped, kept, verts, edges).
    """
    (
        preview, moved, merged, bridges, caps, skipped, kept, verts, edges
    ) = create_preview_object(
        target, size, include_degenerate, opacity, include_hidden
    )
    return moved, merged, preview, bridges, caps, skipped, kept, verts, edges
