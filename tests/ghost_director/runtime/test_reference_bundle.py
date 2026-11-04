"""Reference-first demos survive export/import without replaying paid operations."""
from io import BytesIO
import json
import struct

from PIL import Image
import pytest

from ghost_director.domain.models import Camera, Entity, SceneRevision, ShotPlan
from ghost_director.jobs.store import ProductionStore
from ghost_director.references import ReferenceRegistry
from ghost_director.reference_reconstruction import review_reconstruction
from tools.export_ghost_demo import encoded, digest, export_bundle, import_bundle, verify_bundle


def glb():
    content = json.dumps({"asset": {"version": "2.0"}}).encode()
    content += b" " * (-len(content) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20+len(content), len(content), 0x4E4F534A)+content


def setup(tmp_path):
    store = ProductionStore(tmp_path / "source", opening_entries=())
    registry = ReferenceRegistry(store)
    image = BytesIO(); Image.new("RGB", (320, 320), "blue").save(image, format="PNG")
    asset = registry.create_asset({"category": "prop", "name": "Original bottle"})
    asset = registry.attach_image(asset["id"], asset["revision_id"], image.getvalue(), slot="primary", mime_type="image/png", provenance={"source": "upload", "crop_box": [0, 0, 1, 1]})
    asset = registry.decide(asset["id"], asset["revision_id"], "approved", actor="Original director", reason="Original bottle identity reviewed")
    pin = {"asset_id": asset["id"], "revision_id": asset["revision_id"], "state": "default"}
    reference = registry.approved_references([pin])[0]
    source_model = store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary")
    model = store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary")
    normalization = store.publish_artifact({"dimensions": [.2, .2, .4], "output_sha256": model["sha256"]}, kind="normalization_receipt")
    scene = SceneRevision(id="scene-original", entities=[Entity(id="bottle", name="Original bottle", kind="prop", dimensions=(.2, .2, .4), asset_id=model["id"], asset_url=model["url"], state={"artifact_id": model["id"], "asset_status": "available", "reference_pin": pin, "reference_artifact_id": reference["artifact_id"], "normalization_receipt_artifact_id": normalization["id"], "reconstruction_id": "reconstruction-original"})], cameras=[Camera(id="camera", name="Wide", position=(0, -2, 1.5), target=(0, 0, .2))])
    run = store.create_run(metadata={"name": "Reference workflow"})
    shot = ShotPlan(id="shot", name="Original bottle", scene_revision_id=scene.id, camera_id="camera", reference_pins=[pin])
    store.upsert_record("shots", shot.model_dump(mode="json"))
    store.upsert_record("reconstructions", {"id": "reconstruction-original", "run_id": run["id"], "reference_pin": pin, "asset_id": asset["id"], "artifact_id": model["id"], "source_image_id": reference["artifact_id"], "source_model_id": source_model["id"], "receipt_id": normalization["id"], "dimensions": [.2, .2, .4], "status": "ready_for_review", "quality_status": "unreviewed", "job_id": "historical-job"})
    review = review_reconstruction(store, {"reconstruction_id": "reconstruction-original", "decision": "accepted", "criteria": {key: "match" for key in ("geometry", "proportions", "materials", "colors", "identity")}, "notes": "Synthetic fixture: visual comparison recorded for planning"})
    scene.entities[0].state["reconstruction_review_id"] = review["id"]
    store.publish_scene(scene.model_dump(mode="json"), expected_revision=None, actor="Original director")
    video = store.publish_artifact(b"\0\0\0\x18ftypisomfixture", kind="video", mime_type="video/mp4")
    blend = store.publish_artifact(b"BLENDER-v300 synthetic fixture", kind="blender_scene", mime_type="application/octet-stream")
    receipt = store.publish_artifact({"shot_id": shot.id, "scene_revision_id": scene.id, "video_sha256": video["sha256"], "entity_mapping": [{"entity_id": "bottle", "reference_pin": pin}]}, kind="motion_receipt")
    preview = {"id": "motion-original", "run_id": run["id"], "shot_id": shot.id, "scene_revision_id": scene.id, "plan_sha256": "historical-plan", "video_id": video["id"], "blend_id": blend["id"], "poster_id": reference["artifact_id"], "receipt_id": receipt["id"]}
    store.upsert_record("motion_previews", preview)
    prompt = store.publish_artifact("Use @Image1 for the bottle in @Video1", kind="prompt", mime_type="text/plain")
    package = {"id": "package-original", "run_id": run["id"], "workflow": "reference-motion-v1", "scene_revision_id": scene.id, "shot_id": shot.id, "prompt_ref": prompt["id"], "reference_ids": [reference["artifact_id"]], "reference_pins": [pin], "motion_video_id": video["id"], "motion_receipt_id": receipt["id"], "review_sha256": "a" * 64, "approval": {"accepted": True}}
    store.upsert_record("packages", package)
    return store, registry, asset, pin, run


