"""Run inside Blender: inventory a source without changing its objects or file."""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def inventory():
    objects = []
    for obj in bpy.context.scene.objects:
        if obj.type not in {"MESH", "CAMERA", "LIGHT"}:
            continue
        row = {
            "name": obj.name, "type": obj.type,
            "id": obj.get("gdr_id"), "class": obj.get("gdr_class"),
            "position": list(obj.matrix_world.translation),
            "rotation": list(obj.matrix_world.to_euler("XYZ")),
            "dimensions": list(obj.dimensions),
            "materials": [m.name for m in obj.data.materials] if obj.type == "MESH" else [],
        }
        if obj.type == "CAMERA":
            row["lens_mm"] = obj.data.lens
            row["target_direction"] = list(obj.matrix_world.to_quaternion() @ Vector((0, 0, -1)))
        objects.append(row)
    return {
        "source": bpy.data.filepath,
        "blender": bpy.app.version_string,
        "units": bpy.context.scene.unit_settings.system,
        "objects": objects,
        "images": [{"name": im.name, "path": im.filepath, "packed": bool(im.packed_file)} for im in bpy.data.images],
    }


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    result = json.dumps(inventory(), indent=2)
    if args:
        path = Path(args[0]); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result + "\n")
    else:
        print(result)
