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


def iter_mesh_objects(target):
    """Yield the mesh objects a target contributes to the result.

    A target is either a single mesh object or a collection; a collection is
    read one level deep (its own objects, not nested collections). Preview
    objects are never part of a target.
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


def _polygons_double_sided(source):
    """Indices of polygons whose edges are all marked as seams."""
    mesh = source.data
    seam_edges = set()
    for edge in mesh.edges:
        if edge.use_seam:
            seam_edges.add(tuple(sorted(edge.vertices)))
    doubles = set()
    for polygon in mesh.polygons:
        verts = tuple(polygon.vertices)
        if verts and all(
            tuple(sorted((verts[k], verts[(k + 1) % len(verts)]))) in seam_edges
            for k in range(len(verts))
        ):
            doubles.add(polygon.index)
    return doubles


def _export_plate_loops(source):
    """Vertex loops in plate export order.

    A plate whose edges are all marked as seams is exported twice, the
    second copy wound the opposite way, so the game shows it from both
    sides.
    """
    doubles = _polygons_double_sided(source)
    loops = []
    for polygon in source.data.polygons:
        loop = tuple(polygon.vertices)
        loops.append(loop)
        if polygon.index in doubles:
            loops.append(tuple(reversed(loop)))
    return loops


def _append_face_caps(faces, source, centers, bases, tolerance):
    """Bridge the cubes of every planar face with a cap polygon.

    Each cap corner is the cube corner furthest along the face normal, so
    the cap keeps the original face's vertex order, edge count and normal.
    Returns (caps_added, skipped_vertex_indices).
    """
    caps = 0
    skipped = []
    doubles = _polygons_double_sided(source)
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
        if polygon.index in doubles:
            flip_corner_cap = []
            flipped = (-normal[0], -normal[1], -normal[2])
            for vi in indices:
                corner = _farthest_corner(flipped)
                flip_corner_cap.append(bases[vi] + corner)
            faces.append(tuple(reversed(flip_corner_cap)))
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
                       include_degenerate=True, name=None):
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

    mesh = bpy.data.meshes.new(name or (source.name + PREVIEW_SUFFIX))
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


def snap_target_to_grid_points(target, unit=AM_GRID_UNIT_SIZE):
    """Snap every mesh object a target contributes, summing the results.

    A single object target goes through one snap; a collection target snaps
    each of its mesh objects independently.
    """
    moved = merged = edges = faces = 0
    for obj in iter_mesh_objects(target):
        obj_moved, obj_merged, obj_edges, obj_faces = snap_to_grid_points(
            obj, unit
        )
        moved += obj_moved
        merged += obj_merged
        edges += obj_edges
        faces += obj_faces
    return moved, merged, edges, faces


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
            "nodes": [vi + 1 for vi in reversed(loop)],
            "glass_impacts": [],
        }
        for index, loop in enumerate(_export_plate_loops(source))
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


def create_labels(source, unit=AM_GRID_UNIT_SIZE, geometry=None):
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

    for index, loop in enumerate(_export_plate_loops(geometry)):
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
                   unit=AM_GRID_UNIT_SIZE):
    """Write <name>.meta and <name>.data, then label nodes and plates.

    source may be a single mesh object or a collection. Snapping to grid runs
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

    pre_moved, pre_merged, _, _ = snap_target_to_grid_points(source, unit)
    temp = _combined_temp_object(source)
    try:
        post_moved, post_merged, _, _ = snap_to_grid_points(temp, unit)
        moved = pre_moved + post_moved
        merged = pre_merged + post_merged
        _write_json(meta_path, build_vehicle_meta(vehicle_id))
        _write_json(data_path, build_vehicle_data(temp, vehicle_id, unit))
        collection = create_labels(source, unit, temp)
        nodes = len(temp.data.vertices)
        plates = len(temp.data.polygons)
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


def _apply_all_modifiers(obj):
    """Bake every modifier on obj into its mesh data.

    The object is evaluated through the dependency graph, which applies all
    of its modifiers in order, and the baked result is swapped in as obj's
    mesh so no modifier is left to affect the preview.
    """
    if not obj.modifiers:
        return
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    baked = bpy.data.meshes.new_from_object(
        evaluated, preserve_all_data_layers=False, depsgraph=depsgraph
    )
    old_mesh = obj.data
    obj.data = baked
    obj.modifiers.clear()
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def _temp_object_with_modifiers(source):
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
    _apply_all_modifiers(temp)
    return temp


def _remove_temp_object(temp):
    mesh = temp.data
    bpy.data.objects.remove(temp, do_unlink=True)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _combined_temp_object(target):
    """Build one temporary object holding target's final geometry.

    A single object target is just that object copied with its modifiers
    baked. A collection target bakes each of its mesh objects separately
    and combines them into a single mesh in world space, so a collection
    whose members carry different modifiers still yields one object to
    build the preview or export from. Callers must pass the result to
    _remove_temp_object.
    """
    if not isinstance(target, bpy.types.Collection):
        return _temp_object_with_modifiers(target)

    name = target.name + PREVIEW_SUFFIX + "_temp"
    bm = bmesh.new()
    try:
        for obj in iter_mesh_objects(target):
            temp = _temp_object_with_modifiers(obj)
            try:
                temp.data.transform(temp.matrix_world)
                bm.from_mesh(temp.data)
            finally:
                _remove_temp_object(temp)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
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
                          include_degenerate=True, opacity=0.5):
    """Build the preview mesh for target, snapping before and after baking.

    Snapping runs twice, matching the export path: once on each contributing
    object before its modifiers are baked, and once on the baked, combined
    mesh, so a modifier such as Mirror that moves geometry off the original
    vertices still previews on grid. Returns (preview, moved, merged, bridges,
    caps, skipped, kept, verts, edges), where verts and edges are the node and
    edge counts of the combined, snapped mesh.
    """
    remove_preview_object(target)
    pre_moved, pre_merged, _, _ = snap_target_to_grid_points(target, size)
    temp = _combined_temp_object(target)
    try:
        post_moved, post_merged, _, _ = snap_to_grid_points(temp, size)
        moved = pre_moved + post_moved
        merged = pre_merged + post_merged
        verts = len(temp.data.vertices)
        edges = len(temp.data.edges)
        mesh, bridges, caps, skipped, kept = build_preview_mesh(
            temp, size, include_degenerate, preview_name_for(target)
        )
    finally:
        _remove_temp_object(temp)
    preview = bpy.data.objects.new(mesh.name, mesh)
    preview[PREVIEW_MARKER] = target.name
    material = _get_preview_material(target, opacity)
    mesh.materials.append(material)
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
                    include_degenerate=True, opacity=AM_PREVIEW_OPACITY):
    """Rebuild the preview object from the combined, snapped mesh.

    Shared by the Generate Preview Mesh button and the edit mode watcher, so
    both go through the same pipeline as export. Works for a single object
    or a collection target. Returns (moved, merged, preview, bridges, caps,
    skipped, kept, verts, edges).
    """
    (
        preview, moved, merged, bridges, caps, skipped, kept, verts, edges
    ) = create_preview_object(target, size, include_degenerate, opacity)
    return moved, merged, preview, bridges, caps, skipped, kept, verts, edges
