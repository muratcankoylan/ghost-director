"""Experiment isolation and spend authorization fail before provider access."""
from argparse import Namespace
import json
from types import SimpleNamespace

import pytest

from ghost_director.jobs.store import ProductionStore
from tools.new_reference_example import authorized, check_budget, command, execute, manifest_asset


def test_paid_scope_requires_explicit_authorization_and_finite_cap():
    manifest = {}
    with pytest.raises(ValueError, match="explicit user approval"):
        authorized(manifest)
    for cap in (float("nan"), float("inf"), -1, 1.01):
        with pytest.raises(ValueError, match="finite cap"):
            authorized({}, cost_cap=cap, reason="User explicitly approved this experiment")
    approval = authorized(manifest, cost_cap=1, reason="User explicitly approved this experiment")
    assert approval["approved_by"] == "user"
    assert "no final video" in approval["scope"]
    with pytest.raises(ValueError, match="already exists"):
        authorized(manifest, cost_cap=1, reason="Cannot replace a prior authorization")


def test_held_or_unknown_provider_charge_counts_against_experiment_cap(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    manifest = {}
    authorized(manifest, cost_cap=.1, reason="User explicitly approved a ten cent experiment")
    store.create_job({"id": "pending", "provider": "fal", "model": "fal-ai/sam-3/3d-objects"})
    store.reserve("pending", "assets_3d", .1)
    with pytest.raises(ValueError, match="spending cap"):
        check_budget(store, manifest, "meta", .02)
    assert not [job for job in store.snapshot()["jobs"] if job["provider"] == "meta"]


def test_experiment_attempt_limit_includes_failed_jobs(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    manifest = {}
    authorized(manifest, cost_cap=1, reason="User explicitly approved this experiment")
    for index in range(6):
        job_id = f"failed-{index}"
        store.create_job({"id": job_id, "provider": "meta", "model": "muse-image-1.0"})
        store.reserve(job_id, "meta", .02)
        store.begin_submission(job_id)
        store.finish_job(job_id, "failed", cost_usd=0, error="Rejected before generation")
    with pytest.raises(ValueError, match="attempt count"):
        check_budget(store, manifest, "meta", .02)


@pytest.mark.asyncio
async def test_paid_stage_rejects_before_loading_credentials(tmp_path, monkeypatch):
    (tmp_path / "experiment.json").write_text(json.dumps({"version": 1, "assets": {}, "stages": {}}))
    monkeypatch.setattr("tools.new_reference_example.load_settings", lambda _: pytest.fail("Credentials must not be loaded"))
    with pytest.raises(ValueError, match="explicit user approval"):
        await execute(Namespace(root=str(tmp_path), stage="generate", approve_paid=False))


@pytest.mark.asyncio
async def test_init_refuses_existing_nonexperiment_production(tmp_path):
    existing = tmp_path / "production"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("Existing production must survive")
    with pytest.raises(ValueError, match="existing production"):
        await execute(Namespace(root=str(tmp_path), stage="init"))
    assert marker.read_text() == "Existing production must survive"


def test_foreign_asset_cannot_be_selected_by_stage():
    manifest = {"assets": {"character": {"asset_id": "fresh-character"}}}
    assert manifest_asset(manifest, "character")["asset_id"] == "fresh-character"
    with pytest.raises(ValueError, match="freshly created"):
        manifest_asset(manifest, "previous-perfume")


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_stage_reports_real_command_lifecycle(tmp_path, fails):
    from ghost_director.bootstrap import starter_scene
    store = ProductionStore(tmp_path, opening_entries=())
    scene = starter_scene()
    store.publish_scene(scene.model_dump(mode="json"), expected_revision=None, actor="test")

    async def handle(request):
        receipt = store.command_receipt(request["id"])
        assert receipt["status"] == "accepted"
        if fails:
            raise ValueError("Local Blender failed")
        return {"artifact_id": "actual-output"}

    service = SimpleNamespace(store=store, handle=handle)
    if fails:
        with pytest.raises(ValueError, match="Local Blender failed"):
            await command(service, "render_motion", {"shot_id": "test"})
    else:
        assert await command(service, "render_motion", {"shot_id": "test"}) == {"artifact_id": "actual-output"}
    receipt = store.snapshot()["commands"][0]
    assert receipt["status"] == ("failed" if fails else "completed")
    assert receipt["operation"] == "render_motion"
    assert not store.snapshot()["jobs"]
