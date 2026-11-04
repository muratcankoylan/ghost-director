"""Versioned visual identities backed by the production's artifact and event ledger.

Approval is a separate attributed decision. Adding images creates a draft while
the previous approved revision remains available to already pinned shots. This
module owns transactions for its three record collections so a reference head
and its observable event cannot disagree after concurrent uploads or a crash.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import PurePath
from typing import Annotated, Literal
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, FiniteFloat, StringConstraints, field_validator

from ghost_director.domain.models import Contract, Identifier, new_id, now_iso
from ghost_director.jobs.store import Conflict, NotFound, ProductionStore, encode


MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_EDGE = 8192
IMAGE_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
REFERENCE_COLLECTIONS = frozenset({"reference_assets", "reference_versions", "reference_decisions"})
Category = Literal["character", "location", "prop"]
StateName = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]

# Templates describe framing and identity constraints, not promises of fidelity.
REFERENCE_TEMPLATES = {
    "character": {
        "required_slots": ["front", "back", "face"],
        "slots": {
            "front": "Neutral front full-body view, head to toe, balanced light and a simple background. Show proportions, hair, full outfit, footwear and accessories.",
            "back": "The SAME character from behind, preserving proportions, hair, outfit and accessories. Use the existing identity image as conditioning when supported.",
            "face": "Close-up of the SAME face, with readable eyes, skin texture, hairline and distinguishing features under neutral light.",
            "side": "Side view of the same established identity and costume.",
            "costume": "Detail of the existing costume and its materials; do not redesign it.",
            "expression": "A named expression of the existing character; preserve facial identity.",
        },
        "limitations": "Review identity across all views. A contact sheet or shared prompt does not prove consistent identity or animation-ready geometry.",
    },
    "location": {
        "required_slots": ["wide"],
        "master_slot": "wide",
        "slots": {
            "wide": "Master photographic location reference defining architecture, walls, floor, ceiling, openings, furniture, materials and lighting design.",
            "opposite": "Optional angle derived from the master location and shared spatial plan. Preserve door/window counts, fixed openings, furniture layout and architectural identity; do not invent a separate room.",
            "side": "A side angle of the same physical location, with consistent layout.",
            "detail": "A readable architectural or surface detail of the established location.",
            "material": "Surface material and texture reference, separate from shot-specific lighting.",
            "lighting": "Lighting direction, color temperature and contrast for the established location.",
        },
        "limitations": "Images do not establish exact geometry or dimensions. Author measured or estimated layout separately in Scene State.",
    },
    "prop": {
        "required_slots": ["primary"],
        "slots": {
            "primary": "Readable product view against a clean background showing form, color, material and distinctive details.",
            "back": "Back view of the same object, preserving proportions and design.",
            "side": "Side view of the same object, preserving proportions and design.",
            "detail": "A detail of the same object and material.",
        },
        "limitations": "Record changed appearance under a named state of the same asset, rather than creating a new identity.",
    },
}


class CreateReferenceAsset(Contract):
    category: Category
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    visual_characteristics: str = Field(default="", max_length=4000)

    @field_validator("name", "description", "visual_characteristics", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReferenceProvenance(Contract):
    crop_box: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] | None = None
    source: Literal["upload", "generated", "imported"]
    prompt_ref: Identifier | None = None
    parent_reference_ids: list[Identifier] = Field(default_factory=list, max_length=24)
    notes: str = Field(default="", max_length=4000)

    @field_validator('crop_box')
    @classmethod
    def valid_crop(cls, value):
        if value is not None and not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1):
            raise ValueError('Crop bounds must be ordered fractions between zero and one')
        return value


class ReferenceImage(Contract):
    slot: str = Field(min_length=1, max_length=32)
    state: StateName = "default"
    artifact_id: Identifier
    original_artifact_id: Identifier
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    width: int = Field(gt=0, le=MAX_IMAGE_EDGE)
    height: int = Field(gt=0, le=MAX_IMAGE_EDGE)
    provenance: ReferenceProvenance


class ReferenceVersion(Contract):
    id: Identifier
    asset_id: Identifier
    version: int = Field(ge=1)
    parent_revision_id: Identifier | None = None
    created_at: str
    references: list[ReferenceImage] = Field(default_factory=list, max_length=64)
    visual_characteristics: str = Field(default="", max_length=4000)


class ReferenceAsset(Contract):
    id: Identifier
    category: Category
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    created_at: str
    updated_at: str
    revision_id: Identifier
    version: int = Field(ge=1)
    approved_revision_id: Identifier | None = None
    approval_status: Literal["draft", "approved", "rejected"] = "draft"
    missing_slots: list[str] = Field(default_factory=list)


class ReferenceDecision(Contract):
    id: Identifier
    asset_id: Identifier
    revision_id: Identifier
    decision: Literal["approved", "rejected"]
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    created_at: str

    @field_validator("actor", "reason", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReferencePin(Contract):
    asset_id: Identifier
    revision_id: Identifier
    state: StateName = "default"


def inspect_image(content: bytes, mime_type: str) -> tuple[Image.Image, dict]:
    """Decode once under explicit memory bounds; reject active/animated content."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Reference image must contain between 1 byte and 12 MiB.")
    if mime_type not in IMAGE_TYPES:
        raise ValueError("Reference images must be PNG, JPEG or WebP.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format != IMAGE_TYPES[mime_type]:
                    raise ValueError("Image contents do not match the declared media type.")
                if source.width * source.height > MAX_IMAGE_PIXELS or max(source.size) > MAX_IMAGE_EDGE:
                    raise ValueError("Reference image exceeds 24 megapixels or an 8192 pixel edge.")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Reference images must be still images; animated uploads are not supported.")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                image = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")
                image.info.clear()
        return image, {"width": image.width, "height": image.height}
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Reference image cannot be decoded safely.") from exc


