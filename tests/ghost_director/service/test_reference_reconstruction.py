import hashlib
import asyncio
from io import BytesIO
import json
from pathlib import Path
import struct
from types import SimpleNamespace

from PIL import Image
import pytest

from ghost_director.config import Settings
from ghost_director.domain.models import Entity, SceneRevision
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.reference_reconstruction import (
    ReconstructReferenceRequest, normalize_reconstruction, place_reference,
    reconstruct_reference, validate_embedded_glb, recover_reconstructions, bind_reference,
    review_reconstruction, ReviewReconstructionRequest, BindLocationReferenceRequest,
)
from ghost_director.references import ReferenceRegistry


def glb(*, external=None):
    document = {"asset": {"version": "2.0"}, "meshes": [{"primitives": []}]}
    if external:
        document["images"] = [{"uri": external}]
    raw = json.dumps(document).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(raw), len(raw), 0x4E4F534A) + raw


@pytest.fixture
def system(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    registry = ReferenceRegistry(store)
    image = BytesIO()
    Image.new("RGB", (32, 32), "green").save(image, format="PNG")
    asset = registry.create_asset({"category": "prop", "name": "Green cup"})
    asset = registry.attach_image(asset["id"], asset["revision_id"], image.getvalue(), slot="primary", mime_type="image/png")
    asset = registry.decide(asset["id"], asset["revision_id"], "approved", actor="local-operator", reason="Reviewed identity")
    settings = Settings(meta_key="", openrouter_key="", fal_key="test-credential", allow_live=True, root=tmp_path, blender=__file__)
    service = SimpleNamespace(store=store, settings=settings)
    return service, registry, asset


def review(store, reconstruction, **overrides):
    return review_reconstruction(store, {
        "reconstruction_id": reconstruction["id"], "decision": "accepted",
        "criteria": {key: "match" for key in ("geometry", "proportions", "materials", "colors", "identity")},
        "notes": "Synthetic fixture assessed for placement contract tests", **overrides,
    })


def request_for(asset, **kwargs):
    return {"asset_id": asset["id"], "revision_id": asset["revision_id"], "height_m": .3, "yaw_degrees": 0, **kwargs}


async def fake_normalizer(*args, **kwargs):
    target = Path(args[args.index("--output") + 1])
    source = Path(args[args.index("--input") + 1])
    height = float(args[args.index("--height") + 1])
    raw = source.read_bytes()
    target.write_bytes(raw)
    target.with_suffix(".normalization.json").write_text(json.dumps({
        "input_sha256": hashlib.sha256(raw).hexdigest(), "output_sha256": hashlib.sha256(raw).hexdigest(),
        "dimensions": [.2, .2, height], "quality_status": "Reconstructed static geometry; visual review required",
    }))


@pytest.mark.asyncio
async def test_reconstruction_preserves_pin_and_requires_explicit_placement(system, monkeypatch):
    service, registry, asset = system
    calls = []

    async def reconstruct(image_id, *, prompt, run_id):
        calls.append((image_id, prompt, run_id))
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary", metadata={"provider": "fal", "model": "fal-ai/sam-3/3d-objects"}), {"id": "job-fixture"}

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    record = await reconstruct_reference(service, request_for(asset))
    assert calls[0][1] == "Green cup"
    assert record["reference_pin"] == {"asset_id": asset["id"], "revision_id": asset["revision_id"], "state": "default"}
    assert record["status"] == "ready_for_review"
    assert service.store.scene() is None
    assert len(service.store.list_records("reconstructions")) == 1
    scene = SceneRevision(id="scene-original", entities=[], cameras=[])
    with pytest.raises(Conflict, match="visual review"):
        place_reference(service.store, scene, {"reconstruction_id": record["id"], "position": [1, 1, .7]})
    accepted = review(service.store, record)
    revised = place_reference(service.store, scene, {"reconstruction_id": record["id"], "position": [1, 1, .7]})
    assert len(scene.entities) == 0
    assert revised.parent_id == scene.id
    assert revised.entities[0].kind == "prop"
    assert revised.entities[0].state["reference_pin"] == record["reference_pin"]
    assert revised.entities[0].state["material_override"] is False
    assert revised.entities[0].state["reconstruction_review_id"] == accepted["id"]
    assert revised.entities[0].asset_url == service.store.artifact(record["artifact_id"])["url"]
    with pytest.raises(Conflict, match="immutable"):
        service.store.upsert_record("reconstructions", {**record, "height_m": 8})


@pytest.mark.asyncio
async def test_unapproved_or_unrelated_image_never_calls_provider(system):
    service, registry, asset = system
    with pytest.raises(ValueError, match="outside the approved"):
        await reconstruct_reference(service, request_for(asset, source_artifact_id="art-not-a-reference"))
    draft = registry.create_asset({"category": "character", "name": "Draft actor"})
    with pytest.raises(Conflict, match="explicitly approved"):
        await reconstruct_reference(service, request_for(draft))
    assert not service.store.snapshot()["runs"]
    assert not service.store.snapshot()["jobs"]


@pytest.mark.asyncio
async def test_provider_failure_marks_run_failed_and_never_places_asset(system):
    service, registry, asset = system

    async def failure(*args, **kwargs):
        raise RuntimeError("Dependency unavailable")

    service.runner = SimpleNamespace(reconstruct=failure)
    with pytest.raises(RuntimeError, match="Dependency"):
        await reconstruct_reference(service, request_for(asset))
    assert service.store.snapshot()["runs"][0]["status"] == "failed"
    assert not service.store.list_records("reconstructions")
    assert service.store.scene() is None


@pytest.mark.parametrize("external", ["https://example.com/texture.png", "file:///etc/passwd", "../../texture.png"])
def test_external_glb_resources_rejected(external):
    with pytest.raises(ValueError, match="embed"):
        validate_embedded_glb(glb(external=external))


@pytest.mark.parametrize("raw", [b"glTF", glb()[:-1], b"not-a-glb" * 10])
def test_incomplete_glb_rejected(raw):
    with pytest.raises(ValueError):
        validate_embedded_glb(raw)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, 21])
