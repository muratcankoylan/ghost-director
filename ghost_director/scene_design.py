"""Validated room edits and local, immutable Blender materialization."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import struct
from ghost_director.config import REPO, Settings
from ghost_director.domain.models import Contract, Room, SceneRevision, new_id, now_iso
from ghost_director.media import process


class ApplyDesignRequest(Contract):
    room: Room


def validate_surface_factors(raw: bytes, room: Room) -> None:
    """Reject a successful export that silently dropped a shader's tint."""
    if raw[:4] != b"glTF" or len(raw) < 20:
        raise ValueError("Room compiler did not produce a GLB")
    length, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A:
        raise ValueError("Room GLB has no JSON contract")
    document = json.loads(raw[20:20 + length])
    seen = set()
    for node in document.get("nodes", []):
        surface = node.get("extras", {}).get("gdr_surface")
        if not surface:
            continue
        if surface not in type(room.design.surfaces).model_fields:
            raise ValueError("Room GLB contains an unknown surface slot")
        settings = getattr(room.design.surfaces, surface)
        srgb = [int(settings.color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        color = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in srgb] + [1]
        for primitive in document["meshes"][node["mesh"]]["primitives"]:
            material = document["materials"][primitive["material"]]["pbrMetallicRoughness"]
            actual = [*material.get("baseColorFactor", [1, 1, 1, 1]), material.get("roughnessFactor", 1), material.get("metallicFactor", 1)]
            expected = [*color, settings.roughness, settings.metalness]
            if len(actual) != len(expected) or any(not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6) for a, b in zip(actual, expected)):
                raise ValueError(f"Room GLB lost the authored {surface} material factors")
        seen.add(surface)
    required = {"walls", "ceiling", "floor", "trim"}
    if room.design.windows and room.design.details.curtains:
        required.add("curtains")
    if not required.issubset(seen):
        raise ValueError("Room GLB is missing authored surface slots")


def revise_room(scene: SceneRevision, room: Room) -> SceneRevision:
    if room.design is None:
        raise ValueError("Applying a room design requires explicit design settings")
    roots = [entity for entity in scene.entities if entity.kind == "room"]
    if len(roots) != 1:
        raise ValueError("Room design requires exactly one authored room")
    root = roots[0]
    if root.position != (0, 0, 0) or root.rotation != (0, 0, 0) or root.scale != (1, 1, 1):
        raise ValueError("Restore the room transform before changing its architecture")
    # Resizing must not move cast or silently cut through existing visible assets.
    # Rotate all bounding-box corners, rather than assuming axis-aligned furniture.
    for entity in scene.entities:
        if not entity.visible or entity.kind in {"room", "light"} or entity.state.get("included_in_entity_id"):
            continue
        rx, ry, rz = entity.rotation
        for signs in itertools.product((-1, 1), repeat=3):
            x, y = (entity.dimensions[i] * entity.scale[i] * signs[i] / 2 for i in (0, 1))
            # Asset origins are floor/base contact, unlike their X/Y center.
            z = entity.dimensions[2] * entity.scale[2] * (signs[2] + 1) / 2
            y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
            x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
            x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
            x, y, z = (value + entity.position[i] for i, value in enumerate((x, y, z)))
            if abs(x) > room.width / 2 - 0.03 or abs(y) > room.depth / 2 - 0.03 or z > room.height:
                raise ValueError(f"Room dimensions would intersect {entity.name}; move that asset first")
    revised = scene.model_copy(deep=True)
    revised.id = new_id("scene")
    revised.parent_id = scene.id
    revised.restored_from_id = None
    revised.created_at = now_iso()
    revised.room = room
    authored_room = next(entity for entity in revised.entities if entity.kind == "room")
    authored_room.dimensions = (room.width, room.depth, room.height)
    authored_room.state["material_override"] = False
    return revised


async def compile_room(store, settings: Settings, scene: SceneRevision, run_id: str) -> SceneRevision:
    """Only the caller's final CAS can make these compiled artifacts authoritative."""
    if not settings.blender:
        raise ValueError("Configure GHOST_BLENDER to apply architectural changes")
    room = next(entity for entity in scene.entities if entity.kind == "room")
    source_id = room.state.get("blend_artifact_id")
    if not source_id:
        raise ValueError("The room has no pinned Blender source")
    source_artifact = store.artifact(source_id)
    if source_artifact["kind"] != "blender_scene":
        raise ValueError("The pinned room source is not a Blender scene")
    directory = settings.root / "designs" / scene.id
    directory.mkdir(parents=True, exist_ok=False)
    scene_path = directory / "scene.json"
    scene_path.write_text(scene.model_dump_json(indent=2))
    await process(settings.blender, "--disable-autoexec", "-b", "--python", REPO / "ghost_director/blender/design_room.py", "--",
                  "--scene", scene_path, "--source", store.artifact_path(source_id), "--output", directory, timeout=180)
    receipt = json.loads((directory / "design-receipt.json").read_text())
    validate_surface_factors((directory / "room.glb").read_bytes(), scene.room)
    design_hash = hashlib.sha256(json.dumps(scene.room.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    asset_key = "room-design-" + design_hash[:16]
    artifacts = {}
    for key, name, kind, mime in (("room", "room.glb", "model", "model/gltf-binary"), ("blend", "dinner-set.blend", "blender_scene", "application/octet-stream")):
        path = directory / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt[key + "_sha256"]:
            raise ValueError("Compiled room artifact does not match its Blender receipt")
        artifacts[key] = store.publish_artifact(source_path=path, kind=kind, mime_type=mime, metadata={
            "name": "Authored room geometry" if key == "room" else "Authored room Blender source",
            "asset_key": asset_key, "scene_revision_id": scene.id, "design_sha256": design_hash,
            "source_artifact_id": source_id, "source_sha256": source_artifact["sha256"],
            "origin": "Original procedural Blender architecture; inherited packed textures",
        }, run_id=run_id)
    receipt_art = store.publish_artifact(receipt, kind="design_receipt", metadata={"name": "Blender room compilation", "scene_revision_id": scene.id}, run_id=run_id)
    room.asset_id = asset_key
    room.asset_url = artifacts["room"]["url"]
    room.state.update(artifact_id=artifacts["room"]["id"], blend_artifact_id=artifacts["blend"]["id"],
                      design_receipt_artifact_id=receipt_art["id"], asset_status="available", material_override=False)
    return scene
