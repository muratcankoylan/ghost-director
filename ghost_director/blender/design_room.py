"""Compile editable architecture from a pinned packed scene; no external assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import struct
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_set import box, cylinder, export_group, finish, linear, pipe


def surface_material(source, name, settings):
    source.use_fake_user = True  # Keep the unmodified texture graph through subsequent edits.
    mat = source.copy()
    mat.name = "GDR_surface_" + name
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        raise ValueError(f"Pinned {name} material lacks a Principled shader")
    for socket_name, field in (("Base Color", "color"), ("Roughness", "roughness"), ("Metallic", "metalness")):
        socket = bsdf.inputs[socket_name]
        value = linear(settings[field]) if field == "color" else settings[field]
        if socket.is_linked:
            source_socket = socket.links[0].from_socket
            node = mat.node_tree.nodes.new("ShaderNodeMixRGB" if field == "color" else "ShaderNodeMath")
            if field == "color":
                node.blend_type = "MULTIPLY"
                node.inputs[0].default_value = 1
                node.inputs[2].default_value = value
                mat.node_tree.links.new(source_socket, node.inputs[1])
            else:
                node.operation = "MULTIPLY"
                node.inputs[1].default_value = value
                mat.node_tree.links.new(source_socket, node.inputs[0])
            mat.node_tree.links.new(node.outputs[0], socket)
        else:
            socket.default_value = value
    mat.diffuse_color = linear(settings["color"])
    return mat


def export_room(root, path, original_surfaces, settings):
    """Keep the GLB and Cycles material contracts equal without baking textures.

    Blender's exporter ignores our color-multiply shader node. Export untouched
    texture graphs, then set glTF's standard linear PBR factors explicitly. The
    authored .blend retains its full shader graph and original packed textures.
    """
    export_materials = {name: source.copy() for name, source in original_surfaces.items()}
    for name, mat in export_materials.items():
        mat.name = "GDR_export_surface_" + name
        mat.use_fake_user = False
    names = {mat.name: name for name, mat in export_materials.items()}
    saved = []
    try:
        for obj in root.children_recursive:
            surface = obj.get("gdr_surface")
            if obj.type == "MESH" and surface in export_materials:
                for index, material in enumerate(obj.data.materials):
                    saved.append((obj, index, material))
                    obj.data.materials[index] = export_materials[surface]
        export_group(root, path)
    finally:
        for obj, index, material in saved:
            obj.data.materials[index] = material
        for material in export_materials.values():
            bpy.data.materials.remove(material)
    raw = path.read_bytes()
    chunks, offset = [], 12
    while offset < len(raw):
        length, kind = struct.unpack_from("<II", raw, offset)
        data = raw[offset + 8:offset + 8 + length]
        if kind == 0x4E4F534A:
            document = json.loads(data)
            for material in document.get("materials", []):
                surface = names.get(material.get("name"))
                if surface:
                    values = settings[surface]
                    pbr = material.setdefault("pbrMetallicRoughness", {})
                    pbr.update(baseColorFactor=list(linear(values["color"])), roughnessFactor=values["roughness"], metallicFactor=values["metalness"])
                    material["name"] = "GDR_surface_" + surface
            data = json.dumps(document, separators=(",", ":")).encode()
            data += b" " * (-len(data) % 4)
        chunks.append(struct.pack("<II", len(data), kind) + data)
        offset += length + 8
    body = b"".join(chunks)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body)


def build(room, palette):
    w, d, h = (room[key] for key in ("width", "depth", "height"))
    design = room["design"]
    style = design["style"]
    surfaces = design["surfaces"]

    def piece(name, position, dimensions, surface, bevel=.004, tile=2):
        obj = box(name, position, dimensions, palette[surface], bevel=bevel, tile=tile)
        if surface in surfaces:
            obj["gdr_surface"] = surface
            obj["gdr_surface_material"] = surfaces[surface]
        return obj

    piece("floor", (0, 0, -.025), (w, d, .05), "floor")
    piece("ceiling", (0, 0, h + .035), (w, d, .07), "ceiling")
    piece("south wall", (0, -d / 2 - .1, h / 2), (w + .4, .2, h), "walls")
    door = design["door"]
    door_side = 1 if door.get("wall", "west") == "east" else -1
    door_x = door_side * (w / 2 + .1)
    piece("west wall" if door_side == 1 else "east wall", (-door_x, 0, h / 2), (.2, d, h), "walls")
    door_start, door_end = door["center_y"] - door["width"] / 2, door["center_y"] + door["width"] / 2
    for y0, y1 in ((-d / 2, door_start), (door_end, d / 2)):
        piece("entry wall", (door_x, (y0 + y1) / 2, h / 2), (.2, y1 - y0, h), "walls")
    piece("door lintel", (door_x, door["center_y"], (h + door["height"]) / 2), (.2, door["width"], h - door["height"]), "walls")
    frame_width = .025 if style == "minimal" else .055 if style == "noir" else .045
    for y in (door_start, door_end):
        piece("door casing", (door_side * (w / 2 - .015), y, door["height"] / 2), (.06, frame_width, door["height"]), "trim")
    piece("door casing header", (door_side * (w / 2 - .015), door["center_y"], door["height"]), (.06, door["width"] + frame_width, frame_width), "trim")
    last = -w / 2
    windows = sorted(design["windows"], key=lambda window: window["center_x"])
    for window in windows:
        x, width, bottom = (window[key] for key in ("center_x", "width", "sill"))
        a, b, top = x - width / 2, x + width / 2, bottom + window["height"]
        piece("window pier", ((last + a) / 2, d / 2 + .1, h / 2), (a - last, .2, h), "walls")
        piece("window sill wall", (x, d / 2 + .1, bottom / 2), (width, .2, bottom), "walls")
        piece("window header", (x, d / 2 + .1, (h + top) / 2), (width, .2, h - top), "walls")
        for edge in ((a, b) if style == "minimal" else (a, x, b)):
            piece("window mullion", (edge, d / 2, (bottom + top) / 2), (frame_width, .1, top - bottom + .06), "trim")
        for z in (bottom, top):
            piece("window frame", (x, d / 2, z), (width + .08, .12, frame_width), "trim")
        piece("window ledge", (x, d / 2 - .09, bottom), (width + .12, .25, .04), "trim")
        box("exterior blue night", (x, d / 2 + 1.2, (bottom + top) / 2), (width + 1, .03, top - bottom + 1), palette["night"], 0)
        if design["details"]["curtains"]:
            for edge, sign in ((a - .08, 1), (b + .08, -1)):
                verts, faces = [], []
                nx, ny = 32, 28
                curtain_width = min(.38, width / 4)
                for iz in range(ny + 1):
                    z = .12 + (min(h - .05, top + .18) - .12) * iz / ny
                    for ix in range(nx + 1):
                        u = ix / nx
                        verts.append((edge + sign * u * curtain_width, d / 2 - .17 + .045 * math.sin(u * math.pi * 12), z))
                for iz in range(ny):
                    for ix in range(nx):
                        k = iz * (nx + 1) + ix
                        faces.append((k, k + 1, k + nx + 2, k + nx + 1))
                mesh = bpy.data.meshes.new("woven curtain")
                mesh.from_pydata(verts, [], faces)
                mesh.update()
                obj = bpy.data.objects.new("woven curtain", mesh)
                bpy.context.collection.objects.link(obj)
                mesh.materials.append(palette["curtains"])
                for poly in mesh.polygons:
                    poly.use_smooth = True
                obj["gdr_surface"] = "curtains"
                obj["gdr_surface_material"] = surfaces["curtains"]
        last = b
    piece("window pier", ((last + w / 2) / 2, d / 2 + .1, h / 2), (w / 2 - last, .2, h), "walls")
    trim_height = .055 if style == "minimal" else .18 if style == "noir" else .13
    for y in (-d / 2 + .025, d / 2 - .025):
        piece("baseboard", (0, y, trim_height / 2), (w, .045, trim_height), "trim")
    for side in (-1, 1):
        spans = ((-d / 2, door_start), (door_end, d / 2)) if side == door_side else ((-d / 2, d / 2),)
        for start, end in spans:
            piece("baseboard", (side * (w / 2 - .025), (start + end) / 2, trim_height / 2), (.045, end - start, trim_height), "trim")
    if style == "noir":
        # Authored panel mouldings are geometry, not a renderer-only style flag.
        for x in range(math.ceil(-w / 2 + .4), math.floor(w / 2 - .4) + 1):
            piece("wall panel stile", (x, -d / 2 + .03, .6), (.035, .03, .9), "trim")
        piece("wall panel rail", (0, -d / 2 + .03, 1.08), (w, .03, .035), "trim")
    if design["details"]["sideboard"]:
        cx, cy = w / 2 - 1.2, -d / 2 + .3
        box("walnut sideboard carcass", (cx, cy, .51), (1.8, .48, .72), palette["walnut"], .008)
        box("sideboard stone top", (cx, cy, .886), (1.84, .51, .032), palette["ceramic"], .006)
        for dx in (-.55, 0, .55):
            box("sideboard door", (cx + dx, cy + .255, .51), (.53, .027, .65), palette["walnut"], .003)
            box("brass pull", (cx + dx + .18, cy + .28, .55), (.024, .025, .11), palette["brass"], .004)
        for dx in (-.73, .73):
            for dy in (-.16, .16):
                box("credenza foot", (cx + dx, cy + dy, .075), (.055, .055, .15), palette["walnut"], .004)
    if design["details"]["chandelier"]:
        center = Vector((min(2.15, w / 2 - .65), .15, h - .93))
        cylinder("chandelier stem", .018, .8, center + Vector((0, 0, .48)), palette["brass"])
        for i in range(6):
            angle = i * math.tau / 6
            radial = Vector((math.cos(angle), math.sin(angle), 0))
            p = center + radial * .42
            pipe("chandelier arm", [center, center + radial * .18 + Vector((0, 0, -.12)), p + Vector((0, 0, -.06)), p + Vector((0, 0, .06))], .011, palette["brass"])
            cylinder("bulb socket", .024, .055, p + Vector((0, 0, .065)), palette["brass"])
            bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=12, radius=.035, location=p + Vector((0, 0, .13)))
            bulb = finish(bpy.context.object, "warm bulb", palette["bulb"])
            bulb.scale.z = 1.5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    data = json.loads(args.scene.read_text())
    room_entity = next(entity for entity in data["entities"] if entity["kind"] == "room")
    bpy.ops.wm.open_mainfile(filepath=str(args.source))
    scene = bpy.context.scene
    originals = {name: bpy.data.materials.get("GHOST_" + name) for name in ("plaster", "floor", "trim", "unbleached_linen", "walnut", "stoneware", "aged_brass", "filament", "window_night")}
    if any(mat is None for mat in originals.values()):
        raise ValueError("Pinned Blender source is missing original packed set materials")
    palette = {}
    surface_sources = {}
    for surface, name in (("walls", "plaster"), ("ceiling", "plaster"), ("floor", "floor"), ("trim", "trim"), ("curtains", "unbleached_linen")):
        surface_sources[surface] = originals[name]
        palette[surface] = surface_material(originals[name], surface, data["room"]["design"]["surfaces"][surface])
    for target, source in (("walnut", "walnut"), ("ceramic", "stoneware"), ("brass", "aged_brass"), ("bulb", "filament"), ("night", "window_night")):
        palette[target] = originals[source]
        originals[source].use_fake_user = True
    previous = next((obj for obj in scene.objects if obj.get("gdr_id") == room_entity["id"]), None)
    if previous is None:
        raise ValueError("Pinned Blender source is missing the authored room root")
    for child in list(previous.children_recursive):
        bpy.data.objects.remove(child, do_unlink=True)
    bpy.data.objects.remove(previous, do_unlink=True)
    before = set(scene.objects)
    build(data["room"], palette)
    created = set(scene.objects) - before
    root = bpy.data.objects.new(room_entity["id"], None)
    scene.collection.objects.link(root)
    root["gdr_id"] = room_entity["id"]
    for obj in created:
        if obj.parent not in created:
            obj.parent = root
    args.output.mkdir(parents=True, exist_ok=True)
    export_room(root, args.output / "room.glb", surface_sources, data["room"]["design"]["surfaces"])
    root.location, root.rotation_euler, root.scale = room_entity["position"], room_entity["rotation"], room_entity["scale"]
    scene.view_settings.exposure = data["room"]["design"]["exposure"]
    # Preserve packed textures only; no path from the original workstation is needed.
    for image in bpy.data.images:
        if image.source == "FILE" and image.has_data and not image.packed_file:
            image.pack()
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / "dinner-set.blend"), compress=True)
    receipt = {
        "scene_revision_id": data["id"], "blender_version": bpy.app.version_string,
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "scene_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
        "room": data["room"], "room_mesh_count": sum(obj.type == "MESH" for obj in created),
        "surface_slots": sorted(data["room"]["design"]["surfaces"]),
        "room_sha256": hashlib.sha256((args.output / "room.glb").read_bytes()).hexdigest(),
        "blend_sha256": hashlib.sha256((args.output / "dinner-set.blend").read_bytes()).hexdigest(),
    }
    (args.output / "design-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
