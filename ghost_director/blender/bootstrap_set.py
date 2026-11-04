"""Compile a standalone original room, without downloaded textures or characters."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_set import material, camera_from_record
from design_room import build, export_room, surface_material
from lighting import apply_scene_lighting


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    data = json.loads(args.scene.read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    originals = {
        "plaster": material("GHOST_plaster", "#ffffff", .8),
        "floor": material("GHOST_floor", "#ffffff", .7),
        "trim": material("GHOST_trim", "#ffffff", .46),
        "unbleached_linen": material("GHOST_unbleached_linen", "#ffffff", .92),
        "walnut": material("GHOST_walnut", "#574332", .5),
        "stoneware": material("GHOST_stoneware", "#eee9da", .29),
        "aged_brass": material("GHOST_aged_brass", "#a58452", .3, .85),
        "filament": material("GHOST_filament", "#ffd3a1", .2, emission=4),
        "window_night": material("GHOST_window_night", "#213549", .8, emission=.2),
    }
    for item in originals.values():
        item.use_fake_user = True
    sources, palette = {}, {}
    for surface, name in (("walls", "plaster"), ("ceiling", "plaster"), ("floor", "floor"), ("trim", "trim"), ("curtains", "unbleached_linen")):
        sources[surface] = originals[name]
        palette[surface] = surface_material(originals[name], surface, data["room"]["design"]["surfaces"][surface])
    for target, name in (("walnut", "walnut"), ("ceramic", "stoneware"), ("brass", "aged_brass"), ("bulb", "filament"), ("night", "window_night")):
        palette[target] = originals[name]
    build(data["room"], palette)
    created = list(bpy.context.scene.objects)
    room = next(entity for entity in data["entities"] if entity["kind"] == "room")
    root = bpy.data.objects.new(room["id"], None)
    bpy.context.scene.collection.objects.link(root)
    root["gdr_id"] = room["id"]
    for obj in created:
        if not obj.parent:
            obj.parent = root
    args.output.mkdir(parents=True, exist_ok=True)
    export_room(root, args.output / "room.glb", sources, data["room"]["design"]["surfaces"])
    for camera in data["cameras"]:
        bpy.context.scene.camera = camera_from_record(camera)
    apply_scene_lighting(data["entities"])
    bpy.context.scene.view_settings.view_transform = "AgX"
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "dinner-set.blend"), compress=True)
    receipt = {
        "schema_version": 1, "starter_kind": "original_empty_room", "blender_version": bpy.app.version_string,
        "scene_id": data["id"], "scene_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
        "room_sha256": hashlib.sha256((args.output / "room.glb").read_bytes()).hexdigest(),
        "blend_sha256": hashlib.sha256((args.output / "dinner-set.blend").read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mesh_count": sum(obj.type == "MESH" for obj in created), "external_assets": [], "paid_calls": 0,
        "limitations": "Original editable architecture with simple PBR surfaces. No approved reference identity, actors, generated video, photorealism or layout inference is implied.",
    }
    (args.output / "bootstrap-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
