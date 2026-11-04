import hashlib
import json
from pathlib import Path

import pytest

from ghost_director.bootstrap import bootstrap_set, starter_scene
from ghost_director.config import Settings
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.service import ProductionService


def test_starter_is_original_empty_editable_scene():
    scene = starter_scene()
    assert not [entity for entity in scene.entities if entity.kind == "actor"]
    assert len(scene.cameras) == 1
    assert scene.room.design is not None
    assert not scene.room.design.details.chandelier
    assert not scene.room.design.details.sideboard


@pytest.mark.asyncio
async def test_bootstrap_requires_local_blender_before_creating_assets(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    with pytest.raises(ValueError, match="Install Blender"):
        await bootstrap_set(store, Settings("", "", "", root=tmp_path))
    assert not (tmp_path / "assets").exists()
    assert not store.snapshot()["jobs"]


@pytest.mark.asyncio
async def test_bootstrap_refuses_existing_production_and_assets(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    settings = Settings("", "", "", root=tmp_path, blender=__file__)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/keep.txt").write_text("existing source")
    with pytest.raises(Conflict, match="assets folder"):
        await bootstrap_set(store, settings)
    assert (tmp_path / "assets/keep.txt").read_text() == "existing source"
    scene = starter_scene()
    store.publish_scene(scene.model_dump(mode="json"), expected_revision=None, actor="local-operator")
    with pytest.raises(Conflict, match="already has a scene"):
        await bootstrap_set(store, settings)
    assert store.scene()["id"] == scene.id


@pytest.mark.asyncio
async def test_bootstrap_initializes_generic_shot_with_no_api_calls(tmp_path, monkeypatch):
    store = ProductionStore(tmp_path, opening_entries=())
    settings = Settings("", "", "", root=tmp_path, blender=__file__)

    async def compiler(*args, **kwargs):
        output = Path(args[args.index("--output") + 1])
        for name in ["room.glb", "dinner-set.blend"]:
            (output / name).write_bytes(name.encode())
        receipt = {"starter_kind": "original_empty_room", "room_sha256": hashlib.sha256(b"room.glb").hexdigest(), "blend_sha256": hashlib.sha256(b"dinner-set.blend").hexdigest()}
        (output / "bootstrap-receipt.json").write_text(json.dumps(receipt))

    monkeypatch.setattr("ghost_director.bootstrap.process", compiler)
    monkeypatch.setattr("ghost_director.bootstrap.validate_surface_factors", lambda *args: None)
    result = await bootstrap_set(store, settings)
    assert result["paid_calls"] == 0
    service = ProductionService(store, settings)
    try:
        service.initialize()
    finally:
        await service.runner.http.close()
    snapshot = store.snapshot()
    assert snapshot["scene"]["name"] == "Untitled set"
    assert len(snapshot["shots"]) == 1
    assert "Eren" not in snapshot["shots"][0]["action"]
    assert "Leyla" not in snapshot["shots"][0]["action"]
    assert not snapshot["jobs"]
    assert snapshot["budget"]["committed_usd"] == 0
    room = next(entity for entity in snapshot["scene"]["entities"] if entity["kind"] == "room")
    assert room["asset_url"]
    assert room["state"]["blend_artifact_id"]


@pytest.mark.asyncio
async def test_failed_compiler_never_exposes_partial_asset_directory(tmp_path, monkeypatch):
    store = ProductionStore(tmp_path, opening_entries=())
    settings = Settings("", "", "", root=tmp_path, blender=__file__)

    async def compiler(*args, **kwargs):
        raise RuntimeError("Blender unavailable")

    monkeypatch.setattr("ghost_director.bootstrap.process", compiler)
    with pytest.raises(RuntimeError, match="Blender unavailable"):
        await bootstrap_set(store, settings)
    assert not (tmp_path / "assets").exists()
    assert (tmp_path / "bootstrap-work").exists()
    assert store.scene() is None
