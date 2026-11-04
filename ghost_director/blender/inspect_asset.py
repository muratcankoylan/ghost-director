"""Render a generated mesh from four views; never equate importability to quality."""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector, Matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=float, default=1.78)
    parser.add_argument("--rotate-x", type=float, default=0)
    parser.add_argument("--rotate-z", type=float, default=0)
    parser.add_argument("--closeup", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    correction = Matrix.Rotation(math.radians(args.rotate_z), 4, "Z") @ Matrix.Rotation(math.radians(args.rotate_x), 4, "X")
    for obj in bpy.context.scene.objects:
        if obj.parent is None: obj.matrix_world = correction @ obj.matrix_world
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes: raise RuntimeError("GLB contains no mesh")
    verts = [o.matrix_world @ Vector(p) for o in meshes for p in o.bound_box]
    low = Vector([min(v[i] for v in verts) for i in range(3)])
    high = Vector([max(v[i] for v in verts) for i in range(3)])
    dimensions = high - low
    scale = args.height / dimensions.z
    group = bpy.data.objects.new("Inspection normalization", None)
    bpy.context.collection.objects.link(group)
    roots = [o for o in bpy.context.scene.objects if o != group and o.parent is None]
    for obj in roots: obj.parent = group
    group.scale = (scale,) * 3
    group.location = (-((low.x + high.x) / 2) * scale, -((low.y + high.y) / 2) * scale, -low.z * scale)
    report = {"source": str(args.input), "orientation_correction_deg": {"x": args.rotate_x, "z": args.rotate_z}, "input_dimensions_z_up": list(dimensions), "inspection_height_m": args.height, "meshes": len(meshes), "polygons": sum(len(o.data.polygons) for o in meshes), "materials": len(bpy.data.materials), "images": len(bpy.data.images), "armatures": sum(o.type == "ARMATURE" for o in bpy.context.scene.objects), "actions": len(bpy.data.actions), "quality_status": "requires visual inspection; no acceptance implied"}
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"; scene.cycles.samples = 24; scene.cycles.use_denoising = True
    scene.render.resolution_x = 640; scene.render.resolution_y = 800; scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("Neutral review studio"); world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.35, .35, .35, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .6; scene.world = world
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    mat = bpy.data.materials.new("Neutral floor"); mat.diffuse_color = (.32, .32, .32, 1); floor.data.materials.append(mat)
    for loc, energy, size in [((-3, -4, 5), 350, 4), ((3, 2, 4), 200, 3)]:
        data = bpy.data.lights.new("Review softbox", "AREA"); data.energy = energy; data.size = size
        light = bpy.data.objects.new("Review softbox", data); scene.collection.objects.link(light); light.location = loc
        light.rotation_euler = (Vector((0, 0, .9)) - light.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("Review camera"); camera_data.lens = 55
    camera = bpy.data.objects.new("Review camera", camera_data); scene.collection.objects.link(camera); scene.camera = camera
    for label, angle in [("front", 0), ("three-quarter", 45), ("side", 90), ("rear", 180)]:
        angle = math.radians(angle)
        camera.location = (math.sin(angle) * 3.8, -math.cos(angle) * 3.8, 1.05)
        camera.rotation_euler = (Vector((0, 0, .93)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(args.output / f"{label}.png")
        bpy.ops.render.render(write_still=True)
    if args.closeup:
        camera_data.lens = 85
        camera.location = (0, -.95, args.height - .14)
        camera.rotation_euler = (Vector((0, 0, args.height - .14)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(args.output / "face.png")
        bpy.ops.render.render(write_still=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__": main()
