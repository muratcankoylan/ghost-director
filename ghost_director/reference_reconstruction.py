"""Approved identity → static reconstruction → explicit, revision-checked placement."""
from __future__ import annotations

import hashlib
import asyncio
import itertools
import json
import math
import os
from pathlib import Path
from typing import Literal
import sqlite3
import struct

from pydantic import Field, FiniteFloat, field_validator, model_validator

from ghost_director.config import REPO
from ghost_director.domain.models import Contract, Entity, Identifier, SceneRevision, Vec3, new_id, now_iso
from ghost_director.jobs.store import Conflict, NotFound
from ghost_director.media import process
from ghost_director.references import ReferencePin, ReferenceRegistry


class ReconstructReferenceRequest(ReferencePin):
    height_m: FiniteFloat = Field(ge=.01, le=20)
    yaw_degrees: FiniteFloat = Field(default=0, ge=-360, le=360)
    source_artifact_id: Identifier | None = None


class PlaceReferenceRequest(Contract):
    reconstruction_id: Identifier
    replace_entity_id: Identifier | None = None
    position: Vec3 | None = None
    rotation: Vec3 | None = None


class BindLocationReferenceRequest(ReferencePin):
    entity_id: Identifier
    notes: str = Field(default="", max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def trim_notes(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReconstructionCriteria(Contract):
    geometry: Literal["match", "mismatch", "unknown"]
    proportions: Literal["match", "mismatch", "unknown"]
    materials: Literal["match", "mismatch", "unknown"]
    colors: Literal["match", "mismatch", "unknown"]
    identity: Literal["match", "mismatch", "unknown"]


class ReviewReconstructionRequest(Contract):
    reconstruction_id: Identifier
    decision: Literal["accepted", "changes_requested"]
    criteria: ReconstructionCriteria
    notes: str = Field(min_length=1, max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def trim_notes(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def acceptance_has_no_known_mismatch(self):
        if self.decision == "accepted" and "mismatch" in self.criteria.model_dump().values():
            raise ValueError("Resolve known visual mismatches before accepting this reconstruction for planning")
        # Notes are required for every decision, including the limitations of
        # unknown criteria. This is operator assessment, not an inferred score.
        return self


class ReconstructionReview(ReviewReconstructionRequest):
    id: Identifier
    artifact_id: Identifier
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reference_pin: ReferencePin
    actor: str = Field(min_length=1, max_length=120)
    created_at: str


def review_reconstruction(store, payload: dict) -> dict:
    """Record an attributed comparison against the exact mesh and photographs."""
    request = ReviewReconstructionRequest.model_validate(payload)
    reconstruction = store.get_record("reconstructions", request.reconstruction_id)
    pin = ReferencePin.model_validate(reconstruction["reference_pin"])
    ReferenceRegistry(store).approved_references([pin])
    model = store.artifact(reconstruction["artifact_id"])
    if hashlib.sha256(store.artifact_path(model["id"]).read_bytes()).hexdigest() != model["sha256"]:
        raise Conflict("Reconstruction geometry no longer matches the artifact being reviewed")
    record = ReconstructionReview(
        **request.model_dump(), id=new_id("reconstruction-review"), artifact_id=model["id"],
        artifact_sha256=model["sha256"], reference_pin=pin, actor="local-operator", created_at=now_iso(),
    ).model_dump(mode="json")
    return store.upsert_record("reconstruction_reviews", record)


def accepted_reconstruction_review(store, reconstruction: dict, model: dict) -> dict:
    reviews = [record for record in store.list_records("reconstruction_reviews") if record.get("reconstruction_id") == reconstruction["id"]]
    if not reviews:
        raise Conflict("Inspect this reconstruction against its approved photographs and record a visual review before placement")
    review = ReconstructionReview.model_validate(reviews[-1])
    if review.decision != "accepted":
        raise Conflict("The latest visual review requests changes; resolve the recorded differences before placement")
    if (review.artifact_id != model["id"] or review.artifact_sha256 != model["sha256"]
            or review.reference_pin.model_dump(mode="json") != reconstruction["reference_pin"]):
        raise Conflict("The accepted visual review does not match this exact mesh and approved reference version")
    return review.model_dump(mode="json")


def validate_embedded_glb(raw: bytes) -> dict:
    """Prevent reconstructed assets from resolving network or arbitrary local files."""
    if len(raw) < 20 or len(raw) > 100 * 1024 * 1024:
        raise ValueError("Reconstruction GLB must be between 20 bytes and 100 MiB")
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("Reconstruction is not a complete GLB 2.0 file")
    size, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A or size > len(raw) - 20:
        raise ValueError("Reconstruction GLB has no valid JSON chunk")
    document = json.loads(raw[20:20 + size])
    if not isinstance(document, dict) or not isinstance(document.get("asset"), dict) or document["asset"].get("version") != "2.0":
        raise ValueError("Reconstruction GLB has an unsupported document version")
    resources = []
    for collection in ("buffers", "images"):
        values = document.get(collection, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError("Reconstruction GLB has malformed resource records")
        resources.extend(values)
    for item in resources:
        uri = item.get("uri")
        if uri is not None and (not isinstance(uri, str) or not uri.startswith("data:")):
            raise ValueError("Reconstruction GLB must embed all buffers and textures")
    if not document.get("meshes"):
        raise ValueError("Reconstruction GLB contains no mesh")
    return document


async def normalize_reconstruction(store, settings, source_artifact: dict, request: ReconstructReferenceRequest, *, run_id: str) -> tuple[dict, dict]:
    source = store.artifact_path(source_artifact["id"])
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_artifact["sha256"]:
        raise Conflict("Reconstruction source hash does not match its artifact")
    validate_embedded_glb(raw)
    directory = settings.root / "reconstructions" / new_id("normalize")
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / "normalized.glb"
    await process(settings.blender, "--disable-autoexec", "-b", "--python", REPO / "ghost_director/blender/normalize_reference.py", "--", "--input", source, "--output", target, "--height", request.height_m, "--yaw", request.yaw_degrees, timeout=180)
    receipt = json.loads(target.with_suffix(".normalization.json").read_text())
    output = target.read_bytes()
    validate_embedded_glb(output)
    if receipt.get("input_sha256") != source_artifact["sha256"] or receipt.get("output_sha256") != hashlib.sha256(output).hexdigest():
        raise Conflict("Normalized geometry does not match its Blender receipt")
    dimensions = receipt.get("dimensions", [])
    if len(dimensions) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in dimensions):
        raise ValueError("Normalized reconstruction has invalid dimensions")
    if not math.isclose(dimensions[2], request.height_m, rel_tol=1e-4, abs_tol=1e-5):
        raise ValueError("Normalized reconstruction did not preserve the requested height")
    pin = {key: value for key, value in request.model_dump(mode="json").items() if key in ReferencePin.model_fields}
    artifact = store.publish_artifact(output, kind="model", mime_type="model/gltf-binary", metadata={
        "name": "Reference reconstruction", "source_artifact_id": source_artifact["id"], "source_sha256": source_artifact["sha256"],
        "reference_pin": pin, "dimensions": dimensions, "quality_status": receipt["quality_status"],
        "provider": source_artifact.get("metadata", {}).get("provider"), "model": source_artifact.get("metadata", {}).get("model"),
        "origin": "Reference-conditioned geometry; normalized by Blender; original textures retained",
    }, run_id=run_id)
    receipt_artifact = store.publish_artifact(receipt, kind="normalization_receipt", metadata={"name": "Reference geometry normalization", "artifact_refs": [source_artifact["id"], artifact["id"]]}, run_id=run_id)
    return artifact, receipt_artifact


async def reconstruct_reference(service, payload: dict) -> dict:
    request = ReconstructReferenceRequest.model_validate(payload)
    registry = ReferenceRegistry(service.store)
    pin = ReferencePin(asset_id=request.asset_id, revision_id=request.revision_id, state=request.state)
    references = registry.approved_references([pin])
    asset = registry.asset(request.asset_id)
    if asset["category"] == "location":
        raise ValueError("A whole location cannot be reconstructed faithfully from these images. Use approved location references with the architectural layout editor.")
    if not service.settings.blender or not Path(service.settings.blender).is_file():
        raise ValueError("Configure an available Blender executable before reconstructing assets")
    if not service.settings.fal_key:
        raise ValueError("Configure the server-side reconstruction provider credential")
    if request.source_artifact_id:
        chosen = next((ref for ref in references if ref["artifact_id"] == request.source_artifact_id), None)
        if chosen is None:
            raise ValueError("The selected reconstruction image is outside the approved reference pin")
    else:
        primary = "front" if asset["category"] == "character" else "primary"
        chosen = next((ref for ref in references if ref["slot"] == primary), references[0])
    intent = {"request": request.model_dump(mode="json"), "source_image_id": chosen["artifact_id"], "source_image_sha256": chosen["sha256"], "model": "fal-ai/sam-3/3d-objects"}
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    run_id = "reconstruct-" + fingerprint[:32]
    existing = _existing_reconstruction(service.store, run_id)
    if existing:
        return existing
    # The deterministic run identity atomically claims one paid attempt for this
    # immutable intent. Retrying a command or racing a second server cannot POST
    # it again. A completed remote job may still need only local normalization.
    try:
        run = service.store.create_run(run_id, metadata={"name": "Reference to 3D", "operation": "reference_reconstruction", "reconstruction_intent": intent, "owner_pid": os.getpid(), "reference_pin": pin.model_dump(mode="json"), "input_refs": [chosen["artifact_id"]]})
    except sqlite3.IntegrityError:
        run = service.store.get_run(run_id)
        complete = next((job for job in run["jobs"] if job["status"] == "succeeded" and job.get("result_refs")), None)
        if complete:
            return await _complete_reconstruction(service, run, service.store.artifact(complete["result_refs"][0]), complete)
        raise Conflict("This reconstruction intent already has a recorded attempt. Inspect its pending, failed or uncertain job; it will not be submitted again.") from None
    try:
        service.store.append_event("reconstruction.started", {"asset_id": request.asset_id, "reference_pin": pin.model_dump(mode="json"), "artifact_refs": [chosen["artifact_id"]], "stage": "geometry"}, run_id=run["id"])
        foreground = "person" if asset["category"] == "character" else asset["name"][:200]
        source, job = await service.runner.reconstruct(chosen["artifact_id"], prompt=foreground, run_id=run["id"])
        return await _complete_reconstruction(service, run, source, job)
    except BaseException:
        service.store.finish_run(run["id"], "failed")
        raise


def _existing_reconstruction(store, run_id: str) -> dict | None:
    try:
        return store.get_record("reconstructions", "geometry-" + run_id)
    except NotFound:
        return None


async def _complete_reconstruction(service, run: dict, source: dict, job: dict) -> dict:
    existing = _existing_reconstruction(service.store, run["id"])
    if existing:
        return existing
    intent = run["reconstruction_intent"]
    request = ReconstructReferenceRequest.model_validate(intent["request"])
    pin = ReferencePin(asset_id=request.asset_id, revision_id=request.revision_id, state=request.state)
    refs = ReferenceRegistry(service.store).approved_references([pin])
    if not any(ref["artifact_id"] == intent["source_image_id"] and ref["sha256"] == intent["source_image_sha256"] for ref in refs):
        raise Conflict("Recovered reconstruction no longer matches its immutable approved image intent")
    service.store.append_event("reconstruction.normalizing", {"asset_id": request.asset_id, "artifact_refs": [source["id"]], "height_m": request.height_m, "yaw_degrees": request.yaw_degrees}, run_id=run["id"])
    # Network reconstruction may run alongside local previews; only Blender
    # normalization shares the machine's bounded render slot.
    async with getattr(service, "render_lock", asyncio.Lock()):
        model, receipt = await normalize_reconstruction(service.store, service.settings, source, request, run_id=run["id"])
    record = {
        "id": "geometry-" + run["id"], "run_id": run["id"], "created_at": now_iso(),
        "asset_id": request.asset_id, "reference_pin": pin.model_dump(mode="json"), "source_image_id": intent["source_image_id"],
        "source_model_id": source["id"], "artifact_id": model["id"], "receipt_id": receipt["id"], "job_id": job["id"],
        "height_m": request.height_m, "yaw_degrees": request.yaw_degrees, "dimensions": model["metadata"]["dimensions"],
        "status": "ready_for_review", "quality_status": model["metadata"]["quality_status"],
    }
    try:
        service.store.upsert_record("reconstructions", record, run_id=run["id"])
    except Conflict:
        existing = _existing_reconstruction(service.store, run["id"])
        if existing is None:
            raise
        return existing
    service.store.finish_run(run["id"])
    return record


async def recover_reconstructions(service) -> list[dict]:
    """Resume known GETs and local normalization; this path never submits a POST."""
    results = []
    runs = [run for run in service.store.snapshot()["runs"] if run.get("operation") == "reference_reconstruction"]
    for saved in runs:
        if _existing_reconstruction(service.store, saved["id"]):
            continue
        owner = saved.get("owner_pid")
        if isinstance(owner, int) and owner > 0 and owner != os.getpid():
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                continue
            else:
                continue
        run = service.store.get_run(saved["id"])
        jobs = [job for job in run["jobs"] if job["provider"] == "fal" and job["model"] == run["reconstruction_intent"]["model"]]
        if not jobs:
            service.store.append_event("reconstruction.recovery_blocked", {"run_id": run["id"], "reason": "No durable remote job exists; startup does not submit reconstruction requests."}, run_id=run["id"])
            continue
        job = jobs[-1]
        try:
            if job["status"] in {"queued", "running", "cancel_requested"} and job.get("remote_id"):
                source, _ = await service.runner.poll_fal(job)
                job = service.store.get_job(job["id"])
            elif job["status"] == "succeeded" and job.get("result_refs"):
                source = service.store.artifact(job["result_refs"][0])
            else:
                continue  # Unknown/failed/cancelled outcomes never authorize a paid retry.
            record = await _complete_reconstruction(service, run, source, job)
            service.store.append_event("reconstruction.recovered", {"reconstruction_id": record["id"], "artifact_refs": [record["artifact_id"], record["receipt_id"]]}, run_id=run["id"], job_id=job["id"])
            results.append(record)
        except Exception as exc:
            service.store.append_event("reconstruction.recovery_failed", {"run_id": run["id"], "job_id": job["id"], "error": str(exc)}, run_id=run["id"], job_id=job["id"])
    return results


def place_reference(store, scene: SceneRevision, payload: dict) -> SceneRevision:
    request = PlaceReferenceRequest.model_validate(payload)
    reconstruction = store.get_record("reconstructions", request.reconstruction_id)
    registry = ReferenceRegistry(store)
    pin = ReferencePin.model_validate(reconstruction["reference_pin"])
    references = registry.approved_references([pin])
    asset = registry.asset(pin.asset_id)
    model = store.artifact(reconstruction["artifact_id"])
    if hashlib.sha256(store.artifact_path(model["id"]).read_bytes()).hexdigest() != model["sha256"]:
        raise Conflict("Reconstruction geometry no longer matches its immutable artifact")
    review = accepted_reconstruction_review(store, reconstruction, model)
    if asset["category"] == "location":
        raise ValueError("Location layout must be authored with the architectural editor")
    kind = "actor" if asset["category"] == "character" else "prop"
    replaced = next((entity for entity in scene.entities if entity.id == request.replace_entity_id), None) if request.replace_entity_id else None
    if request.replace_entity_id and replaced is None:
        raise ValueError("The entity selected for replacement does not exist")
    if replaced and (replaced.kind != kind or replaced.state.get("included_in_entity_id")):
        raise ValueError("Replacement must match the asset category and cannot target a component owned by another entity")
    if request.position is None and replaced is None:
        raise ValueError("A new asset placement needs an explicit position")
    entity = Entity(
        id=replaced.id if replaced else new_id("entity"), name=asset["name"], kind=kind,
        position=request.position if request.position is not None else replaced.position,
        rotation=request.rotation if request.rotation is not None else replaced.rotation if replaced else (0, 0, 0), dimensions=tuple(reconstruction["dimensions"]),
        asset_id=model["id"], asset_url=model["url"], state={
            "artifact_id": model["id"], "asset_status": "available", "material_override": False,
            "reference_pin": pin.model_dump(mode="json"), "reference_artifact_id": reconstruction["source_image_id"],
            "reconstruction_id": reconstruction["id"], "quality_status": reconstruction["quality_status"],
            "reconstruction_review_id": review["id"],
            "normalization_receipt_artifact_id": reconstruction["receipt_id"], "representation": "reference_reconstruction",
        },
    )
    # Same canonical base-origin bounds as the room editor, including rotation.
    rx, ry, rz = entity.rotation
    for signs in itertools.product((-1, 1), repeat=3):
        x, y = (entity.dimensions[i] * signs[i] / 2 for i in (0, 1))
        z = entity.dimensions[2] * (signs[2] + 1) / 2
        y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
        x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
        x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
        x, y, z = (value + entity.position[i] for i, value in enumerate((x, y, z)))
        if abs(x) > scene.room.width / 2 - .03 or abs(y) > scene.room.depth / 2 - .03 or z < -.001 or z > scene.room.height:
            raise ValueError("Place the complete reconstructed asset inside the room bounds")
    revised = scene.model_copy(deep=True)
    revised.id = new_id("scene")
    revised.parent_id = scene.id
    revised.restored_from_id = None
    revised.created_at = now_iso()
    if replaced:
        revised.entities = [entity if current.id == replaced.id else current for current in revised.entities if current.state.get("included_in_entity_id") != replaced.id]
    else:
        revised.entities.append(entity)
    return revised


def bind_reference(store, scene: SceneRevision, payload: dict) -> SceneRevision:
    request = BindLocationReferenceRequest.model_validate(payload)
    registry = ReferenceRegistry(store)
    pin = ReferencePin(asset_id=request.asset_id, revision_id=request.revision_id, state=request.state)
    registry.approved_references([pin])
    asset = registry.asset(request.asset_id)
    entity = next((entity for entity in scene.entities if entity.id == request.entity_id), None)
    if asset["category"] != "location" or entity is None or entity.kind != "room":
        raise ValueError("Direct reference binding supports approved locations and the authored room only. Reconstruct character and prop identities before placement.")
    revised = scene.model_copy(deep=True)
    revised.id = new_id("scene")
    revised.parent_id = scene.id
    revised.restored_from_id = None
    revised.created_at = now_iso()
    room = next(item for item in revised.entities if item.id == entity.id)
    room.state["reference_pin"] = pin.model_dump(mode="json")
    room.state["reference_binding_kind"] = "director_guidance"
    room.state["reference_binding_note"] = "Approved location appearance guides authored architecture; this binding does not prove reconstructed geometry or visual fidelity."
    if request.notes:
        room.state["reference_binding_note"] += " Recorded adaptation review: " + request.notes
    return revised