def test_authored_size_has_finite_positive_bounds(value):
    with pytest.raises(ValueError):
        ReconstructReferenceRequest(asset_id="asset", revision_id="revision", height_m=value)


@pytest.mark.asyncio
async def test_normalization_receipt_hash_mismatch_rejected(system, monkeypatch):
    service, registry, asset = system
    source = service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary")

    async def corrupt(*args, **kwargs):
        await fake_normalizer(*args, **kwargs)
        target = Path(args[args.index("--output") + 1])
        receipt = json.loads(target.with_suffix(".normalization.json").read_text())
        receipt["output_sha256"] = "0" * 64
        target.with_suffix(".normalization.json").write_text(json.dumps(receipt))

    monkeypatch.setattr("ghost_director.reference_reconstruction.process", corrupt)
    run = service.store.create_run()
    with pytest.raises(Conflict, match="receipt"):
        await normalize_reconstruction(service.store, service.settings, source, ReconstructReferenceRequest(**request_for(asset)), run_id=run["id"])
    assert not [artifact for artifact in service.store.snapshot()["artifacts"] if artifact["kind"] == "normalization_receipt"]


@pytest.mark.asyncio
async def test_placement_checks_entire_rotated_bounds(system, monkeypatch):
    service, registry, asset = system

    async def reconstruct(*args, **kwargs):
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    record = await reconstruct_reference(service, request_for(asset, height_m=1.5))
    review(service.store, record)
    scene = SceneRevision(id="scene-original", entities=[], cameras=[])
    for position, rotation in [([4.95, 0, 0], [0, 0, 0]), ([0, 0, -.1], [0, 0, 0]), ([0, 0, 2], [0, 0, 0]), ([0, 0, 0], [1.5, 0, 0])]:
        with pytest.raises(ValueError, match="inside the room"):
            place_reference(service.store, scene, {"reconstruction_id": record["id"], "position": position, "rotation": rotation})


