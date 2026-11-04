"""Reference identity/approval invariants, image boundary and persistence checks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from PIL import Image
import pytest

from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.references import (
    MAX_IMAGE_BYTES, ReferenceRegistry, inspect_image, read_image_upload,
)


def image_bytes(color="red", *, format="PNG", size=(32, 48), exif=None):
    output = BytesIO()
    kwargs = {"exif": exif} if exif else {}
    Image.new("RGB", size, color=color).save(output, format=format, **kwargs)
    return output.getvalue()


@pytest.fixture
def registry(tmp_path):
    return ReferenceRegistry(ProductionStore(tmp_path, opening_entries=()))


def asset(registry, category="prop"):
    return registry.create_asset({"category": category, "name": "Original identity", "description": "Director supplied identity"})


def attach(registry, record, slot="primary", color="red", state="default"):
    return registry.attach_image(record["id"], record["revision_id"], image_bytes(color), slot=slot, state=state, mime_type="image/png")


def approve(registry, record):
    return registry.decide(record["id"], record["revision_id"], "approved", actor="local-operator", reason="Reviewed against the director's intent")


def pin(record, state="default"):
    return {"asset_id": record["id"], "revision_id": record["revision_id"], "state": state}


def test_character_standard_views_and_explicit_approval(registry):
    record = asset(registry, "character")
    assert record["missing_slots"] == ["front", "back", "face"]
    for slot in ["front", "back"]:
        record = attach(registry, record, slot=slot)
        with pytest.raises(ValueError, match="standard reference views"):
            approve(registry, record)
    record = attach(registry, record, slot="face")
    assert record["missing_slots"] == []
    assert record["approval_status"] == "draft"
    assert record["approved_revision_id"] is None
    with pytest.raises(Conflict, match="explicitly approved"):
        registry.approved_references([pin(record)])
    record = approve(registry, record)
    refs = registry.approved_references([pin(record)])
    assert [ref["slot"] for ref in refs] == ["front", "back", "face"]
    assert all(ref["asset_id"] == record["id"] for ref in refs)
    assert registry.store.budget()["committed_usd"] == 0


def test_new_draft_and_rejection_preserve_approved_shot_pins(registry):
    first = approve(registry, attach(registry, asset(registry)))
    first_version = registry.version(first["revision_id"])
    first_refs = registry.approved_references([pin(first)])
    revised = attach(registry, first, color="blue")
    assert revised["approved_revision_id"] == first["revision_id"]
    assert revised["approval_status"] == "draft"
    assert registry.version(first["revision_id"]) == first_version
    assert registry.approved_references([pin(first)]) == first_refs
    revised = registry.decide(revised["id"], revised["revision_id"], "rejected", actor="local-operator", reason="Changed identity")
    assert revised["approved_revision_id"] == first["revision_id"]
    with pytest.raises(Conflict, match="already has a decision"):
        approve(registry, revised)
    with pytest.raises(Conflict, match="explicitly approved"):
        registry.approved_references([pin(revised)])
    assert len(registry.snapshot()["reference_decisions"]) == 2


def test_imported_artifact_never_inherits_approval(registry):
    record = asset(registry)
    artifact = registry.store.publish_artifact(image_bytes(), kind="image", mime_type="image/png", metadata={"approval_status": "approved"})
    record = registry.attach_artifact(record["id"], record["revision_id"], artifact["id"], slot="primary", provenance={"source": "imported", "notes": "Prior production image; approval is not inherited"})
    assert record["approval_status"] == "draft"
    assert not registry.snapshot()["reference_decisions"]


def test_category_slot_and_state_are_validated_before_publication(registry):
    record = asset(registry)
    before = registry.store.snapshot()
    for slot, state in [("face", "default"), ("primary", "../../escape"), ("primary", " default ")]:
        with pytest.raises(ValueError):
            attach(registry, record, slot=slot, state=state)
    after = registry.store.snapshot()
    assert before["artifacts"] == after["artifacts"]
    assert before["last_seq"] == after["last_seq"]


def test_state_variants_keep_one_asset_identity(registry):
    record = attach(registry, asset(registry))
    record = attach(registry, record, color="brown", state="partly_eaten")
    record = approve(registry, record)
    default = registry.approved_references([pin(record)])
    changed = registry.approved_references([pin(record, "partly_eaten")])
    assert default[0]["asset_id"] == changed[0]["asset_id"]
    assert default[0]["artifact_id"] != changed[0]["artifact_id"]
    with pytest.raises(ValueError, match="requested state"):
        registry.approved_references([pin(record, "finished")])


def test_concurrent_attachments_have_one_winner(registry):
    record = asset(registry)
    artifacts = [registry.store.publish_artifact(image_bytes(color), kind="image", mime_type="image/png") for color in ["red", "blue"]]

    def bind(artifact):
        try:
            return registry.attach_artifact(record["id"], record["revision_id"], artifact["id"], slot="primary", provenance={"source": "imported"})
        except Conflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, artifacts))
    assert sum(isinstance(result, Conflict) for result in results) == 1
    assert len(registry.snapshot()["reference_versions"]) == 2
    assert registry.asset(record["id"])["version"] == 2


def test_stale_approval_and_wrong_asset_pin_are_rejected(registry):
    previous = attach(registry, asset(registry))
    current = attach(registry, previous, color="blue")
    with pytest.raises(Conflict, match="current reference draft"):
        approve(registry, previous)
    current = approve(registry, current)
    other = asset(registry)
    with pytest.raises(ValueError, match="different asset identity"):
        registry.approved_references([{**pin(current), "asset_id": other["id"]}])
    with pytest.raises(ValueError, match="Duplicate"):
        registry.approved_references([pin(current), pin(current)])


def test_original_preserved_and_display_orientation_metadata_cleaned(registry):
    exif = Image.Exif()
    exif[274] = 6  # Rotate 90 degrees clockwise for display.
    exif[270] = "Private camera note"
    original = image_bytes(format="JPEG", exif=exif)
    record = asset(registry)
    record = registry.attach_image(record["id"], record["revision_id"], original, slot="primary", mime_type="image/jpeg", filename="../../my-photo.jpg")
    reference = registry.version(record["revision_id"])["references"][0]
    assert (reference["width"], reference["height"]) == (48, 32)
    assert registry.store.artifact_path(reference["original_artifact_id"]).read_bytes() == original
    assert registry.store.artifact(reference["original_artifact_id"])["name"] == "my-photo.jpg"
    with Image.open(registry.store.artifact_path(reference["artifact_id"])) as display:
        assert display.format == "PNG"
        assert not display.getexif()


@pytest.mark.parametrize("content,mime", [
    (b"<svg><script>alert(1)</script></svg>", "image/svg+xml"),
    (b"invalid", "image/png"),
    (image_bytes(), "image/jpeg"),
    (image_bytes()[:40], "image/png"),
    (b"x" * (MAX_IMAGE_BYTES + 1), "image/png"),
    (image_bytes(size=(8193, 1)), "image/png"),
])
def test_invalid_images_rejected_without_artifacts(registry, content, mime):
    record = asset(registry)
    with pytest.raises(ValueError):
        registry.attach_image(record["id"], record["revision_id"], content, slot="primary", mime_type=mime)
    assert not registry.store.snapshot()["artifacts"]
    assert registry.asset(record["id"])["version"] == 1


def test_animated_image_rejected():
    output = BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, format="WEBP", save_all=True, append_images=[Image.new("RGB", (32, 32), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError, match="still images"):
        inspect_image(output.getvalue(), "image/webp")


def test_artifact_corruption_blocks_approval_and_later_conditioning(registry):
    first = attach(registry, asset(registry))
    reference = registry.version(first["revision_id"])["references"][0]
    path = registry.store.artifact_path(reference["artifact_id"])
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    with pytest.raises(Conflict, match="hash"):
        approve(registry, first)
    assert not registry.snapshot()["reference_decisions"]
    path.write_bytes(original)
    first = approve(registry, first)
    path.write_bytes(b"changed later")
    with pytest.raises(Conflict, match="hash"):
        registry.approved_references([pin(first)])


def test_registry_survives_restart_with_atomic_events(registry):
    record = approve(registry, attach(registry, asset(registry)))
    reopened = ReferenceRegistry(ProductionStore(registry.store.root, opening_entries=()))
    assert reopened.snapshot() == registry.snapshot()
    assert reopened.approved_references([pin(record)])
    events = registry.store.events(0)
    decisions = [event for event in events if event["type"] == "reference_decisions.updated"]
    assert len(decisions) == 1
    assert events[-1]["payload"]["approved_revision_id"] == record["revision_id"]


def test_generic_store_cannot_bypass_registry_invariants(registry):
    record = asset(registry)
    with pytest.raises((ValueError, Conflict)):
        registry.store.upsert_record("reference_assets", {**record, "approval_status": "approved"})


class StreamRequest:
    def __init__(self, chunks, **headers):
        self.chunks = chunks
        self.headers = {"content-type": "image/png", **headers}

    async def stream(self):
        for chunk in self.chunks:
            yield chunk


def test_bounded_upload_reader_counts_actual_stream_bytes():
    with pytest.raises(ValueError, match="12 MiB"):
        asyncio.run(read_image_upload(StreamRequest([b"x" * MAX_IMAGE_BYTES, b"x"], **{"content-length": "1"})))
    with pytest.raises(ValueError, match="Content-Length"):
        asyncio.run(read_image_upload(StreamRequest([], **{"content-length": "-1"})))
    content, mime = asyncio.run(read_image_upload(StreamRequest([b"one", b"two"])))
    assert (content, mime) == (b"onetwo", "image/png")


def test_location_master_alone_is_approvable_and_old_missing_slots_are_projected(registry):
    location = attach(registry, asset(registry, "location"), slot="wide")
    # Simulate the previously stored two-angle requirement without replacing IDs.
    with registry.store._transaction() as db:
        registry._write(db, "reference_assets", {**location, "missing_slots": ["opposite"]}, replace=True)
    assert registry.asset(location["id"])["missing_slots"] == []
    assert registry.snapshot()["reference_assets"][0]["missing_slots"] == []
    approved = approve(registry, location)
    assert approved["missing_slots"] == []
    assert len(registry.approved_references([pin(approved)])) == 1
    opposite = attach(registry, approved, slot="opposite")
    assert opposite["approval_status"] == "draft"
    assert opposite["approved_revision_id"] == approved["revision_id"]
    assert registry.approved_references([pin(approved)])[0]["slot"] == "wide"
