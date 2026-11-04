"""Compile a validated shot to a minimal, inspectable Blender motion scene.

This script consumes data only. It never executes model-authored Python, imports
external assets, or changes a production's accepted scene. Run with --disable-autoexec.
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from motion_math import sample_motion


COLORS = ((.94, .65, .10, 1), (.14, .47, .89, 1), (.83, .20, .26, 1), (.20, .70, .46, 1), (.61, .32, .85, 1), (.10, .70, .73, 1))
CLAY = (.60, .62, .65, 1)


def cube(name, location, dimensions, color, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    obj.color = color
    if parent:
        obj.parent = parent
    return obj


def sphere(name, location, dimensions, color, parent):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=6, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    obj.color = color
    obj.parent = parent
    for face in obj.data.polygons:
        face.use_smooth = True
    return obj


def wall_x(name, x0, x1, y, bottom, top):
    if x1 > x0 and top > bottom:
        cube(name, ((x0+x1)/2, y, (bottom+top)/2), (x1-x0, .12, top-bottom), (.78, .79, .81, 1))


def room_shell(room):
    width, depth, height = room["width"], room["depth"], room["height"]
    cube("Room floor", (0, 0, -.06), (width, depth, .12), (.48, .50, .53, 1))
    # Windows and doorway retain physical openings. Ceiling is a deliberate
    # technical cutaway, recorded in the receipt rather than a changed scene.
    windows = sorted((room.get("design") or {}).get("windows", []), key=lambda item: item["center_x"])
    left = -width/2
    for window in windows:
        start, end = window["center_x"]-window["width"]/2, window["center_x"]+window["width"]/2
        wall_x("North pier", left, start, depth/2, 0, height)
        wall_x("North sill", start, end, depth/2, 0, window["sill"])
        wall_x("North header", start, end, depth/2, window["sill"]+window["height"], height)
        left = end
    wall_x("North pier", left, width/2, depth/2, 0, height)
    wall_x("South wall", -width/2, width/2, -depth/2, 0, height)
    door = (room.get("design") or {}).get("door")
    door_side = 1 if door and door.get("wall", "west") == "east" else -1
    cube("West wall" if door_side == 1 else "East wall", (-door_side*width/2, 0, height/2), (.12, depth, height), (.78, .79, .81, 1))
    if door:
        start, end = door["center_y"]-door["width"]/2, door["center_y"]+door["width"]/2
        for a, b, z, h in ((-depth/2, start, height/2, height), (end, depth/2, height/2, height), (start, end, (door["height"]+height)/2, height-door["height"])):
            if b > a and h > 0:
                cube("East wall" if door_side == 1 else "West wall", (door_side*width/2, (a+b)/2, z), (.12, b-a, h), (.78, .79, .81, 1))
    else:
        cube("West wall", (-width/2, 0, height/2), (.12, depth, height), (.78, .79, .81, 1))


def entity_proxy(entity, color):
    root = bpy.data.objects.new(entity["id"], None)
    bpy.context.collection.objects.link(root)
    root["gdr_id"] = entity["id"]
    root["representation"] = "technical_root_motion"
    width, depth, height = entity["dimensions"]
    if entity["kind"] == "actor":
        sphere(entity["id"]+" head", (0, 0, height*.87), (width*.50, depth*.60, height*.22), color, root)
        cube(entity["id"]+" torso", (0, 0, height*.57), (width*.74, depth*.56, height*.38), color, root)
        for side in (-1, 1):
            cube(entity["id"]+" leg", (side*width*.20, 0, height*.19), (width*.27, depth*.45, height*.38), color, root)
            cube(entity["id"]+" arm", (side*width*.44, 0, height*.54), (width*.14, depth*.38, height*.38), color, root)
        # A small facial direction marker conveys orientation without suggesting
        # that this proxy supplies the person's actual appearance.
        sphere(entity["id"]+" direction", (0, -depth*.31, height*.86), (width*.15, depth*.16, height*.05), (.08, .10, .14, 1), root)
    else:
        cube(entity["id"]+" bounds", (0, 0, height/2), (width, depth, height), color, root)
    root.location = entity["position"]
    root.rotation_mode = "XYZ"
    root.rotation_euler = entity["rotation"]
    root.scale = entity["scale"]
    return root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    started = time.monotonic()
    raw = args.input.read_bytes()
    payload = json.loads(raw)
    scene_data, shot, motion = payload["scene"], payload["shot"], payload["motion"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.fps = 24
    scene.render.fps_base = 1
    scene.frame_start = 1
    scene.frame_end = shot["duration"]*24
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "BOTH"
    scene.display.shading.background_type = "WORLD"
    scene.world = bpy.data.worlds.new("Technical neutral background")
    scene.world.color = (.14, .15, .17)
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = False
    room_shell(scene_data["room"])
    roots, mapping = {}, []
    identity_ids = sorted(entity["id"] for entity in scene_data["entities"] if entity["kind"] == "actor")
    identity_ids += sorted(entity["id"] for entity in scene_data["entities"] if entity["kind"] not in {"actor", "room", "light"} and entity.get("state", {}).get("reference_pin"))
    tracked = {track["entity_id"] for track in motion["tracks"]}
    for entity in scene_data["entities"]:
        if not entity.get("visible", True) or entity["kind"] in {"room", "light"} or entity.get("state", {}).get("included_in_entity_id"):
            continue
        if entity["id"] in identity_ids:
            index = identity_ids.index(entity["id"])
            color = COLORS[index] if index < len(COLORS) else (*colorsys.hsv_to_rgb((index*.61803398875) % 1, .75, .85), 1)
        else:
            color = (.92, .40, .13, 1) if entity["id"] in tracked else CLAY
        roots[entity["id"]] = entity_proxy(entity, color)
        mapping.append({"entity_id": entity["id"], "kind": entity["kind"], "technical_color": list(color), "dimensions_m": entity["dimensions"], "scale": entity["scale"], "reference_pin": entity.get("state", {}).get("reference_pin")})
    record = next(item for item in scene_data["cameras"] if item["id"] == shot["camera_id"])
    data = bpy.data.cameras.new(record["id"])
    camera = bpy.data.objects.new(record["id"], data)
    bpy.context.collection.objects.link(camera)
    camera.rotation_mode = "QUATERNION"
    data.sensor_width = record["sensor_width_mm"]
    data.sensor_fit = "HORIZONTAL"
    data.clip_start = record["near"]
    data.clip_end = record["far"]
    scene.camera = camera
    samples = []
    for frame in range(1, scene.frame_end+1):
        seconds = (frame-1)/24
        state = sample_motion(motion, seconds)
        cam = state["camera"]
        camera.location = cam["position"]
        camera.rotation_quaternion = (Vector(cam["target"])-camera.location).to_track_quat("-Z", "Y")
        data.lens = cam["focal_length_mm"]
        camera.keyframe_insert(data_path="location", frame=frame)
        camera.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        data.keyframe_insert(data_path="lens", frame=frame)
        for entity in state["entities"]:
            root = roots[entity["entity_id"]]
            root.location = entity["position"]
            root.rotation_euler = entity["rotation"]
            root.keyframe_insert(data_path="location", frame=frame)
            root.keyframe_insert(data_path="rotation_euler", frame=frame)
        if frame in {1, (scene.frame_end+1)//2, scene.frame_end}:
            samples.append({"frame": frame, **state})
    for owner in [camera, data, *roots.values()]:
        action = owner.animation_data.action if owner.animation_data else None
        if action:
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in bag.fcurves:
                            for point in curve.keyframe_points:
                                point.interpolation = "LINEAR"
    # Read back evaluated Blender values. Input echoes alone are not evidence that
    # the authored animation actually follows the plan.
    max_error = 0
    for sample in samples:
        scene.frame_set(sample["frame"])
        expected = sample["camera"]
        direction = camera.rotation_quaternion @ Vector((0, 0, -1))
        wanted = (Vector(expected["target"])-Vector(expected["position"])).normalized()
        errors = [abs(a-b) for a, b in zip(camera.location, expected["position"])]
        errors += [abs(a-b) for a, b in zip(direction, wanted)]
        errors += [abs(data.lens-expected["focal_length_mm"])]
        actual = {"position": list(camera.location), "direction": list(direction), "focal_length_mm": data.lens}
        evaluated_entities = []
        for expected_entity in sample["entities"]:
            root = roots[expected_entity["entity_id"]]
            errors += [abs(a-b) for a, b in zip(root.location, expected_entity["position"])]
            errors += [abs(a-b) for a, b in zip(root.rotation_euler, expected_entity["rotation"])]
            evaluated_entities.append({"entity_id": expected_entity["entity_id"], "position": list(root.location), "rotation": list(root.rotation_euler)})
        sample["evaluated_blender"] = {"camera": actual, "entities": evaluated_entities}
        max_error = max(max_error, *errors)
    if max_error > .0001:
        raise ValueError("Evaluated Blender animation differs from the shot plan")
    # Evaluate the saved file at the first frame so opening it is immediately useful.
    scene.frame_set(1)
    args.output.mkdir(parents=True, exist_ok=True)
    frames = args.output / "frames"
    frames.mkdir(exist_ok=True)
    scene.render.filepath = str(frames / "frame-######")
    scene["ghost_shot_id"] = shot["id"]
    scene["ghost_scene_revision_id"] = scene_data["id"]
    scene["ghost_motion_plan"] = json.dumps(motion, sort_keys=True)
    blend = args.output / "technical-motion.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.render.render(animation=True)
    receipt = {
        "version": 1, "shot_id": shot["id"], "scene_revision_id": scene_data["id"],
        "source_sha256": hashlib.sha256(raw).hexdigest(), "blend_sha256": hashlib.sha256(blend.read_bytes()).hexdigest(),
        "blender_version": bpy.app.version_string, "engine": scene.render.engine,
        "width": 640, "height": 360, "fps": 24, "frame_count": scene.frame_end,
        "duration_seconds": shot["duration"], "last_sample_seconds": (scene.frame_end-1)/24,
        "coordinate_frame": "right-handed Z-up meters; XYZ Euler radians",
        "timeline": motion, "sampled_states": samples, "entity_mapping": mapping,
        "evaluation": {"passed": True, "sample_count": len(samples), "max_channel_error": max_error, "tolerance": .0001},
        "planned_terminal_state": sample_motion(motion, shot["duration"]),
        "limitations": ["Root translation/rotation only; no articulated gestures, facial performance, cloth, or contact solver.",
                        "Actor proxies and prop bounding boxes convey blocking, not approved appearance.",
                        "Room ceiling is omitted as a technical cutaway.",
                        "Video frame n samples time (n-1)/24; the terminal control key lies one frame beyond the last sample."],
        "render_seconds": round(time.monotonic()-started, 3),
    }
    (args.output / "blender-motion-receipt.json").write_text(json.dumps(receipt, indent=2, allow_nan=False))
    print(json.dumps({"status": "complete", "frame_count": scene.frame_end, "render_seconds": receipt["render_seconds"]}))


if __name__ == "__main__":
    main()