def test_reference_workflow_roundtrip_rebinds_media_and_retains_historical_authority(tmp_path):
    store, registry, asset, pin, run = setup(tmp_path)
    # A later draft is not part of this selected scene's immutable identity.
    later = BytesIO(); Image.new("RGB", (320, 320), "red").save(later, format="PNG")
    newer = registry.attach_image(asset["id"], asset["revision_id"], later.getvalue(), slot="primary", mime_type="image/png")
    later_image_id = registry.version(newer["revision_id"])["references"][0]["artifact_id"]
    before = store.snapshot()
    bundle = tmp_path / "bundle"
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    assert store.snapshot() == before
    manifest, creative = verify_bundle(bundle)
    assert newer["revision_id"] not in {row["id"] for row in creative["reference_versions"]}
    assert later_image_id not in {row["id"] for row in manifest["artifacts"]}
    assert creative["reference_assets"][0]["revision_id"] == pin["revision_id"]
    assert {"reference_original", "motion_receipt", "normalization_receipt"}.issubset({row["kind"] for row in manifest["artifacts"]})
    destination = tmp_path / "imported"
    result = import_bundle(bundle, destination)
    restored = ProductionStore(destination, opening_entries=())
    snapshot = restored.snapshot()
    assert result["paused"] and snapshot["jobs"] == []
    assert snapshot["budget"]["committed_usd"] == 0
    imported_refs = ReferenceRegistry(restored).approved_references([pin])
    assert imported_refs[0]["artifact_id"] != registry.approved_references([pin])[0]["artifact_id"]
    assert imported_refs[0]["sha256"] == registry.approved_references([pin])[0]["sha256"]
    assert ReferenceRegistry(restored).version(pin["revision_id"])["references"][0]["provenance"]["crop_box"] == [0, 0, 1, 1]
    assert snapshot["reference_decisions"][0]["actor"].startswith("Imported historical record:")
    reconstruction = snapshot["reconstructions"][0]
    assert reconstruction["provider_calls_executed"] == 0
    assert restored.artifact(reconstruction["source_image_id"])["kind"] == "image"
    assert restored.artifact(reconstruction["source_model_id"])["kind"] == "model"
    review = snapshot["reconstruction_reviews"][0]
    assert review["actor"].startswith("Imported historical record:")
    assert review["artifact_id"] == reconstruction["artifact_id"]
    assert review["artifact_sha256"] == restored.artifact(reconstruction["artifact_id"])["sha256"]
    assert review["reference_pin"] == pin
    assert restored.scene()["entities"][0]["state"]["reconstruction_review_id"] == review["id"]
    assert "review_sha256" not in snapshot["packages"][0]
    assert "approval" not in snapshot["packages"][0]
    assert snapshot["packages"][0]["imported_requires_recompile"] is True
    preview = snapshot["motion_previews"][0]
    assert preview["scene_revision_id"] == "scene-original"
    assert preview["scene_revision_id"] != restored.scene()["id"]
    assert restored.artifact(preview["video_id"])["mime_type"] == "video/mp4"
    assert restored.get_record("packages", "package-original")["scene_revision_id"] == "scene-original"
    asset_key = restored.scene()["entities"][0]["asset_id"]
    mapping = json.loads((destination / "assets/asset-map.json").read_text())
    assert asset_key in mapping
    restored.recover()
    assert restored.snapshot()["jobs"] == []


def test_bundle_rejects_dangling_reference_image_before_import(tmp_path):
    store, _, _, _, run = setup(tmp_path)
    bundle = tmp_path / "bundle"
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    manifest, creative = verify_bundle(bundle)
    version = next(row for row in creative["reference_versions"] if row["references"])
    version["references"][0]["artifact_id"] = "missing-artifact"
    content = encoded(creative)
    (bundle / "creative.json").write_bytes(content)
    manifest["files"]["creative.json"] = {"sha256": digest(content), "bytes": len(content)}
    (bundle / "manifest.json").write_bytes(encoded(manifest))
    with pytest.raises(ValueError, match="missing an image"):
        import_bundle(bundle, tmp_path / "imported")
    assert not (tmp_path / "imported").exists()


