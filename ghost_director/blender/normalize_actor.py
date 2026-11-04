"""Normalize an inspected seated mesh to meters, floor contact, +Y forward.

The yaw is an explicit inspection decision, not a runtime orientation guess.
The origin uses the torso's horizontal center so it can share the chair anchor.
"""
import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--yaw", type=float, default=180)
    parser.add_argument("--optimize-textures", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    roots = [o for o in bpy.context.scene.objects if not o.parent]
    rotation = Matrix.Rotation(math.radians(args.yaw), 4, "Z")
    for obj in roots: obj.matrix_world = rotation @ obj.matrix_world
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    vertices = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    low = Vector([min(v[i] for v in vertices) for i in range(3)])
    high = Vector([max(v[i] for v in vertices) for i in range(3)])
    scale = args.height / (high.z - low.z)
    # Torso band excludes forward knees/hands, which bias a full bounding box pivot.
    torso = [v for v in vertices if .55 < (v.z - low.z) / (high.z - low.z) < .70]
    if len(torso) < 20: raise ValueError("Insufficient torso geometry for an explicit seated anchor")
    origin = Vector((statistics.median(v.x for v in torso), statistics.median(v.y for v in torso), low.z))
    transform = Matrix.Scale(scale, 4) @ Matrix.Translation(-origin)
    for obj in roots: obj.matrix_world = transform @ obj.matrix_world
    bpy.context.view_layer.update()
    for obj in meshes:
        for poly in obj.data.polygons: poly.use_smooth = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    texture_receipts = []
    if args.optimize_textures:
        texture_dir = args.output.parent / (args.output.stem + "-textures")
        texture_dir.mkdir(parents=True, exist_ok=True)
        for index, im in enumerate(list(bpy.data.images)):
            # Imported GLB images decode lazily; has_data is false until size/pixels
            # are read, despite valid embedded bytes being available.
            if not im.size[0] or not im.size[1]: continue
            _ = im.pixels[0]
            is_color = im.colorspace_settings.name == "sRGB"
            max_size = 2048 if is_color else 1024
            ratio = min(1, max_size / max(im.size))
            old_size = list(im.size)
            im.scale(max(1, round(im.size[0] * ratio)), max(1, round(im.size[1] * ratio)))
            im.file_format = "JPEG" if is_color else "PNG"
            path = texture_dir / f"{index}-{'color' if is_color else 'data'}.{'jpg' if is_color else 'png'}"
            im.filepath_raw = str(path); im.save()
            replacement = bpy.data.images.load(str(path), check_existing=False)
            replacement.colorspace_settings.name = im.colorspace_settings.name; replacement.pack()
            for mat in bpy.data.materials:
                if not mat.use_nodes: continue
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image == im: node.image = replacement
            texture_receipts.append({"input_dimensions": old_size, "output_dimensions": list(replacement.size), "color_space": replacement.colorspace_settings.name, "path": str(path.name), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    bpy.ops.export_scene.gltf(filepath=str(args.output), export_format="GLB", export_apply=True, export_yup=True, export_extras=True, export_cameras=False, export_lights=False)
    vertices = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    low = [min(v[i] for v in vertices) for i in range(3)]
    high = [max(v[i] for v in vertices) for i in range(3)]
    report = {"input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(), "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "bytes": args.output.stat().st_size, "height_m": args.height, "yaw_correction_deg": args.yaw, "uniform_scale": scale, "bounds_canonical": {"min": low, "max": high}, "dimensions": [b-a for a,b in zip(low,high)], "origin": "floor below torso median; seated chair anchor", "forward": "+Y canonical", "file_coordinates": "glTF Y-up meters", "rigged": False, "textures": texture_receipts, "quality_status": "prototype; inspect in actual room before acceptance"}
    args.output.with_suffix(".normalization.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__": main()