@pytest.mark.asyncio
async def test_historical_approved_pin_remains_placeable_after_reference_draft(system, monkeypatch):
    service, registry, asset = system

    async def reconstruct(*args, **kwargs):
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    record = await reconstruct_reference(service, request_for(asset))
    review(service.store, record)
    ref = registry.version(asset["revision_id"])["references"][0]
    draft = registry.attach_artifact(asset["id"], asset["revision_id"], ref["artifact_id"], slot="side", provenance={"source": "imported"})
    assert draft["approval_status"] == "draft"
    revised = place_reference(service.store, SceneRevision(id="scene-original", entities=[], cameras=[]), {"reconstruction_id": record["id"], "position": [0, 0, 0]})
    assert revised.entities[0].state["reference_pin"]["revision_id"] == asset["revision_id"]


def queued_job(store, run_id):
    job = store.create_job({"id": "job-recovery-test", "run_id": run_id, "provider": "fal", "model": "fal-ai/sam-3/3d-objects"})
    store.reserve(job["id"], "assets_3d", .1)
    store.begin_submission(job["id"])
    store.record_submission(job["id"], "remote-test", polling_locator="https://queue.fal.run/fal-ai/sam-3/requests/remote-test/status")
    return store.get_job(job["id"])


def complete_model(store, job):
    source = store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary")
    store.finish_job(job["id"], result_refs=[source["id"]], cost_usd=.02, cost_basis="estimated")
    return source, store.get_job(job["id"])


@pytest.mark.asyncio
async def test_completed_remote_reconstruction_recovers_after_normalizer_crash_without_post(system, monkeypatch):
    service, registry, asset = system
    posts = []

    async def reconstruct(image_id, *, prompt, run_id):
        posts.append(image_id)
        # A durable immutable intent precedes the first provider call.
        intent = service.store.get_run(run_id)["reconstruction_intent"]
        assert intent["request"]["height_m"] == .3
        assert intent["source_image_id"] == image_id
        return complete_model(service.store, queued_job(service.store, run_id))

    async def crash(*args, **kwargs):
        raise RuntimeError("Process ended after the paid job completed")

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", crash)
    with pytest.raises(RuntimeError, match="Process ended"):
        await reconstruct_reference(service, request_for(asset))
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    result = await recover_reconstructions(service)
    assert len(result) == 1
    assert len(posts) == 1
    assert len(service.store.snapshot()["jobs"]) == 1
    assert service.store.budget()["committed_usd"] == .02
    assert await reconstruct_reference(service, request_for(asset)) == result[0]
    assert await recover_reconstructions(service) == []
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_known_remote_reconstruction_recovers_with_get_only(system, monkeypatch):
    service, registry, asset = system
    gets = []

    async def reconstruct(image_id, *, prompt, run_id):
        queued_job(service.store, run_id)
        raise asyncio.CancelledError()

    async def poll(job):
        gets.append(job["remote_id"])
        return complete_model(service.store, job)

    service.runner = SimpleNamespace(reconstruct=reconstruct, poll_fal=poll)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    with pytest.raises(asyncio.CancelledError):
        await reconstruct_reference(service, request_for(asset))
    results = await recover_reconstructions(service)
    assert len(results) == 1
    assert gets == ["remote-test"]
    assert service.store.snapshot()["runs"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_uncertain_submission_keeps_hold_and_never_reposts(system):
    service, registry, asset = system
    posts = []

    async def reconstruct(image_id, *, prompt, run_id):
        posts.append(image_id)
        job = service.store.create_job({"id": "job-unknown-test", "run_id": run_id, "provider": "fal", "model": "fal-ai/sam-3/3d-objects"})
        service.store.reserve(job["id"], "assets_3d", .1)
        service.store.begin_submission(job["id"])
        service.store.mark_submission_unknown(job["id"], error="Connection ended before receipt")
        raise RuntimeError("Uncertain submission")

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    with pytest.raises(RuntimeError, match="Uncertain"):
        await reconstruct_reference(service, request_for(asset))
    with pytest.raises(Conflict, match="not be submitted again"):
        await reconstruct_reference(service, request_for(asset))
    assert await recover_reconstructions(service) == []
    assert len(posts) == 1
    assert service.store.budget()["committed_usd"] == .1


@pytest.mark.asyncio
async def test_same_intent_concurrent_commands_have_only_one_provider_call(system, monkeypatch):
    service, registry, asset = system
    entered, release = asyncio.Event(), asyncio.Event()
    posts = []

    async def reconstruct(image_id, *, prompt, run_id):
        posts.append(image_id)
        entered.set()
        await release.wait()
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    first = asyncio.create_task(reconstruct_reference(service, request_for(asset)))
    await entered.wait()
    try:
        with pytest.raises(Conflict, match="not be submitted again"):
            await reconstruct_reference(service, request_for(asset))
    finally:
        release.set()
    await first
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_replacement_preserves_entity_id_and_removes_owned_old_components(system, monkeypatch):
    service, registry, asset = system

    async def reconstruct(*args, **kwargs):
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}

    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    record = await reconstruct_reference(service, request_for(asset))
    scene = SceneRevision(id="scene-original", entities=[
        Entity(id="old-prop", name="Old composite object", kind="prop", position=(1, 1, .2), rotation=(0, 0, .5)),
        Entity(id="old-component", name="Old integrated component", kind="prop", visible=False, state={"included_in_entity_id": "old-prop"}),
    ], cameras=[])
    review(service.store, record)
    revised = place_reference(service.store, scene, {"reconstruction_id": record["id"], "replace_entity_id": "old-prop"})
    assert len(scene.entities) == 2
    assert len(revised.entities) == 1
    assert revised.entities[0].id == "old-prop"
    assert revised.entities[0].position == (1, 1, .2)
    assert revised.entities[0].rotation == (0, 0, .5)
    with pytest.raises(ValueError, match="component owned"):
        place_reference(service.store, scene, {"reconstruction_id": record["id"], "replace_entity_id": "old-component"})