async def read_image_upload(request) -> tuple[bytes, str]:
    """Bound streamed bytes even if Content-Length is absent or dishonest."""
    mime_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime_type not in IMAGE_TYPES:
        raise ValueError("Reference images must be PNG, JPEG or WebP.")
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise ValueError("Invalid upload Content-Length.")
        if int(length) > MAX_IMAGE_BYTES:
            raise ValueError("Reference image exceeds the 12 MiB upload limit.")
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_IMAGE_BYTES:
            raise ValueError("Reference image exceeds the 12 MiB upload limit.")
        chunks.extend(chunk)
    return bytes(chunks), mime_type


class ReferenceRegistry:
    def __init__(self, store: ProductionStore):
        self.store = store

    @staticmethod
    def _get(db, collection: str, record_id: str) -> dict:
        row = db.execute("SELECT data FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone()
        if row is None:
            raise NotFound(f"Reference record not found: {record_id}")
        return json.loads(row[0])

    def _write(self, db, collection: str, data: dict, *, replace=False, run_id=None):
        # Only the mutable asset head is replaced; revisions and decisions are insert-only.
        data = self.store.redactor.value(data)
        if replace:
            db.execute("UPDATE records SET data=? WHERE collection=? AND id=?", (encode(data), collection, data["id"]))
        else:
            db.execute("INSERT INTO records VALUES (?,?,?,?)", (collection, data["id"], run_id, encode(data)))
        self.store._event(db, f"{collection}.updated", data, run_id=run_id, record_collection=collection)
        return data

    @staticmethod
    def _missing(category: str, refs: list[dict]) -> list[str]:
        present = {item["slot"] for item in refs if item["state"] == "default"}
        return [slot for slot in REFERENCE_TEMPLATES[category]["required_slots"] if slot not in present]

    def create_asset(self, request: dict | CreateReferenceAsset, *, run_id=None) -> dict:
        request = CreateReferenceAsset.model_validate(request)
        timestamp = now_iso()
        asset_id = new_id({"character": "CHAR", "location": "LOC", "prop": "PROP"}[request.category])
        revision = ReferenceVersion(id=new_id("refv"), asset_id=asset_id, version=1, created_at=timestamp, visual_characteristics=request.visual_characteristics)
        asset = ReferenceAsset(id=asset_id, category=request.category, name=request.name, description=request.description, created_at=timestamp, updated_at=timestamp, revision_id=revision.id, version=1, missing_slots=self._missing(request.category, []))
        with self.store._transaction() as db:
            if run_id:
                self.store._record(db, "runs", run_id)
            self._write(db, "reference_versions", revision.model_dump(mode="json"), run_id=run_id)
            return self._write(db, "reference_assets", asset.model_dump(mode="json"), run_id=run_id)

    def asset(self, asset_id: str) -> dict:
        with self.store._transaction(write=False) as db:
            asset = self._get(db, "reference_assets", asset_id)
            version = self._get(db, "reference_versions", asset["revision_id"])
            return reference_asset_projection(asset, version)

    def version(self, revision_id: str) -> dict:
        with self.store._transaction(write=False) as db:
            return self._get(db, "reference_versions", revision_id)

    def snapshot(self) -> dict:
        with self.store._transaction(write=False) as db:
            records = {name: self.store._records(db, name) for name in REFERENCE_COLLECTIONS}
            versions = {version["id"]: version for version in records["reference_versions"]}
            records["reference_assets"] = [reference_asset_projection(asset, versions[asset["revision_id"]]) for asset in records["reference_assets"]]
            return {**records, "reference_templates": REFERENCE_TEMPLATES}

    def _check_attachment(self, asset_id: str, expected_revision_id: str, slot: str, state: str):
        asset = self.asset(asset_id)
        if asset["revision_id"] != expected_revision_id:
            raise Conflict("Reference version changed; inspect the latest draft before attaching an image.")
        if slot not in REFERENCE_TEMPLATES[asset["category"]]["slots"]:
            raise ValueError("Reference slot is not valid for this asset category.")
        # Validate state before any artifact publication, including upload originals.
        ReferencePin(asset_id=asset_id, revision_id=expected_revision_id, state=state)

    def attach_image(self, asset_id: str, expected_revision_id: str, image_bytes: bytes, *, slot: str, state: str = "default", mime_type: str, filename: str = "reference", provenance: dict | ReferenceProvenance | None = None, run_id=None) -> dict:
        self._check_attachment(asset_id, expected_revision_id, slot, state)
        source = ReferenceProvenance.model_validate(provenance or {"source": "upload"})
        image, dimensions = inspect_image(image_bytes, mime_type)
        # Keep the uploaded original byte-identical for provenance, but use a
        # metadata-free, orientation-correct derivative for display/generation.
        name = PurePath(filename.replace("\\", "/")).name[:120] or "reference"
        output = BytesIO()
        image.save(output, format="PNG")
        if output.tell() > MAX_IMAGE_BYTES:
            raise ValueError("The normalized reference exceeds 12 MiB; upload a smaller image.")
        original = self.store.publish_artifact(image_bytes, kind="reference_original", mime_type=mime_type, metadata={"name": name, "source": source.source, "asset_id": asset_id}, run_id=run_id)
        display = self.store.publish_artifact(output.getvalue(), kind="image", mime_type="image/png", metadata={"name": f"{self.asset(asset_id)['name']} · {slot}", "asset_id": asset_id, "original_artifact_id": original["id"], "transform": "EXIF orientation applied; metadata removed; lossless PNG", **dimensions}, run_id=run_id)
        return self.attach_artifact(asset_id, expected_revision_id, display["id"], slot=slot, state=state, provenance=source, original_artifact_id=original["id"], run_id=run_id)

    def attach_artifact(self, asset_id: str, expected_revision_id: str, artifact_id: str, *, slot: str, state: str = "default", provenance: dict | ReferenceProvenance, original_artifact_id: str | None = None, run_id=None) -> dict:
        self._check_attachment(asset_id, expected_revision_id, slot, state)
        source = ReferenceProvenance.model_validate(provenance)
        artifact = self.store.artifact(artifact_id)
        content = self.store.artifact_path(artifact_id).read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise Conflict("Reference image artifact hash does not match its recorded content.")
        _, dimensions = inspect_image(content, artifact["mime_type"])
        original_id = original_artifact_id or artifact_id
        self.store.artifact(original_id)
        if source.prompt_ref:
            self.store.artifact(source.prompt_ref)
        for parent in source.parent_reference_ids:
            self.store.artifact(parent)
        reference = ReferenceImage(slot=slot, state=state, artifact_id=artifact_id, original_artifact_id=original_id, sha256=artifact["sha256"], mime_type=artifact["mime_type"], provenance=source, **dimensions).model_dump(mode="json")
        with self.store._transaction() as db:
            if run_id:
                self.store._record(db, "runs", run_id)
            asset = self._get(db, "reference_assets", asset_id)
            if asset["revision_id"] != expected_revision_id:
                raise Conflict("Reference version changed while importing; the image remains available as an unbound artifact.")
            previous = self._get(db, "reference_versions", asset["revision_id"])
            refs = [item for item in previous["references"] if (item["slot"], item["state"]) != (slot, state)]
            refs.append(reference)
            revision = ReferenceVersion(id=new_id("refv"), asset_id=asset_id, version=asset["version"] + 1, parent_revision_id=asset["revision_id"], created_at=now_iso(), references=refs, visual_characteristics=previous["visual_characteristics"])
            asset.update(revision_id=revision.id, version=revision.version, updated_at=revision.created_at, approval_status="draft", missing_slots=self._missing(asset["category"], refs))
            self._write(db, "reference_versions", revision.model_dump(mode="json"), run_id=run_id)
            return self._write(db, "reference_assets", ReferenceAsset.model_validate(asset).model_dump(mode="json"), replace=True, run_id=run_id)

    def decide(self, asset_id: str, revision_id: str, decision: Literal["approved", "rejected"], *, actor: str, reason: str, run_id=None) -> dict:
        record = ReferenceDecision(id=new_id("refd"), asset_id=asset_id, revision_id=revision_id, decision=decision, actor=actor, reason=reason, created_at=now_iso()).model_dump(mode="json")
        with self.store._transaction() as db:
            if run_id:
                self.store._record(db, "runs", run_id)
            asset = self._get(db, "reference_assets", asset_id)
            if asset["revision_id"] != revision_id:
                raise Conflict("Only the current reference draft can receive a new decision.")
            if asset["approval_status"] != "draft":
                raise Conflict("Reference revision already has a decision; create a new revision to change it.")
            version = self._get(db, "reference_versions", revision_id)
            # Required views are a projection of the current specification. Old
            # location drafts may still store the superseded opposite-angle gate.
            asset["missing_slots"] = self._missing(asset["category"], version["references"])
            if decision == "approved" and asset["missing_slots"]:
                raise ValueError("Approval requires the standard reference views: " + ", ".join(asset["missing_slots"]))
            if decision == "approved":
                for image in version["references"]:
                    self._verify_reference(image)
                asset["approved_revision_id"] = revision_id
            asset.update(approval_status=decision, updated_at=record["created_at"])
            self._write(db, "reference_decisions", record, run_id=run_id)
            return self._write(db, "reference_assets", asset, replace=True, run_id=run_id)

    def _verify_reference(self, image: dict):
        artifact = self.store.artifact(image["artifact_id"])
        actual = hashlib.sha256(self.store.artifact_path(image["artifact_id"]).read_bytes()).hexdigest()
        if actual != image["sha256"] or actual != artifact["sha256"]:
            raise Conflict("Reference image is missing or its hash no longer matches the approved content.")

    def approved_references(self, pins: list[dict | ReferencePin]) -> list[dict]:
        if not pins or len(pins) > 32:
            raise ValueError("A reference package needs between 1 and 32 asset pins.")
        result = []
        seen = set()
        with self.store._transaction(write=False) as db:
            decisions = self.store._records(db, "reference_decisions")
            for raw in pins:
                pin = ReferencePin.model_validate(raw)
                key = (pin.asset_id, pin.revision_id, pin.state)
                if key in seen:
                    raise ValueError("Duplicate asset reference pin.")
                seen.add(key)
                asset = self._get(db, "reference_assets", pin.asset_id)
                version = self._get(db, "reference_versions", pin.revision_id)
                if version["asset_id"] != pin.asset_id:
                    raise ValueError("Reference revision belongs to a different asset identity.")
                if not any(d["asset_id"] == pin.asset_id and d["revision_id"] == pin.revision_id and d["decision"] == "approved" for d in decisions):
                    raise Conflict("Only explicitly approved reference versions can condition final production.")
                refs = [ref for ref in version["references"] if ref["state"] == pin.state]
                if not refs:
                    raise ValueError("The pinned asset has no references for the requested state.")
                for ref in refs:
                    self._verify_reference(ref)
                    result.append({**ref, "asset_id": pin.asset_id, "revision_id": pin.revision_id, "category": asset["category"], "name": asset["name"]})
        return result


def reference_asset_projection(asset: dict, version: dict) -> dict:
    """Apply current required-view rules without rewriting reference history."""
    return {**asset, "missing_slots": ReferenceRegistry._missing(asset["category"], version["references"])}
