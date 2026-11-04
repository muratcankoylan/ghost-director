"""Create an original empty directing set without demo bundles or model calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ghost_director.config import REPO
from ghost_director.domain.models import Camera, Entity, Room, RoomDesign, SceneRevision, new_id
from ghost_director.jobs.store import Conflict
from ghost_director.media import process
from ghost_director.scene_design import validate_surface_factors


def starter_scene() -> SceneRevision:
    room = Room(design=RoomDesign(style="minimal"))
    room.design.details.sideboard = False
    room.design.details.chandelier = False
    room.design.surfaces.walls.color = "#ded6c9"
    room.design.surfaces.ceiling.color = "#f5f1e9"
    room.design.surfaces.floor.color = "#a58c68"
    return SceneRevision(
        id=new_id("scene"), name="Untitled set", room=room,
        entities=[
            Entity(id="SET-ROOM-001", name="Original starter room", kind="room", dimensions=(room.width, room.depth, room.height), asset_id="starter-room", state={"quality_status": "Original architectural starting point; add and approve visual identities", "material_override": False}),
            Entity(id="SET-KEY-001", name="Large soft key", kind="light", position=(1, -1.4, 2.8), state={"light_type": "area", "intensity": 3, "soft_size_m": 3, "target": [0, .7, 1]}),
            Entity(id="SET-FILL-001", name="Window fill", kind="light", position=(-2, 2, 2.6), state={"light_type": "area", "intensity": 2, "soft_size_m": 2.5, "target": [0, 0, 1]}),
        ],
        cameras=[Camera(id="camera-starter", name="Wide master", position=(3.8, -2.4, 1.6), target=(0, 1, 1.25), focal_length_mm=24)],
    )


async def bootstrap_set(store, settings) -> dict:
    """Refuse existing authored assets/state; partial failures remain inspectable."""
    if store.scene() is not None:
        raise Conflict("This production already has a scene. Choose a new GHOST_PRODUCTION_ROOT for an empty set.")
    target = settings.root / "assets"
    if target.exists():
        raise Conflict("The production already has an assets folder; bootstrap never overwrites existing files.")
    if not settings.blender or not Path(settings.blender).is_file():
        raise ValueError("Install Blender and set GHOST_BLENDER to its executable before creating an empty set.")
    scene = starter_scene()
    # Stage all outputs together, so a failed compiler never presents a partial
    # assets folder as a usable scene. The work directory is useful failure evidence.
    staging = settings.root / "bootstrap-work" / new_id("build")
    staging.mkdir(parents=True, exist_ok=False)
    template = staging / "scene.template.json"
    template.write_text(scene.model_dump_json(indent=2))
    await process(settings.blender, "--disable-autoexec", "-b", "--python", REPO / "ghost_director/blender/bootstrap_set.py", "--", "--scene", template, "--output", staging, timeout=180)
    receipt = json.loads((staging / "bootstrap-receipt.json").read_text())
    for filename, key in (("room.glb", "room_sha256"), ("dinner-set.blend", "blend_sha256")):
        path = staging / filename
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt[key]:
            raise Conflict("Starter set output does not match its Blender compilation receipt")
    validate_surface_factors((staging / "room.glb").read_bytes(), scene.room)
    manifest = {"assets": [{"id": "starter-room", "path": "room.glb", "sha256": receipt["room_sha256"], "geometry": "Original procedural architecture", "license": "Project-authored geometry; follows the project source license", "quality_status": "Original starting geometry; not a reconstruction of approved location references"}]}
    (staging / "asset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # Another bootstrap winner or an operator-created directory must not be replaced.
    with store._transaction() as db:
        if store._setting(db, "accepted_head") is not None or target.exists():
            raise Conflict("Another bootstrap created the scene or assets folder; inspect that result.")
        staging.rename(target)
    return {"scene_id": scene.id, "assets": str(target), "receipt": receipt, "paid_calls": 0}
