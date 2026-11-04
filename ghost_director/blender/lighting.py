"""Deterministic scene-light interpretation for the authored soft-light set.

Scene intensity is an authored strength, not a shared radiometric unit. Three.js
uses point/directional previews; Cycles uses finite area sources for soft shadows.
These fixed gains preserve the baseline look while making every edit repeatable.
"""
import math

import bpy
from mathutils import Vector


LIGHT_GAINS = {"point": 1.4, "directional": 120.0, "area": 1.0}


def apply_scene_lighting(entities):
    # Replace the complete authored lighting rig, including legacy untagged lights.
    for obj in list(bpy.context.scene.objects):
        if obj.type == "LIGHT": bpy.data.objects.remove(obj, do_unlink=True)
    records = []
    for entity in entities:
        if entity["kind"] != "light": continue
        state = entity.get("state", {})
        kind = state.get("light_type", "point")
        if kind not in LIGHT_GAINS: raise ValueError(f"Unsupported light type: {kind}")
        intensity = float(state.get("intensity", 35 if kind == "point" else 1.4))
        if not math.isfinite(intensity) or intensity < 0: raise ValueError("Light intensity must be finite and nonnegative")
        energy = intensity * LIGHT_GAINS[kind]
        color = (entity.get("material") or {}).get("color", "#ffd1a2" if kind == "point" else "#aecbff")
        rgb = [int(color.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4)]
        # Blender light colors are linear, matching Three.Color's sRGB conversion.
        rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        data = bpy.data.lights.new(entity["id"], "AREA")
        data.energy = energy; data.color = rgb; data.shape = "DISK"
        data.size = float(state.get("soft_size_m", 1.3 if kind == "point" else 2.0))
        if not math.isfinite(data.size) or data.size <= 0:
            raise ValueError("Light source size must be finite and positive")
        obj = bpy.data.objects.new(entity["id"], data); bpy.context.collection.objects.link(obj)
        obj["gdr_id"] = entity["id"]; obj.location = entity["position"]
        obj.scale = entity["scale"]
        target = state.get("target")
        if target is None and kind == "point": target = [obj.location.x, obj.location.y, 0]
        if target is not None:
            delta = Vector(target) - obj.location
            if delta.length < 1e-6: raise ValueError("Light target must differ from its position")
            obj.rotation_euler = delta.to_track_quat("-Z", "Y").to_euler()
        else: obj.rotation_euler = entity["rotation"]
        obj.hide_render = not entity.get("visible", True)
        records.append({"entity_id": entity["id"], "authored_type": kind, "cycles_type": "AREA", "intensity": intensity, "energy_w": energy, "position": list(obj.location), "target": target, "visible": not obj.hide_render, "color_linear": rgb, "soft_size_m": data.size})
    # The fixed bounce is the room's ambient support, not a user-editable entity.
    data = bpy.data.lights.new("room bounce", "AREA"); data.energy = 90
    data.color = (1, .84, .65); data.shape = "DISK"; data.size = 3
    obj = bpy.data.objects.new("room bounce", data); bpy.context.collection.objects.link(obj)
    obj.location = (-2, -1, 2.4)
    obj.rotation_euler = (Vector((2.15, .15, 1)) - obj.location).to_track_quat("-Z", "Y").to_euler()
    return records
