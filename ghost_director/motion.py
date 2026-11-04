"""Validated, local-only motion materialization. Never publishes a scene revision."""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from ghost_director.config import REPO, Settings
from ghost_director.domain.models import SceneRevision, ShotPlan, new_id
from ghost_director.media import process, video_evidence
from ghost_director.motion_models import MotionPlan


def _close(first, second, tolerance=1e-5):
    return len(first) == len(second) and all(abs(a-b) <= tolerance for a, b in zip(first, second))


def validate_motion(scene: SceneRevision, shot: ShotPlan) -> MotionPlan:
    """Bind a root-motion plan to the exact authoritative start state."""
    if scene.id != shot.scene_revision_id:
        raise ValueError("Motion must use the shot's pinned scene revision")
    camera = next((item for item in scene.cameras if item.id == shot.camera_id), None)
    if camera is None:
        raise ValueError("Motion camera is absent from the scene")
    if not math.isclose(camera.aspect_ratio, 16/9, rel_tol=1e-5):
        raise ValueError("The validated clay renderer currently requires a 16:9 camera")
    if getattr(shot, "fps", 24) != 24:
        raise ValueError("The validated clay renderer currently requires 24 FPS")
    raw = getattr(shot, "motion", None)
    if raw is None:
        raise ValueError("Review a motion plan before rendering its technical reference")
    # Revalidate even when a caller used model_copy(update=...), which bypasses validators.
    motion = MotionPlan.model_validate(raw.model_dump(mode="json") if isinstance(raw, MotionPlan) else raw)
    first = motion.camera_keyframes[0]
    if not _close(first.position, camera.position) or not _close(first.target, camera.target) or not math.isclose(first.focal_length_mm, camera.focal_length_mm, abs_tol=1e-5):
        raise ValueError("Camera motion must start at the saved camera position, target, and lens")
    if motion.camera_keyframes[-1].time_seconds != shot.duration:
        raise ValueError("Camera motion must end at the shot duration")
    entities = {item.id: item for item in scene.entities}
    for track in motion.tracks:
        entity = entities.get(track.entity_id)
        if entity is None:
            raise ValueError(f"Unknown motion entity: {track.entity_id}")
        if not entity.visible or entity.kind not in {"actor", "prop", "set"}:
            raise ValueError(f"Motion requires a visible actor, prop, or set entity: {entity.id}")
        if entity.state.get("included_in_entity_id"):
            raise ValueError(f"Move the owning asset rather than its included geometry: {entity.id}")
        first = track.keyframes[0]
        if not _close(first.position, entity.position) or not _close(first.rotation, entity.rotation):
            raise ValueError(f"Motion must start at the saved transform: {entity.id}")
        if track.keyframes[-1].time_seconds != shot.duration:
            raise ValueError(f"Entity motion must end at the shot duration: {entity.id}")
    return motion


async def render_motion(store, settings: Settings, scene: SceneRevision, shot: ShotPlan, run_id: str) -> dict:
    """Bake structured transforms to Blender, validate/decode, then publish immutable outputs.

    The caller serializes Blender operations. Each attempt has an isolated directory;
    a failed render may leave diagnostic files but never a successful receipt.
    """
    motion = validate_motion(scene, shot)
    if not settings.blender or not Path(settings.blender).is_file():
        raise ValueError("Configure an executable GHOST_BLENDER to render technical motion")
    started = time.monotonic()
    script = REPO / "ghost_director/blender/render_motion.py"
    source_hashes = {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in (script, REPO / "ghost_director/motion_math.py", REPO / "ghost_director/motion_models.py")
    }
    payload = {"scene": scene.model_dump(mode="json"), "shot": shot.model_dump(mode="json"), "motion": motion.model_dump(mode="json")}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    source_digest = hashlib.sha256(payload_bytes).hexdigest()
    directory = settings.root / "motion" / new_id("render")
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "motion-input.json"
    source.write_bytes(payload_bytes)
    store.append_event("motion.render_started", {"shot_id": shot.id, "scene_revision_id": scene.id, "source_sha256": source_digest, "duration_seconds": shot.duration, "fps": 24}, run_id=run_id)
    await process(settings.blender, "--disable-autoexec", "-b", "--python", script, "--", "--input", source, "--output", directory, timeout=600)
    receipt_path = directory / "blender-motion-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("source_sha256") != source_digest or receipt.get("shot_id") != shot.id or receipt.get("scene_revision_id") != scene.id:
        raise ValueError("Blender motion receipt does not match its requested source")
    if receipt.get("frame_count") != shot.duration*24 or receipt.get("fps") != 24:
        raise ValueError("Blender motion receipt has an unexpected timeline")
    blend = directory / "technical-motion.blend"
    if hashlib.sha256(blend.read_bytes()).hexdigest() != receipt.get("blend_sha256"):
        raise ValueError("Blender motion source does not match its receipt")
    video = directory / "technical-motion.mp4"
    await process("ffmpeg", "-v", "error", "-y", "-framerate", "24", "-start_number", "1", "-i", directory / "frames/frame-%06d.png",
                  "-frames:v", str(shot.duration*24), "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", video, timeout=120)
    probe, evidence = await video_evidence(video, directory / "evidence")
    if probe["width"] != 640 or probe["height"] != 360 or abs(probe["duration"] - shot.duration) > 1/24 or probe["frame_rate"] != "24/1":
        raise ValueError("Encoded motion reference differs from the validated timeline")
    metadata = {"scene_revision_id": scene.id, "shot_id": shot.id, "source_sha256": source_digest,
                "representation": "technical_motion", "provider": "local", "model": "blender", "motion_scope": "camera and rigid entity transforms"}
    artifacts = {}
    for key, path, kind, mime, name in (
        ("video", video, "video", "video/mp4", "Technical motion reference"),
        ("blend", blend, "blender_scene", "application/octet-stream", "Technical motion Blender scene"),
        ("poster", evidence[0]["path"], "image", "image/jpeg", "Technical motion first frame"),
    ):
        artifacts[key] = store.publish_artifact(source_path=path, kind=kind, mime_type=mime, metadata={**metadata, "name": name, **(probe if key == "video" else {})}, run_id=run_id)
    receipt.update(video=probe, video_sha256=artifacts["video"]["sha256"], video_artifact_id=artifacts["video"]["id"], blend_artifact_id=artifacts["blend"]["id"],
                   compiler_sha256=source_hashes, elapsed_seconds=round(time.monotonic()-started, 3), transport="local-blender-subprocess", mcp_connected=False)
    artifacts["receipt"] = store.publish_artifact(receipt, kind="motion_receipt", metadata={**metadata, "name": "Technical motion validation receipt"}, run_id=run_id)
    store.append_event("motion.render_completed", {**metadata, "artifact_ids": {key: value["id"] for key, value in artifacts.items()}, "elapsed_seconds": receipt["elapsed_seconds"]}, run_id=run_id)
    return artifacts