def test_location_binding_is_explicit_guidance_and_does_not_change_geometry(system):
    service, registry, prop = system
    image = registry.version(prop["revision_id"])["references"][0]["artifact_id"]
    location = registry.create_asset({"category": "location", "name": "Approved dining room"})
    for slot in ("wide", "opposite"):
        location = registry.attach_artifact(location["id"], location["revision_id"], image, slot=slot, provenance={"source": "imported"})
    location = registry.decide(location["id"], location["revision_id"], "approved", actor="test-fixture", reason="Synthetic contract test")
    scene = SceneRevision(id="scene-original", entities=[Entity(id="room", name="Authored room", kind="room", asset_id="original-room")], cameras=[])
    revised = bind_reference(service.store, scene, {"asset_id": location["id"], "revision_id": location["revision_id"], "entity_id": "room"})
    assert revised.entities[0].asset_id == scene.entities[0].asset_id
    assert revised.entities[0].state["reference_binding_kind"] == "director_guidance"
    assert revised.entities[0].state["reference_pin"]["state"] == "default"
    assert not scene.entities[0].state
    with pytest.raises(ValueError, match="approved locations"):
        bind_reference(service.store, scene, {"asset_id": prop["id"], "revision_id": prop["revision_id"], "entity_id": "room"})
    reviewed = bind_reference(service.store, scene, {"asset_id": location["id"], "revision_id": location["revision_id"],
        "entity_id": "room", "notes": "  Right-wall door matches; floor joints remain simplified.  "})
    note = reviewed.entities[0].state["reference_binding_note"]
    assert "does not prove reconstructed geometry or visual fidelity" in note
    assert note.endswith("Recorded adaptation review: Right-wall door matches; floor joints remain simplified.")
    assert reviewed.entities[0].asset_id == "original-room"
    assert reviewed.entities[0].position == scene.entities[0].position
    assert not scene.entities[0].state