def test_bundle_requires_explicit_opt_in_for_byte_original_uploads(tmp_path):
    store, _, _, _, run = setup(tmp_path)
    before = store.snapshot()
    bundle = tmp_path / "bundle"
    with pytest.raises(ValueError, match="byte-original uploads"):
        export_bundle(store.root, bundle, run_ids=[run["id"]])
    assert not bundle.exists()
    assert store.snapshot() == before
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    manifest, creative = verify_bundle(bundle)
    assert manifest["uploaded_originals_included"] is True
    originals = {row["id"]: row for row in manifest["artifacts"] if row["kind"] == "reference_original"}
    assert originals
    for version in creative["reference_versions"]:
        for reference in version["references"]:
            original_id = reference["original_artifact_id"]
            assert original_id in originals
            assert originals[original_id]["sha256"] == store.artifact(original_id)["sha256"]


def test_bundle_rejects_reference_hash_rewrite_before_import(tmp_path):
    store, _, _, _, run = setup(tmp_path)
    bundle = tmp_path / "bundle"
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    manifest, creative = verify_bundle(bundle)
    version = next(row for row in creative["reference_versions"] if row["references"])
    version["references"][0]["sha256"] = "0" * 64
    content = encoded(creative)
    (bundle / "creative.json").write_bytes(content)
    manifest["files"]["creative.json"] = {"sha256": digest(content), "bytes": len(content)}
    (bundle / "manifest.json").write_bytes(encoded(manifest))
    with pytest.raises(ValueError, match="immutable identity"):
        import_bundle(bundle, tmp_path / "imported")
    assert not (tmp_path / "imported").exists()


def rewrite_creative(bundle, manifest, creative):
    content = encoded(creative)
    (bundle / "creative.json").write_bytes(content)
    manifest["files"]["creative.json"] = {"sha256": digest(content), "bytes": len(content)}
    (bundle / "manifest.json").write_bytes(encoded(manifest))


@pytest.mark.parametrize("tamper", ["orphan", "hash", "pin", "duplicate", "placement"])
def test_bundle_rejects_invalid_reconstruction_review_before_creating_destination(tmp_path, tamper):
    store, _, _, _, run = setup(tmp_path)
    bundle = tmp_path / "bundle"
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    manifest, creative = verify_bundle(bundle)
    review = creative["reconstruction_reviews"][0]
    if tamper == "orphan":
        review["reconstruction_id"] = "unknown-reconstruction"
    elif tamper == "hash":
        review["artifact_sha256"] = "0" * 64
    elif tamper == "pin":
        review["reference_pin"]["state"] = "different-appearance"
    elif tamper == "duplicate":
        creative["reconstruction_reviews"].append(dict(review))
    else:
        creative["scene"]["entities"][0]["state"]["reconstruction_review_id"] = "unknown-review"
    rewrite_creative(bundle, manifest, creative)
    with pytest.raises(ValueError, match="review"):
        import_bundle(bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_bundle_keeps_review_history_and_does_not_revive_superseded_acceptance(tmp_path):
    store, _, _, _, run = setup(tmp_path)
    old = store.snapshot()["reconstruction_reviews"][0]
    feedback = review_reconstruction(store, {"reconstruction_id": old["reconstruction_id"], "decision": "changes_requested", "criteria": {key: "mismatch" if key == "materials" else "match" for key in ("geometry", "proportions", "materials", "colors", "identity")}, "notes": "Glass appears opaque; preserve this unresolved difference"})
    bundle = tmp_path / "bundle"
    export_bundle(store.root, bundle, run_ids=[run["id"]], include_uploaded_originals=True)
    _, creative = verify_bundle(bundle)
    assert [review["id"] for review in creative["reconstruction_reviews"]] == [old["id"], feedback["id"]]
    destination = tmp_path / "restored"
    import_bundle(bundle, destination)
    restored = ProductionStore(destination, opening_entries=())
    assert [review["decision"] for review in restored.list_records("reconstruction_reviews")] == ["accepted", "changes_requested"]
    assert restored.scene()["entities"][0]["state"]["reconstruction_review_id"] == old["id"]
    assert not restored.snapshot()["jobs"]
