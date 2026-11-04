"""Local authoring invariants; mock only the external Blender process boundary."""
import asyncio
import hashlib
import json
import struct
from pathlib import Path
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from ghost_director.api import public_snapshot
from ghost_director.config import Settings
from ghost_director.domain.models import Camera, Entity, Room, RoomDesign, SceneRevision, ShotPlan
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.scene_design import revise_room, validate_surface_factors
from ghost_director.service import ProductionService


def setup(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    blend = store.publish_artifact(b"test-only-Blender-source", kind="blender_scene", mime_type="application/octet-stream")
    scene = SceneRevision(id="initial", entities=[
        Entity(id="room", name="Room", kind="room", dimensions=(10, 6.2, 3.15), state={"blend_artifact_id": blend["id"]}),
        Entity(id="cup", name="Cup", kind="prop", dimensions=(.1, .1, .1)),
    ], cameras=[Camera(id="camera", name="Wide", position=(0, -3, 2), target=(0, 0, 1))])
    store.publish_scene(scene.model_dump(mode="json"), expected_revision=None, actor="test")
    service = ProductionService(store, Settings("", "", "", False, tmp_path, blender="mock-blender"))
    return store, service, scene


def command(store, operation, payload=None, id="command"):
    return {"id": id, "operation": operation, "expected_revision": store.scene()["id"], "payload": payload or {}}


def test_invalid_windows_and_actor_clipping_are_rejected(tmp_path):
    store, _, scene = setup(tmp_path)
    design = RoomDesign().model_dump(mode="json")
    design["windows"][1]["center_x"] = -2.4
    with pytest.raises(ValueError, match="pier"):
        Room(design=design)
    design = RoomDesign().model_dump(mode="json")
    design["door"]["height"] = 10
    with pytest.raises(ValueError):
        Room(design=design)
    scene.entities[1].position = (4.8, 0, 0)
    design = RoomDesign(windows=[])
    with pytest.raises(ValueError, match="intersect Cup"):
        revise_room(scene, Room(width=8, design=design))
    assert store.scene()["id"] == "initial"


def test_door_wall_defaults_west_and_east_uses_the_same_spatial_bounds():
    assert RoomDesign().door.wall == "west"
    east = Room(design=RoomDesign(door={"wall": "east", "center_y": 1, "width": 1, "height": 2.4}))
    assert east.design.door.wall == "east"
    for side in ("west", "east"):
        with pytest.raises(ValueError, match=f"{side} wall"):
            Room(design=RoomDesign(door={"wall": side, "center_y": 3, "width": 1, "height": 2.4}))
    with pytest.raises(ValueError):
        RoomDesign(door={"wall": "north"})


@pytest.mark.asyncio
async def test_design_compilation_does_not_lock_editing_and_stale_result_cannot_commit(tmp_path, monkeypatch):
    store, service, _ = setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def compile(*args):
        started.set()
        await release.wait()
        return args[2]

    monkeypatch.setattr("ghost_director.service.compile_room", compile)
    pending = asyncio.create_task(service.handle(command(store, "apply_design", {"room": Room(design=RoomDesign()).model_dump(mode="json")})))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(service.handle(command(store, "apply_scene", {"patches": [{"entity_id": "cup", "position": [1, 0, 0]}]}, "edit")), 1)
        release.set()
        with pytest.raises(Conflict, match="changed"):
            await pending
        assert store.scene()["room"]["design"] is None
        assert store.scene()["entities"][1]["position"] == [1, 0, 0]
        assert store.snapshot()["runs"][-1]["status"] == "failed"
        assert not store.snapshot()["jobs"]
    finally:
        release.set()
        await service.runner.http.close()


@pytest.mark.asyncio
async def test_sequential_undo_restores_earlier_edits_without_toggling(tmp_path):
    store, service, _ = setup(tmp_path)
    try:
        for index in (1, 2):
            await service.handle(command(store, "apply_scene", {"patches": [{"entity_id": "cup", "position": [index, 0, 0]}]}, str(index)))
        for expected in (1, 0):
            old_head = store.scene()["id"]
            await service.handle(command(store, "undo", id=f"undo-{expected}"))
            assert store.scene()["entities"][1]["position"] == [expected, 0, 0]
            assert store.scene()["parent_id"] == old_head
        assert public_snapshot(store.snapshot())["undo_target_revision_id"] is None
        with pytest.raises(ValueError, match="No previous"):
            await service.handle(command(store, "undo"))
    finally:
        await service.runner.http.close()


@pytest.mark.asyncio
async def test_actor_free_preview_is_local_and_quality_and_purpose_have_distinct_caches(tmp_path, monkeypatch):
    store, service, scene = setup(tmp_path)
    invocations = []

    async def blender(*args, **kwargs):
        args = list(map(str, args))
        directory = Path(args[args.index("--output") + 1])
        camera = args[args.index("--camera-id") + 1]
        width = int(args[args.index("--resolution") + 1])
        target = directory / f"{camera}.png"
        Image.new("RGB", (width, round(width * 9 / 16)), "red").save(target)
        receipt = {"blender_version": "mock boundary", "quality": {"samples": int(args[args.index('--samples') + 1]), "resolution_x": width, "view_transform": "AgX", "exposure_stops": 0}, "renders": [{"camera_id": camera, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "width": width, "height": round(width * 9 / 16)}]}
        (directory / "render-receipt.json").write_text(json.dumps(receipt))
        invocations.append(args)

    monkeypatch.setattr("ghost_director.service.process", blender)
    service.runner.image = AsyncMock(side_effect=AssertionError("Provider call forbidden"))
    service.runner.video = AsyncMock(side_effect=AssertionError("Provider call forbidden"))
    try:
        draft = await service.handle(command(store, "render_preview", {"camera_id": "camera", "quality": "draft"}))
        again = await service.handle(command(store, "render_preview", {"camera_id": "camera", "quality": "draft"}, "again"))
        assert store.artifact(draft["artifact_id"])["sha256"] == store.artifact(again["artifact_id"])["sha256"]
        final = await service.handle(command(store, "render_preview", {"camera_id": "camera", "quality": "final"}, "final"))
        assert len(invocations) == 2
        assert store.artifact(draft["artifact_id"])["metadata"]["width"] == 640
        assert store.artifact(final["artifact_id"])["metadata"]["width"] == 1280
        shot = ShotPlan(id="shot", name="Shot", scene_revision_id=scene.id, camera_id="camera")
        run = store.create_run()
        await service._render_local(scene, shot, run["id"], quality="final", render_kind="reference", require_cast=False)
        assert len(invocations) == 3
        # An empty set is a valid production shot; only visible unintegrated cast block.
        await service.render(scene, shot, run["id"])
        assert len(invocations) == 3
        assert store.scene()["id"] == "initial"
        assert not store.snapshot()["jobs"]
        assert store.snapshot()["budget"]["committed_usd"] == 0
        assert store.artifact(final["receipt_artifact_id"])["kind"] == "render_receipt"
        receipt = json.loads(store.artifact_path(final["receipt_artifact_id"]).read_text())
        assert receipt['quality'] == {'samples': 48, 'resolution_x': 1280, 'view_transform': 'AgX', 'exposure_stops': 0}
        assert receipt['requested_quality'] == 'final'
    finally:
        await service.runner.http.close()


def test_snapshot_keeps_active_command_even_after_recent_history_limit(tmp_path):
    store, _, _ = setup(tmp_path)
    store.submit_command(command(store, "render_preview", {"camera_id": "camera"}, "active"))
    for index in range(130):
        id = f"past-{index}"
        store.submit_command(command(store, "render_preview", {"camera_id": "camera"}, id))
        store.complete_command(id, result={"artifact_id": "test"})
    snapshot = public_snapshot(store.snapshot())
    assert len(snapshot["commands"]) == 129
    assert snapshot["commands"][0]["command_id"] == "active"
    assert snapshot["commands"][0]["status"] == "accepted"
    assert snapshot["commands"][-1]["result"] == {"artifact_id": "test"}
    assert snapshot["design_defaults"]["version"] == 1


@pytest.mark.asyncio
async def test_camera_edit_preserves_identity_and_restores_lens_with_undo(tmp_path):
    store, service, _ = setup(tmp_path)
    try:
        before = store.scene()
        result = await service.handle(command(store, "update_camera", {"camera_id": "camera", "camera": {"focal_length_mm": 85, "depth_of_field": True, "aperture_fstop": 2.8, "focus_distance_m": 2.4}}))
        camera = store.scene()["cameras"][0]
        assert result["camera_id"] == camera["id"] == "camera"
        assert camera["name"] == "Wide" and camera["focal_length_mm"] == 85
        assert camera["depth_of_field"] and camera["focus_distance_m"] == 2.4
        assert store.scene()["parent_id"] == before["id"]
        with pytest.raises(ValueError, match="identity"):
            await service.handle(command(store, "update_camera", {"camera_id": "camera", "camera": {"id": "other"}}))
        await service.handle(command(store, "undo"))
        assert store.scene()["cameras"] == before["cameras"]
    finally:
        await service.runner.http.close()


def test_malformed_light_settings_rejected_before_render():
    for state in ({"target": [1, 2]}, {"target": [0, 0, 0]}, {"soft_size_m": -1}, {"intensity": float('inf')}, {"light_type": "undocumented"}):
        with pytest.raises(ValueError):
            Entity(id="light", name="Light", kind="light", state=state)


@pytest.mark.parametrize('field', ['near', 'far', 'focal_length_mm', 'sensor_width_mm', 'aspect_ratio', 'aperture_fstop', 'focus_distance_m'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_camera_numeric_settings_are_finite(field, value):
    with pytest.raises(ValueError):
        Camera(id='camera', name='Camera', position=(0, -3, 2), target=(0, 0, 1), **{field: value})


def test_compiled_surface_guard_catches_exporter_dropped_tint():
    room = Room(design=RoomDesign(windows=[]))
    room.design.surfaces.walls.color = '#808080'
    document = {'nodes': [], 'meshes': [], 'materials': []}
    for index, surface in enumerate(('walls', 'floor', 'ceiling', 'trim')):
        settings = getattr(room.design.surfaces, surface)
        values = [int(settings.color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        color = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values] + [1]
        document['nodes'].append({'mesh': index, 'extras': {'gdr_surface': surface}})
        document['meshes'].append({'primitives': [{'material': index}]})
        document['materials'].append({'pbrMetallicRoughness': {'baseColorFactor': color, 'roughnessFactor': settings.roughness, 'metallicFactor': settings.metalness}})

    def encode():
        raw = json.dumps(document).encode()
        raw += b' ' * (-len(raw) % 4)
        return struct.pack('<4sIIII', b'glTF', 2, len(raw) + 20, len(raw), 0x4E4F534A) + raw

    validate_surface_factors(encode(), room)
    del document['materials'][0]['pbrMetallicRoughness']['baseColorFactor']
    with pytest.raises(ValueError, match='lost the authored walls'):
        validate_surface_factors(encode(), room)


@pytest.mark.asyncio
async def test_room_compiler_waits_for_the_same_local_blender_lock(tmp_path, monkeypatch):
    store, service, _ = setup(tmp_path)
    started = asyncio.Event()

    async def compile(*args):
        started.set()
        return args[2]

    monkeypatch.setattr('ghost_director.service.compile_room', compile)
    try:
        async with service.render_lock:
            pending = asyncio.create_task(service.handle(command(store, 'apply_design', {'room': Room(design=RoomDesign()).model_dump(mode='json')})))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(started.wait(), .05)
            assert store.scene()['id'] == 'initial'
        await pending
        assert started.is_set()
    finally:
        await service.runner.http.close()
