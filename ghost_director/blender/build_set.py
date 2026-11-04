"""Deterministic original set geometry. Run with Blender --background --python.

Assets export in local glTF Y-up coordinates. Entity transforms remain metric
Z-up in the JSON source. No original mannequin or third-party character is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lighting import apply_scene_lighting


def arguments():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    repo = Path(__file__).resolve().parents[2]
    parser.add_argument("--scene", type=Path, default=repo / "productions/ghost-director-hackathon/assets/scene.template.json")
    parser.add_argument("--source", type=Path, default=repo / "productions/ghost-director-dining-room/blends/r04-blocking.blend")
    parser.add_argument("--output", type=Path, default=repo / "productions/ghost-director-hackathon/assets")
    parser.add_argument("--asset-root", type=Path, default=repo / "productions/ghost-director-hackathon/assets")
    parser.add_argument("--require-actors", action="store_true")
    parser.add_argument("--asset-version", default="v1", choices=["v1", "v2"])
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--resolution", type=int, default=960)
    return parser.parse_args(args)


def linear(hex_color):
    values = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values) + (1,)


def material(name, color, roughness=.5, metalness=0, emission=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = linear(color)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metalness
    if emission:
        bsdf.inputs["Emission Color"].default_value = linear(color)
        bsdf.inputs["Emission Strength"].default_value = emission
    mat.diffuse_color = linear(color)
    return mat


def load_materials(source, out, version="v1"):
    """Reuse named packed CC0 texture components; author the shading graphs here."""
    names = {
        "oak": ("oak_veneer_03_Diffuse", "oak_veneer_03_Rough", "oak_veneer_03_nor_gl"),
        "walnut": ("black_walnut_veneer_03_Diffuse", "black_walnut_veneer_03_Rough", "black_walnut_veneer_03_nor_gl"),
        "plaster": ("beige_wall_001_Diffuse", "beige_wall_001_Rough", "beige_wall_001_nor_gl"),
        "floor": ("laminate_floor_03_Diffuse", "laminate_floor_03_Rough", "laminate_floor_03_nor_gl"),
        "linen": ("fabric_pattern_05_col_01", "fabric_pattern_05_Rough", "fabric_pattern_05_nor_gl"),
    }
    wanted = {item for row in names.values() for item in row}
    with bpy.data.libraries.load(str(source), link=False) as (src, dst):
        missing = wanted - set(src.images)
        if missing:
            raise RuntimeError(f"Required packed textures absent: {sorted(missing)}")
        dst.images = sorted(wanted)
    texture_dir = out / ("textures" if version == "v1" else f"textures-{version}")
    texture_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    mats = {}
    for key, image_names in names.items():
        mat = material(f"GHOST_{key}", "#ffffff")
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        for index, name in enumerate(image_names):
            im = bpy.data.images[name]
            im.colorspace_settings.name = "sRGB" if index == 0 else "Non-Color"
            resolution = (512 if key == "linen" else 1024) if index == 0 else (256 if key == "linen" else 512)
            im.scale(resolution, resolution)
            im.file_format = "JPEG" if index == 0 else "PNG"
            path = texture_dir / f"{key}-{'color' if index == 0 else 'roughness' if index == 1 else 'normal'}.{'jpg' if index == 0 else 'png'}"
            im.filepath_raw = str(path)
            im.save()
            # A loaded packed image retains its original bytes after scale/save.
            # Reopen the written image so glTF cannot embed the old high-res pack.
            im = bpy.data.images.load(str(path), check_existing=False)
            im.colorspace_settings.name = "sRGB" if index == 0 else "Non-Color"
            im.pack()
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = im
            if index == 2:
                normal = mat.node_tree.nodes.new("ShaderNodeNormalMap")
                normal.inputs["Strength"].default_value = .55
                mat.node_tree.links.new(node.outputs["Color"], normal.inputs["Color"])
                mat.node_tree.links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
            else:
                mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color" if index == 0 else "Roughness"])
            receipts.append({"path": str(path.relative_to(out)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_image": name, "source_blend": str(source.name), "license": "CC0-1.0", "provider": "Poly Haven", "source_url": f"https://polyhaven.com/a/{name.split('_Diffuse')[0].split('_Rough')[0].split('_nor_gl')[0].split('_col_01')[0]}"})
        mats[key] = mat
    mats.update({
        "ceramic": material("GHOST_stoneware", "#eee9da", .29),
        "green": material("GHOST_cup_glaze", "#47664b", .22),
        "coffee": material("GHOST_tea", "#34251c", .12),
        "brass": material("GHOST_aged_brass", "#a58452", .3, .85),
        "trim": material("GHOST_trim", "#ddd6c7", .46),
        "cork": material("GHOST_cork", "#9d7950", .88),
        "bulb": material("GHOST_filament", "#ffd3a1", .2, emission=4),
        "night": material("GHOST_window_night", "#1b2b3b", .8, emission=.2),
        "sheer": material("GHOST_unbleached_linen", "#d4cbb5", .92),
    })
    return mats, receipts


def finish(obj, name, mat, bevel=0):
    obj.name = name
    obj["gdr_component"] = name
    if mat:
        obj.data.materials.append(mat)
    if bevel:
        mod = obj.modifiers.new("Manufactured edge radius", "BEVEL")
        mod.width = bevel; mod.segments = 3
        mod.limit_method = "ANGLE"
        obj.modifiers.new("Weighted face normals", "WEIGHTED_NORMAL")
    return obj


def box(name, location, dimensions, mat, bevel=.004, tile=1):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    # World-size planar UVs make material density independent of object dimensions.
    uv = obj.data.uv_layers.active.data
    for poly in obj.data.polygons:
        axes = (1, 2) if abs(poly.normal.x) > .5 else (0, 2) if abs(poly.normal.y) > .5 else (0, 1)
        for loop in poly.loop_indices:
            p = obj.data.vertices[obj.data.loops[loop].vertex_index].co
            uv[loop].uv = (p[axes[0]] / tile, p[axes[1]] / tile)
    return finish(obj, name, mat, bevel)


def cylinder(name, radius, depth, location, mat, vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
    return finish(bpy.context.object, name, mat, min(.002, depth / 5))


def lathe(name, profile, mat, segments=64):
    verts = [(r * math.cos(2 * math.pi * j / segments), r * math.sin(2 * math.pi * j / segments), z) for r, z in profile for j in range(segments)]
    faces = []
    for i in range(len(profile) - 1):
        for j in range(segments):
            a = i * segments + j; b = i * segments + (j + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces); mesh.update()
    obj = bpy.data.objects.new(name, mesh); bpy.context.collection.objects.link(obj)
    finish(obj, name, mat)
    for poly in mesh.polygons: poly.use_smooth = True
    return obj


def pipe(name, points, radius, mat):
    curve = bpy.data.curves.new(name, "CURVE"); curve.dimensions = "3D"
    curve.bevel_depth = radius; curve.bevel_resolution = 3; curve.resolution_u = 16
    spline = curve.splines.new("BEZIER"); spline.bezier_points.add(len(points) - 1)
    for p, co in zip(spline.bezier_points, points):
        p.co = co; p.handle_left_type = "AUTO"; p.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve); bpy.context.collection.objects.link(obj)
    curve.materials.append(mat)
    return obj


def room(m, dimensions):
    w, d, h = dimensions
    box("floor", (0, 0, -.025), (w, d, .05), m["floor"], tile=2)
    box("ceiling", (0, 0, h + .035), (w, d, .07), m["plaster"], tile=2)
    box("south wall", (0, -d / 2 - .1, h / 2), (w + .4, .2, h), m["plaster"], tile=2)
    box("east wall", (w / 2 + .1, 0, h / 2), (.2, d, h), m["plaster"], tile=2)
    # Entry opening in -X wall, preserved from original measured production.
    for y0, y1 in [(-d / 2, -.20), (.76, d / 2)]:
        box("entry wall", (-w / 2 - .1, (y0 + y1) / 2, h / 2), (.2, y1 - y0, h), m["plaster"], tile=2)
    box("door lintel", (-w / 2 - .1, .28, (h + 2.5) / 2), (.2, .96, h - 2.5), m["plaster"])
    # Window wall built as segments; clear holes rather than dark planes on a wall.
    windows = [(-3.4, -1.6, .65, 2.45), (1.4, 4.0, .65, 2.55)]
    for a, b in [(-w / 2, -3.4), (-1.6, 1.4), (4.0, w / 2)]:
        box("window pier", ((a + b) / 2, d / 2 + .1, h / 2), (b - a, .2, h), m["plaster"], tile=2)
    for index, (a, b, bottom, top) in enumerate(windows):
        x = (a + b) / 2
        box("window sill wall", (x, d / 2 + .1, bottom / 2), (b - a, .2, bottom), m["plaster"])
        box("window header", (x, d / 2 + .1, (h + top) / 2), (b - a, .2, h - top), m["plaster"])
        for edge in (a, x, b): box("oak window mullion", (edge, d / 2, (bottom + top) / 2), (.045, .1, top - bottom + .06), m["walnut"])
        for z in (bottom, top): box("oak window frame", (x, d / 2, z), (b - a + .08, .12, .055), m["walnut"])
        box("window ledge", (x, d / 2 - .09, bottom), (b - a + .12, .25, .04), m["oak"])
        box("exterior blue night", (x, d / 2 + 1.2, (bottom + top) / 2), (b - a + 1, .03, top - bottom + 1), m["night"], 0)
        # Gathered curtains are actual corrugated cloth surfaces.
        for edge, sign in ((a - .08, 1), (b + .08, -1)):
            verts = []; faces = []; nx = 32; ny = 28
            for iz in range(ny + 1):
                z = .12 + (top + .18 - .12) * iz / ny
                for ix in range(nx + 1):
                    u = ix / nx; xcur = edge + sign * u * .38
                    ycur = d / 2 - .17 + .045 * math.sin(u * math.pi * 12) + .015 * math.sin(iz / ny * math.pi)
                    verts.append((xcur, ycur, z + .012 * math.cos(u * math.pi * 12) * (1 - iz / ny)))
            for iz in range(ny):
                for ix in range(nx):
                    k = iz * (nx + 1) + ix; faces.append((k, k + 1, k + nx + 2, k + nx + 1))
            mesh = bpy.data.meshes.new("woven curtain"); mesh.from_pydata(verts, [], faces); mesh.update()
            uv = mesh.uv_layers.new()
            for poly in mesh.polygons:
                poly.use_smooth = True
                for loop in poly.loop_indices:
                    p = mesh.vertices[mesh.loops[loop].vertex_index].co; uv.data[loop].uv = (p.x * 3, p.z * 3)
            obj = bpy.data.objects.new("woven curtain", mesh); bpy.context.collection.objects.link(obj)
            mesh.materials.append(m["sheer"])
    for y in (-d / 2 + .025, d / 2 - .025): box("baseboard", (0, y, .065), (w, .045, .13), m["trim"], .006)
    for x in (-w / 2 + .025, w / 2 - .025): box("baseboard", (x, 0, .065), (.045, d, .13), m["trim"], .006)
    # An original credenza retains the older/newer wood distinction.
    box("walnut sideboard carcass", (3.8, -2.80, .51), (1.8, .48, .72), m["walnut"], .008)
    box("sideboard stone top", (3.8, -2.80, .886), (1.84, .51, .032), m["ceramic"], .006)
    for x in (3.25, 3.8, 4.35):
        box("sideboard door", (x, -2.545, .51), (.53, .027, .65), m["walnut"], .003)
        box("brass pull", (x + .18, -2.52, .55), (.024, .025, .11), m["brass"], .004)
    for x in (3.07, 4.53):
        for y in (-2.96, -2.64): box("credenza tapered foot", (x, y, .075), (.055, .055, .15), m["walnut"], .004)
    # Six-arm practical with actual bulb geometry; lighting is separately controlled.
    center = Vector((2.15, .15, 2.22))
    cylinder("chandelier stem", .018, .8, (2.15, .15, 2.70), m["brass"])
    for i in range(6):
        angle = i * math.tau / 6
        radial = Vector((math.cos(angle), math.sin(angle), 0))
        p = center + radial * .42
        pipe("chandelier arm", [center, center + radial * .18 + Vector((0, 0, -.12)), p + Vector((0, 0, -.06)), p + Vector((0, 0, .06))], .011, m["brass"])
        cylinder("bulb socket", .024, .055, p + Vector((0, 0, .065)), m["brass"])
        bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=12, radius=.035, location=p + Vector((0, 0, .13)))
        bulb = finish(bpy.context.object, "warm bulb", m["bulb"])
        bulb.scale.z = 1.5


def table(m):
    box("oak tabletop", (0, 0, .735), (2.2, 1, .05), m["oak"], .018)
    for x in (-.9, .9):
        for y in (-.34, .34): box("table leg", (x, y, .355), (.075, .075, .71), m["oak"], .008)
    for y in (-.36, .36): box("table apron", (0, y, .665), (1.9, .035, .09), m["oak"], .003)
    for x in (-.94, .94): box("table apron", (x, 0, .665), (.035, .72, .09), m["oak"], .003)


def chair(m):
    # Local +Y is the seated person's forward direction; origin is floor contact.
    box("shaped seat", (0, 0, .445), (.49, .47, .045), m["walnut"], .025)
    box("linen seat pad", (0, .015, .474), (.43, .40, .025), m["linen"], .012, tile=.35)
    for x in (-.19, .19):
        for y in (-.18, .18): box("chair leg", (x, y, .22), (.035, .035, .44), m["walnut"], .006)
        box("back stile", (x, -.205, .64), (.032, .036, .56), m["walnut"], .006)
    box("upper back rail", (0, -.205, .88), (.45, .045, .07), m["walnut"], .018)
    for x in (-.10, 0, .10): box("back spindle", (x, -.205, .685), (.023, .026, .34), m["walnut"], .008)
    for x in (-.19, .19): box("side stretcher", (x, 0, .18), (.025, .39, .025), m["walnut"], .005)


def cup(m):
    lathe("glazed green cup", [(0, 0), (.035, 0), (.040, .004), (.045, .10), (.043, .108), (.039, .108), (.037, .101), (.032, .013), (0, .013)], m["green"])
    pipe("cup handle", [(0.04, 0, .087), (.074, 0, .088), (.084, 0, .060), (.074, 0, .031), (.039, 0, .031)], .007, m["green"])
    cylinder("tea surface", .035, .001, (0, 0, .061), m["coffee"])


def plate(m):
    lathe("stoneware plate", [(0, .004), (.074, .004), (.105, .010), (.132, .022), (.14, .023), (.14, .018), (.107, .005), (.077, 0), (0, 0)], m["ceramic"])


def export_group(group, path):
    bpy.ops.object.select_all(action="DESELECT")
    group.select_set(True)
    for obj in group.children_recursive: obj.select_set(True)
    bpy.context.view_layer.objects.active = group
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_apply=True, export_yup=True, export_extras=True, export_cameras=False, export_lights=False, export_materials="EXPORT")


def make_group(id, builder, path):
    previous = set(bpy.context.scene.objects)
    builder()
    children = set(bpy.context.scene.objects) - previous
    group = bpy.data.objects.new(id, None); bpy.context.collection.objects.link(group)
    for obj in children: obj.parent = group
    group["gdr_asset_id"] = id
    export_group(group, path)
    return group


def camera_from_record(record):
    data = bpy.data.cameras.new(record["id"])
    obj = bpy.data.objects.new(record["id"], data); bpy.context.collection.objects.link(obj)
    obj.location = record["position"]
    obj.rotation_euler = (Vector(record["target"]) - obj.location).to_track_quat("-Z", "Y").to_euler()
    data.lens = record["focal_length_mm"]; data.sensor_width = record["sensor_width_mm"]
    data.sensor_fit = "HORIZONTAL"; data.clip_start = record["near"]; data.clip_end = record["far"]
    data.dof.use_dof = record.get("depth_of_field", False)
    data.dof.aperture_fstop = record.get("aperture_fstop", 4)
    # Null is an explicit focus-on-target policy, evaluated in canonical meters.
    distance = record.get("focus_distance_m")
    data.dof.focus_distance = distance if distance is not None else (Vector(record["target"]) - obj.location).length
    return obj


def import_actor(entity, asset_root):
    map_path = asset_root / "asset-map.json"
    mappings = json.loads(map_path.read_text()) if map_path.exists() else {}
    relative = mappings.get(entity.get("asset_id"))
    if not relative:
        return None
    path = (asset_root / relative).resolve()
    if not path.is_relative_to(asset_root.resolve()) or path.suffix.lower() != ".glb":
        raise ValueError("Actor assets must be production-local GLBs")
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = set(bpy.context.scene.objects) - before
    group = bpy.data.objects.new(entity["id"], None); bpy.context.collection.objects.link(group)
    for obj in imported:
        if obj.parent not in imported: obj.parent = group
    group["gdr_id"] = entity["id"]
    group.location = entity["position"]; group.rotation_euler = entity["rotation"]; group.scale = entity["scale"]
    for obj in [group, *group.children_recursive]: obj.hide_render = not entity.get("visible", True)
    return group


def main():
    args = arguments(); args.output.mkdir(parents=True, exist_ok=True)
    scene_data = json.loads(args.scene.read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"; sc.unit_settings.scale_length = 1
    sc.render.engine = "CYCLES"; sc.cycles.samples = args.samples; sc.cycles.use_denoising = True
    sc.render.resolution_x = args.resolution; sc.render.resolution_y = round(args.resolution * 9 / 16); sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "AgX"
    mats, texture_receipts = load_materials(args.source, args.output, args.asset_version)
    dimensions = [scene_data["room"][key] for key in ("width", "depth", "height")]
    builders = {
        f"room-{args.asset_version}": lambda: room(mats, dimensions), f"table-{args.asset_version}": lambda: table(mats),
        f"chair-{args.asset_version}": lambda: chair(mats), f"cup-{args.asset_version}": lambda: cup(mats),
        f"plate-{args.asset_version}": lambda: plate(mats),
        f"coaster-{args.asset_version}": lambda: cylinder("cork coaster", .07, .006, (0, 0, .003), mats["cork"]),
        f"napkin-{args.asset_version}": lambda: box("folded linen", (0, 0, .004), (.20, .12, .008), mats["linen"], .003, tile=.25),
    }
    prototypes = {}
    for id, builder in builders.items():
        group = make_group(id, builder, args.output / f"{id}.glb")
        prototypes[id] = group
    # Instantiate the same local geometry using canonical entity transforms.
    instantiated = []
    for entity in scene_data["entities"]:
        asset_id = entity.get("asset_id")
        if asset_id not in prototypes: continue
        proto = prototypes[asset_id]
        group = bpy.data.objects.new(entity["id"], None); sc.collection.objects.link(group)
        group["gdr_id"] = entity["id"]
        for child in proto.children_recursive:
            copy = child.copy(); sc.collection.objects.link(copy); copy.parent = group
        group.location = entity["position"]; group.rotation_euler = entity["rotation"]; group.scale = entity["scale"]
        for obj in [group, *group.children_recursive]: obj.hide_render = not entity.get("visible", True)
        instantiated.append(group)
    for proto in prototypes.values():
        for child in list(proto.children_recursive): bpy.data.objects.remove(child, do_unlink=True)
        bpy.data.objects.remove(proto, do_unlink=True)
    missing_actors = []
    for entity in scene_data["entities"]:
        if entity["kind"] == "actor" and entity.get("visible", True):
            if import_actor(entity, args.asset_root) is None: missing_actors.append(entity["id"])
    if args.require_actors and missing_actors:
        raise RuntimeError(f"Reference render rejected: missing actor assets {missing_actors}")
    world = bpy.data.worlds.new("Night ambient"); world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.11, .15, .22, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .3; sc.world = world
    apply_scene_lighting(scene_data["entities"])
    proof = {"blender_version": bpy.app.version_string, "resolution": [sc.render.resolution_x, sc.render.resolution_y], "cameras": []}
    points = [[1.1, -.2, .76], [2.15, .49, .87], [3.3, .2, 1.25], [1.6, .6, .9], [2.8, -.3, .3]]
    for record in scene_data["cameras"]:
        cam = camera_from_record(record); sc.camera = cam; bpy.context.view_layer.update()
        pixels = []
        for p in points:
            ndc = world_to_camera_view(sc, cam, Vector(p))
            pixels.append([ndc.x * sc.render.resolution_x, (1 - ndc.y) * sc.render.resolution_y])
        proof["cameras"].append({"camera": record, "points_world": points, "pixels_top_left": pixels})
        if args.render:
            sc.render.filepath = str(args.output / f"{record['id']}-room.png")
            bpy.ops.render.render(write_still=True)
    (args.output / "camera-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    sc.camera = bpy.data.objects[scene_data["cameras"][0]["id"]]
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "dinner-set.blend"))
    assets = [{"id": id, "path": f"{id}.glb", "sha256": hashlib.sha256((args.output / f"{id}.glb").read_bytes()).hexdigest(), "bytes": (args.output / f"{id}.glb").stat().st_size, "geometry": "original parameterized geometry", "license": "project-authored; owner license pending", "local_coordinates": "glTF Y-up; meters; origin at floor/contact plane"} for id in builders]
    actor_manifest = args.asset_root / "actor-manifest.json"
    if actor_manifest.exists(): assets.extend(json.loads(actor_manifest.read_text()).get("assets", []))
    manifest = {"schema_version": "1.0", "status": "production prototype; visual acceptance pending", "blender_version": bpy.app.version_string, "assets": assets, "textures": texture_receipts, "texture_license_url": "https://polyhaven.com/license", "source_scene": str(args.scene.name)}
    (args.output / "asset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"assets": len(assets), "output": str(args.output), "rendered": args.render, "actors": sum(e["kind"] == "actor" and e.get("visible", True) for e in scene_data["entities"]) - len(missing_actors)}))


if __name__ == "__main__":
    main()
