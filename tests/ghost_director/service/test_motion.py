"""Executable motion must bind to the saved world and fail before external work."""
import copy
from unittest.mock import AsyncMock

import pytest

from ghost_director.config import Settings
from ghost_director.domain.models import Camera, Entity, SceneRevision, ShotPlan
from ghost_director.jobs.store import ProductionStore
from ghost_director.motion import render_motion, validate_motion
from ghost_director.motion_math import sample_motion
from ghost_director.motion_models import MotionPlan


def scene_and_shot():
    scene = SceneRevision(id="scene-a", entities=[
        Entity(id="room", name="Room", kind="room", dimensions=(10, 6, 3)),
        Entity(id="actor", name="Actor", kind="actor", position=(0, 0, 0), dimensions=(.6, .4, 1.8)),
        Entity(id="cup", name="Cup", kind="prop", position=(1, 0, .8), dimensions=(.1, .1, .2)),
    ], cameras=[Camera(id="camera", name="Wide", position=(0, -2, 1.5), target=(0, 0, 1), focal_length_mm=35)])
    motion = MotionPlan(camera_keyframes=[
        {"time_seconds": 0, "position": (0, -2, 1.5), "target": (0, 0, 1), "focal_length_mm": 35},
        {"time_seconds": 4, "position": (1, -2, 1.5), "target": (0, 0, 1), "focal_length_mm": 50},
    ], tracks=[{"entity_id": "cup", "keyframes": [
        {"time_seconds": 0, "position": (1, 0, .8), "rotation": (0, 0, 0)},
        {"time_seconds": 4, "position": (0, 0, .8), "rotation": (0, 0, 1)},
    ]}])
    shot = ShotPlan(id="shot", name="Move", scene_revision_id=scene.id, camera_id="camera", duration=4, motion=motion)
    return scene, shot


def test_sample_interpolates_camera_and_entity_from_one_timeline():
    scene, shot = scene_and_shot()
    motion = validate_motion(scene, shot).model_dump(mode="json")
    middle = sample_motion(motion, 2)
    assert middle["camera"]["position"] == [.5, -2, 1.5]
    assert middle["camera"]["focal_length_mm"] == 42.5
    assert middle["entities"][0]["position"] == [.5, 0, .8]
    assert middle["entities"][0]["rotation"] == [0, 0, .5]
    assert sample_motion(motion, 0)["entities"][0]["position"] == [1, 0, .8]
    assert sample_motion(motion, 4)["entities"][0]["position"] == [0, 0, .8]
    assert sample_motion(motion, 9)["entities"][0]["position"] == [0, 0, .8]


@pytest.mark.parametrize("field,value", [("position", [float("nan"), 0, 0]), ("target", [0, float("inf"), 0]), ("focal_length_mm", float("nan"))])
def test_motion_rejects_nonfinite_camera_values(field, value):
    _, shot = scene_and_shot()
    data = shot.motion.model_dump(mode="json")
    data["camera_keyframes"][1][field] = value
    with pytest.raises(ValueError):
        MotionPlan.model_validate(data)


def test_motion_rejects_camera_target_crossing_between_valid_endpoints():
    _, shot = scene_and_shot()
    data = shot.motion.model_dump(mode="json")
    for key, position in zip(data["camera_keyframes"], ((0, -1, 0), (0, 1, 0))):
        key.update(position=position, target=(0, 0, 0))
    with pytest.raises(ValueError, match="crosses its target"):
        MotionPlan.model_validate(data)


@pytest.mark.parametrize("mutation,match", [
    (lambda s, p: setattr(p, "scene_revision_id", "another-scene"), "pinned scene"),
    (lambda s, p: setattr(p, "camera_id", "absent"), "absent"),
    (lambda s, p: setattr(s.cameras[0], "position", (1, -2, 1.5)), "saved camera"),
    (lambda s, p: setattr(s.cameras[0], "aspect_ratio", 1), "16:9"),
    (lambda s, p: setattr(s.entities[2], "position", (2, 0, .8)), "saved transform"),
    (lambda s, p: setattr(s.entities[2], "visible", False), "visible"),
    (lambda s, p: s.entities[2].state.update(included_in_entity_id="actor"), "owning asset"),
    (lambda s, p: setattr(p, "duration", 5), "shot duration"),
    (lambda s, p: setattr(p.motion.tracks[0], "entity_id", "missing"), "Unknown motion entity"),
])
def test_motion_start_state_invariants(mutation, match):
    scene, shot = scene_and_shot()
    mutation(scene, shot)
    with pytest.raises(ValueError, match=match):
        validate_motion(scene, shot)


def test_motion_revalidates_after_bypassed_model_update():
    scene, shot = scene_and_shot()
    data = shot.motion.model_dump(mode="json")
    data["tracks"][0]["keyframes"][1]["position"] = [float("nan"), 0, 0]
    unsafe = shot.model_copy(update={"motion": data})
    with pytest.raises(ValueError):
        validate_motion(scene, unsafe)


def test_duplicate_tracks_and_unsorted_times_are_rejected():
    _, shot = scene_and_shot()
    data = shot.motion.model_dump(mode="json")
    data["tracks"].append(copy.deepcopy(data["tracks"][0]))
    with pytest.raises(ValueError, match="one motion track"):
        MotionPlan.model_validate(data)
    data = shot.motion.model_dump(mode="json")
    data["tracks"][0]["keyframes"][1]["time_seconds"] = 0
    with pytest.raises(ValueError, match="increase strictly"):
        MotionPlan.model_validate(data)


@pytest.mark.asyncio
async def test_invalid_plan_never_starts_process_or_spends(tmp_path, monkeypatch):
    scene, shot = scene_and_shot()
    shot.scene_revision_id = "stale"
    store = ProductionStore(tmp_path, opening_entries=())
    run = store.create_run()
    process = AsyncMock(side_effect=AssertionError("Must fail before launching Blender"))
    monkeypatch.setattr("ghost_director.motion.process", process)
    with pytest.raises(ValueError, match="pinned scene"):
        await render_motion(store, Settings("", "", "", root=tmp_path), scene, shot, run["id"])
    process.assert_not_called()
    assert store.snapshot()["jobs"] == []
    assert store.snapshot()["artifacts"] == []


@pytest.mark.asyncio
async def test_missing_blender_is_explicit_and_never_spends(tmp_path):
    scene, shot = scene_and_shot()
    store = ProductionStore(tmp_path, opening_entries=())
    with pytest.raises(ValueError, match="GHOST_BLENDER"):
        await render_motion(store, Settings("", "", "", root=tmp_path), scene, shot, store.create_run()["id"])
    assert store.snapshot()["jobs"] == []
