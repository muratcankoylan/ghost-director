"""Normalize a static reference reconstruction to canonical meters and floor contact.

The operator supplies height and yaw. Neither photograph scale nor subject
orientation is silently inferred. Textures stay attached to the original mesh.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


def bounds(meshes):
    corners = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    if not corners or not all(math.isfinite(value) for point in corners for value in point):
        raise ValueError("Reconstruction has no finite mesh bounds")
    return Vector([min(point[i] for point in corners) for i in range(3)]), Vector([max(point[i] for point in corners) for i in range(3)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--height", type=float, required=True)
    parser.add_argument("--yaw", type=float, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not math.isfinite(args.height) or not .01 <= args.height <= 20 or not math.isfinite(args.yaw) or not -360 <= args.yaw <= 360:
        raise ValueError("Explicit finite height (0.01–20m) and yaw (-360–360 degrees) are required")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input))
    roots = [obj for obj in bpy.context.scene.objects if obj.parent is None]
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if any(obj.type == "ARMATURE" for obj in bpy.context.scene.objects) or bpy.data.actions:
        raise ValueError("The reference placement path supports static meshes; rigs and animation require a separate evaluated import")
    if sum(len(obj.data.vertices) for obj in meshes) > 2_000_000:
        raise ValueError("Reconstruction exceeds the 2 million vertex interactive import limit")
    rotation = Matrix.Rotation(math.radians(args.yaw), 4, "Z")
    for obj in roots:
        obj.matrix_world = rotation @ obj.matrix_world
    bpy.context.view_layer.update()
    low, high = bounds(meshes)
    dimensions = high - low
    if min(dimensions) <= 1e-8:
        raise ValueError("Reconstruction has degenerate geometry")
    scale = args.height / dimensions.z
    origin = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
    transform = Matrix.Scale(scale, 4) @ Matrix.Translation(-origin)
    for obj in roots:
        obj.matrix_world = transform @ obj.matrix_world
    bpy.context.view_layer.update()
    low, high = bounds(meshes)
    # Meshes generated from one image are static evidence, not measured anatomy.
    for obj in meshes:
        obj["gdr_reference_reconstruction"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(args.output), export_format="GLB", export_apply=True, export_yup=True, export_extras=True, export_cameras=False, export_lights=False, export_animations=False)
    receipt = {
        "schema_version": 1,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "blender_version": bpy.app.version_string,
        "normalizer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "height_m": args.height,
        "yaw_correction_degrees": args.yaw,
        "uniform_scale": scale,
        "bounds_canonical": {"min": list(low), "max": list(high)},
        "dimensions": list(high - low),
        "origin": "XY bounding-box center; base contact Z=0",
        "canonical_coordinates": "right-handed Z-up; meters",
        "file_coordinates": "glTF Y-up; meters",
        "rigged": False,
        "mesh_count": len(meshes),
        "vertices": sum(len(obj.data.vertices) for obj in meshes),
        "polygons": sum(len(obj.data.polygons) for obj in meshes),
        "material_count": len(bpy.data.materials),
        "texture_count": len(bpy.data.images),
        "quality_status": "Reconstructed static geometry; inspect likeness and hidden surfaces. Height and yaw are operator-authored, not inferred measurements.",
    }
    args.output.with_suffix(".normalization.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
