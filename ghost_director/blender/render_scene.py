"""Render a revision from the cached authored set, without rebuilding its assets."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_set import camera_from_record, import_actor, linear
from lighting import apply_scene_lighting


def apply_material(group, override):
    for obj in group.children_recursive:
        if obj.type != "MESH": continue
        obj.data = obj.data.copy()
        for index, old in enumerate(obj.data.materials):
            mat = old.copy(); obj.data.materials[index] = mat
            bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
            if not bsdf: continue
            base = bsdf.inputs["Base Color"]
            if base.is_linked:
                source = base.links[0].from_socket
                multiply = mat.node_tree.nodes.new("ShaderNodeMixRGB"); multiply.blend_type = "MULTIPLY"
                multiply.inputs[0].default_value = 1; multiply.inputs[2].default_value = linear(override["color"])
                mat.node_tree.links.new(source, multiply.inputs[1]); mat.node_tree.links.new(multiply.outputs[0], base)
            else: base.default_value = linear(override["color"])
            for socket_name, field in (("Roughness", "roughness"), ("Metallic", "metalness")):
                socket = bsdf.inputs[socket_name]
                if socket.is_linked:
                    source = socket.links[0].from_socket
                    multiply = mat.node_tree.nodes.new("ShaderNodeMath"); multiply.operation = "MULTIPLY"
                    multiply.inputs[1].default_value = override[field]
                    mat.node_tree.links.new(source, multiply.inputs[0]); mat.node_tree.links.new(multiply.outputs[0], socket)
                else: socket.default_value = override[field]


def main():
    parser = argparse.ArgumentParser()
    repo = Path(__file__).resolve().parents[2]
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, default=repo / "productions/ghost-director-hackathon/assets")
    parser.add_argument("--camera-id")
    parser.add_argument("--samples", type=int, default=48)
    parser.add_argument("--resolution", type=int, default=1280)
    parser.add_argument("--allow-missing-actors", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if not 1 <= args.samples <= 4096 or not 64 <= args.resolution <= 8192:
        raise ValueError("Render quality exceeds the supported sample/resolution bounds")
    scene_data = json.loads(args.scene.read_text())
    bpy.ops.wm.open_mainfile(filepath=str(args.asset_root / "dinner-set.blend"))
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type == "CAMERA": bpy.data.objects.remove(obj, do_unlink=True)
    roots = {obj.get("gdr_id"): obj for obj in scene.objects if obj.get("gdr_id") and obj.type != "LIGHT"}
    mapping_path = args.asset_root / "asset-map.json"
    pinned_assets = json.loads(mapping_path.read_text()) if mapping_path.is_file() else {}
    wanted = {entity["id"] for entity in scene_data["entities"]}
    for id, root in roots.items():
        if id not in wanted:
            for child in list(root.children_recursive): bpy.data.objects.remove(child, do_unlink=True)
            bpy.data.objects.remove(root, do_unlink=True)
    for entity in scene_data["entities"]:
        if entity["kind"] == "light": continue
        # Every pinned non-room asset, including newly reconstructed props, is
        # imported from the frozen map rather than stale geometry in the room.
        if entity["kind"] == "actor" or entity.get("asset_id") in pinned_assets:
            prior = roots.get(entity["id"])
            if prior:
                for child in list(prior.children_recursive): bpy.data.objects.remove(child, do_unlink=True)
                bpy.data.objects.remove(prior, do_unlink=True)
            root = import_actor(entity, args.asset_root)
        else:
            root = roots.get(entity["id"])
        if root is None:
            if entity.get("visible", True) and (entity["kind"] != "actor" or not args.allow_missing_actors):
                raise RuntimeError(f"Missing visible entity geometry: {entity['id']}")
            continue
        root.location = entity["position"]; root.rotation_euler = entity["rotation"]; root.scale = entity["scale"]
        for obj in [root, *root.children_recursive]: obj.hide_render = not entity.get("visible", True)
        if entity.get("state", {}).get("material_override") and entity.get("material"):
            apply_material(root, entity["material"])
    light_receipts = apply_scene_lighting(scene_data["entities"])
    scene.render.engine = "CYCLES"; scene.cycles.samples = args.samples; scene.cycles.use_denoising = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = (scene_data.get("room", {}).get("design") or {}).get("exposure", 0)
    scene.render.resolution_x = args.resolution; scene.render.resolution_percentage = 100
    args.output.mkdir(parents=True, exist_ok=True)
    renders = []
    for record in scene_data["cameras"]:
        if args.camera_id and record["id"] != args.camera_id: continue
        scene.camera = camera_from_record(record)
        scene.render.resolution_y = round(args.resolution / record["aspect_ratio"])
        path = args.output / f"{record['id']}.png"
        scene.render.filepath = str(path)
        started = time.monotonic(); bpy.ops.render.render(write_still=True)
        renders.append({"camera_id": record["id"], "camera": record, "resolved_focus_distance_m": scene.camera.data.dof.focus_distance, "depth_of_field": scene.camera.data.dof.use_dof, "aperture_fstop": scene.camera.data.dof.aperture_fstop, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "width": args.resolution, "height": scene.render.resolution_y, "render_seconds": round(time.monotonic() - started, 3)})
    if not renders: raise ValueError("No requested camera exists in this scene revision")
    receipt = {"scene_id": scene_data["id"], "scene_file_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(), "blender_version": bpy.app.version_string, "renderer_profile": "cycles-agx-v3", "quality": {"samples": args.samples, "resolution_x": args.resolution, "denoising": True, "view_transform": scene.view_settings.view_transform, "look": scene.view_settings.look, "exposure_stops": scene.view_settings.exposure}, "blend_source_sha256": hashlib.sha256((args.asset_root / "dinner-set.blend").read_bytes()).hexdigest(), "asset_mapping_sha256": hashlib.sha256((args.asset_root / "asset-map.json").read_bytes()).hexdigest(), "lights": light_receipts, "renders": renders}
    (args.output / "render-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__": main()
