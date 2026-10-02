import json
import math
import os

import bpy # type: ignore

AM_GRID_UNIT_SIZE = 0.08
PREVIEW_SUFFIX = "_AM_preview"
PREVIEW_MATERIAL_SUFFIX = "_AM_preview_mat"
LABEL_COLLECTION_SUFFIX = "_AM_labels"
AM_PLANAR_TOLERANCE_RATIO = 0.05
AM_PREVIEW_OPACITY = 1.0
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


def get_preview_object(obj):
    """Return the preview object generated for obj, or None.

    Only objects carrying the preview marker are treated as previews, so an
    unrelated object that happens to share the preview name is never deleted.
    """
    candidate = bpy.data.objects.get(preview_name_for(obj))
    if candidate is None or candidate == obj:
        return None
    if not is_preview_object(candidate):
        return None
    return candidate


def remove_preview_object(obj):
    preview = get_preview_object(obj)
    if preview is None:
        return False
    mesh = preview.data
    bpy.data.objects.remove(preview, do_unlink=True)
    if isinstance(mesh, bpy.types.Mesh) and mesh.users == 0:
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


def _node_positions(source, unit=AM_GRID_UNIT_SIZE):
    matrix = source.matrix_world
    return [
        to_grid_coords(to_game_coords(matrix @ vertex.co), unit)
        for vertex in source.data.vertices
    ]


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
    return preview, bridges, caps, skipped, kept
