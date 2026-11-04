"""Actual mesh regression: run with Blender --background --python this_file.

Raycasts inspect both authored and technical geometry, including the threshold,
so moving the doorway cannot leave a hidden full wall or baseboard in its path.
"""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ghost_director" / "blender"))
from design_room import build
from render_motion import room_shell


def hit(x_direction, y, z):
    return bpy.context.scene.ray_cast(
        bpy.context.evaluated_depsgraph_get(), Vector((0, y, z)), Vector((x_direction, 0, 0)), distance=3
    )[0]


results = []
for side in (None, "west", "east"):
    door = {"center_y": .4, "width": 1.2, "height": 2.3}
    if side:
        door["wall"] = side
    room = {"width": 4, "depth": 4, "height": 3, "design": {
        "style": "minimal", "windows": [], "door": door,
        "surfaces": {name: {"color": "#ffffff", "roughness": .7, "metalness": 0} for name in ("walls", "floor", "ceiling", "trim")},
        "details": {"curtains": False, "sideboard": False, "chandelier": False},
    }}
    direction = 1 if side == "east" else -1
    for representation in ("authored", "technical"):
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        if representation == "authored":
            material = bpy.data.materials.new("Door geometry verification")
            build(room, {name: material for name in room["design"]["surfaces"]})
        else:
            room_shell(room)
        bpy.context.view_layer.update()
        assert not hit(direction, .4, 1), (representation, side, "doorway blocked")
        assert not hit(direction, .4, .03), (representation, side, "threshold blocked")
        assert hit(-direction, .4, 1), (representation, side, "opposite wall missing")
        assert hit(direction, .4, 2.7), (representation, side, "lintel missing")
        assert hit(direction, -1.2, 1), (representation, side, "doorway pier missing")
        results.append({"wall": side or "legacy-default-west", "representation": representation, "raycasts_passed": 5})

print("DOOR_GEOMETRY_VERIFIED " + json.dumps({"blender_version": bpy.app.version_string, "cases": results}))