@pytest.mark.parametrize("notes", ["x" * 4001, {"unexpected": "structured data"}, None])
def test_location_adaptation_notes_are_bounded_text(notes):
    with pytest.raises(ValueError):
        BindLocationReferenceRequest.model_validate({"entity_id": "room", "asset_id": "location",
            "revision_id": "approved", "notes": notes})


@pytest.mark.asyncio
async def test_review_is_attributed_immutable_and_later_changes_block_new_placements(system, monkeypatch):
    service, registry, asset = system
    async def reconstruct(*args, **kwargs):
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}
    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    reconstruction = await reconstruct_reference(service, request_for(asset))
    criteria = {key: "match" for key in ("geometry", "proportions", "materials", "colors", "identity")}
    accepted = review(service.store, reconstruction, criteria={**criteria, "geometry": "unknown"}, notes="Unseen back surface remains unverified; front view is sufficient for this planning shot")
    assert accepted["actor"] == "local-operator"
    assert accepted["artifact_sha256"] == service.store.artifact(reconstruction["artifact_id"])["sha256"]
    assert accepted["reference_pin"] == reconstruction["reference_pin"]
    with pytest.raises(Conflict, match="immutable"):
        service.store.upsert_record("reconstruction_reviews", {**accepted, "notes": "Changed after the decision"})
    scene = SceneRevision(id="scene-original", entities=[], cameras=[])
    placed = place_reference(service.store, scene, {"reconstruction_id": reconstruction["id"], "position": [0, 0, 0]})
    feedback = review(service.store, reconstruction, decision="changes_requested", criteria={**criteria, "colors": "mismatch"}, notes="Surface is blue but the approved cup is green")
    assert feedback["id"] != accepted["id"]
    assert placed.entities[0].state["reconstruction_review_id"] == accepted["id"]
    with pytest.raises(Conflict, match="requests changes"):
        place_reference(service.store, scene, {"reconstruction_id": reconstruction["id"], "position": [0, 0, 0]})
    with pytest.raises(ValueError, match="mismatches"):
        review(service.store, reconstruction, criteria={**criteria, "colors": "mismatch"})
    assert len(service.store.list_records("reconstruction_reviews")) == 2
    assert not service.store.snapshot()["jobs"]


def test_fidelity_review_requires_all_criteria_and_explanatory_notes():
    payload = {"reconstruction_id": "mesh", "decision": "accepted", "criteria": {key: "unknown" for key in ("geometry", "proportions", "materials", "colors", "identity")}, "notes": "   "}
    with pytest.raises(ValueError):
        ReviewReconstructionRequest.model_validate(payload)
    with pytest.raises(ValueError):
        ReviewReconstructionRequest.model_validate({**payload, "notes": "Unknown surfaces", "criteria": {"geometry": "match"}})


@pytest.mark.asyncio
async def test_review_rejects_changed_geometry_and_placement_rejects_stale_review(system, monkeypatch):
    service, registry, asset = system
    async def reconstruct(*args, **kwargs):
        return service.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary"), {"id": "job-fixture"}
    service.runner = SimpleNamespace(reconstruct=reconstruct)
    monkeypatch.setattr("ghost_director.reference_reconstruction.process", fake_normalizer)
    reconstruction = await reconstruct_reference(service, request_for(asset))
    accepted = review(service.store, reconstruction)
    service.store.upsert_record("reconstruction_reviews", {**accepted, "id": "stale-review", "artifact_sha256": "0" * 64})
    with pytest.raises(Conflict, match="exact mesh"):
        place_reference(service.store, SceneRevision(id="original", entities=[], cameras=[]), {"reconstruction_id": reconstruction["id"], "position": [0, 0, 0]})
    service.store.artifact_path(reconstruction["artifact_id"]).write_bytes(b"modified")
    with pytest.raises(Conflict, match="being reviewed"):
        review(service.store, reconstruction)
