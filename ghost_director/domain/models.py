"""Versioned boundary models. Coordinates are meters, right-handed, Z-up."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from ghost_director.motion_models import MotionPlan

Vec3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Material(Contract):
    color: str = Field(default="#b5aa92", pattern=r"^#[0-9a-fA-F]{6}$")
    roughness: float = Field(default=0.5, ge=0, le=1)
    metalness: float = Field(default=0, ge=0, le=1)


class Entity(Contract):
    id: Identifier
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["actor", "prop", "room", "light", "set"]
    position: Vec3 = (0, 0, 0)
    rotation: Vec3 = (0, 0, 0)
    scale: Vec3 = (1, 1, 1)
    dimensions: Vec3 = (1, 1, 1)
    material: Material = Field(default_factory=Material)
    asset_id: str | None = None
    asset_url: str | None = None
    visible: bool = True
    state: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def positive_dimensions(self):
        if min(self.scale) <= 0 or min(self.dimensions) < 0 or (self.kind != "light" and min(self.dimensions) == 0):
            raise ValueError("Entity dimensions and scale must be positive")
        if max(map(abs, self.position)) > 1000:
            raise ValueError("Position exceeds the production bounds")
        if self.kind == "light":
            if self.state.get("light_type", "point") not in {"point", "directional", "area"}:
                raise ValueError("Unsupported light type")
            for key, minimum, maximum in (("intensity", 0, 1000), ("soft_size_m", 0.01, 30)):
                if key in self.state:
                    value = self.state[key]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
                        raise ValueError(f"Light {key} must be finite and between {minimum} and {maximum}")
            target = self.state.get("target")
            if target is not None:
                if not isinstance(target, (list, tuple)) or len(target) != 3 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in target):
                    raise ValueError("Light target must contain three finite coordinates")
                if tuple(target) == self.position:
                    raise ValueError("Light target must differ from its position")
        return self


class Camera(Contract):
    id: Identifier
    name: str
    position: Vec3
    target: Vec3
    focal_length_mm: float = Field(default=50, ge=12, le=300)
    sensor_width_mm: float = Field(default=36, gt=0, le=100)
    aspect_ratio: float = Field(default=16 / 9, gt=0.2, le=5)
    near: FiniteFloat = Field(default=0.05, gt=0)
    far: FiniteFloat = Field(default=100, gt=0)
    depth_of_field: bool = False
    aperture_fstop: FiniteFloat = Field(default=4, ge=1, le=32)
    focus_distance_m: FiniteFloat | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_camera(self):
        if self.position == self.target or self.far <= self.near:
            raise ValueError("Camera needs a nonzero viewing direction and ordered clip planes")
        return self


class WindowOpening(Contract):
    id: Identifier
    center_x: FiniteFloat
    width: FiniteFloat = Field(gt=0.2, le=30)
    sill: FiniteFloat = Field(ge=0.1)
    height: FiniteFloat = Field(gt=0.2, le=20)


class DoorOpening(Contract):
    wall: Literal["west", "east"] = "west"
    center_y: FiniteFloat = 0.28
    width: FiniteFloat = Field(default=0.96, ge=0.6, le=3)
    height: FiniteFloat = Field(default=2.5, ge=1.8, le=5)


class SurfacePalette(Contract):
    walls: Material = Field(default_factory=lambda: Material(color="#ffffff", roughness=0.8))
    ceiling: Material = Field(default_factory=lambda: Material(color="#ffffff", roughness=0.8))
    floor: Material = Field(default_factory=lambda: Material(color="#ffffff", roughness=0.6))
    trim: Material = Field(default_factory=lambda: Material(color="#ddd6c7", roughness=0.46))
    curtains: Material = Field(default_factory=lambda: Material(color="#d4cbb5", roughness=0.92))


class RoomDetails(Contract):
    curtains: bool = True
    sideboard: bool = True
    chandelier: bool = True


class RoomDesign(Contract):
    version: Literal[1] = 1
    style: Literal["warm_modern", "minimal", "noir"] = "warm_modern"
    windows: list[WindowOpening] = Field(default_factory=lambda: [
        WindowOpening(id="window-left", center_x=-2.5, width=1.8, sill=0.65, height=1.8),
        WindowOpening(id="window-right", center_x=2.7, width=2.6, sill=0.65, height=1.9),
    ], max_length=4)
    door: DoorOpening = Field(default_factory=DoorOpening)
    surfaces: SurfacePalette = Field(default_factory=SurfacePalette)
    details: RoomDetails = Field(default_factory=RoomDetails)
    exposure: FiniteFloat = Field(default=0, ge=-4, le=4)


class Room(Contract):
    width: float = Field(default=10, gt=0, le=100)
    depth: float = Field(default=6.2, gt=0, le=100)
    height: float = Field(default=3.15, gt=0, le=30)
    design: RoomDesign | None = None

    @model_validator(mode="after")
    def valid_architecture(self):
        if self.design is None:
            return self
        windows = sorted(self.design.windows, key=lambda item: item.center_x)
        if len({item.id for item in windows}) != len(windows):
            raise ValueError("Window IDs must be unique")
        previous_end = -self.width / 2
        for window in windows:
            start, end = window.center_x - window.width / 2, window.center_x + window.width / 2
            if start < -self.width / 2 + 0.1 or end > self.width / 2 - 0.1 or window.sill + window.height > self.height - 0.1:
                raise ValueError("Window must fit inside the north wall with a 0.1 m structural margin")
            if start < previous_end + 0.1:
                raise ValueError("Windows need at least a 0.1 m wall pier between openings")
            previous_end = end
        door = self.design.door
        if abs(door.center_y) + door.width / 2 > self.depth / 2 - 0.1 or door.height > self.height - 0.1:
            raise ValueError(f"Door must fit inside the {door.wall} wall with a 0.1 m structural margin")
        if self.design.details.sideboard and (self.width < 2.2 or self.depth < 1.2):
            raise ValueError("Room is too small for the sideboard")
        return self


class SceneRevision(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Identifier
    parent_id: str | None = None
    restored_from_id: str | None = None
    name: str = "The Dinner"
    units: Literal["meters"] = "meters"
    up_axis: Literal["Z"] = "Z"
    entities: list[Entity]
    cameras: list[Camera]
    room: Room = Field(default_factory=Room)
    created_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [x.id for x in [*self.entities, *self.cameras]]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene entity and camera IDs must be unique")
        return self


class EntityPatch(Contract):
    entity_id: Identifier
    position: Vec3 | None = None
    rotation: Vec3 | None = None
    scale: Vec3 | None = None
    material: Material | None = None
    state: dict[str, Any] | None = None


class ReferencePin(Contract):
    asset_id: Identifier
    revision_id: Identifier
    state: str = Field(default="default", min_length=1, max_length=80)


class ShotPlan(Contract):
    parent_id: str | None = None
    fps: Literal[24] = 24
    motion: MotionPlan | None = None
    reference_pins: list[ReferencePin] = Field(default_factory=list, max_length=30)
    id: Identifier
    name: str
    scene_revision_id: Identifier
    camera_id: Identifier
    duration: int = Field(default=5, ge=4, le=30)
    action: str = Field(default="Hold the scene with natural restrained performance.", max_length=12000)
    assertions: list[str] = Field(default_factory=list)
    proposed_patch: list[EntityPatch] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    status: str = "draft"
    created_at: str = Field(default_factory=now_iso)


class Artifact(Contract):
    id: Identifier
    kind: str
    mime_type: str
    sha256: str
    bytes: int = Field(ge=0)
    name: str
    relative_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    url: str = ""


class AgentTask(Contract):
    id: Identifier
    run_id: Identifier
    role_id: str
    role_label: str
    model: str
    persona_id: str | None = None
    scene_revision_id: str | None = None
    goal: str
    status: str = "queued"
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    prompt_ref: str | None = None
    context_ref: str | None = None
    attempt: int = 0
    created_at: str = Field(default_factory=now_iso)


class Observation(Contract):
    id: Identifier
    take_id: Identifier
    assertion: str
    status: Literal["pass", "fail", "unknown"]
    detail: str
    time_ms: int | None = Field(default=None, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    method: str = "meta-visual-review"


class Take(Contract):
    proposed_patch: list[EntityPatch] = Field(default_factory=list)
    id: Identifier
    shot_id: Identifier
    scene_revision_id: Identifier
    package_id: Identifier
    job_id: Identifier
    artifact_id: Identifier
    poster_id: str | None = None
    run_id: str | None = None
    status: Literal["unreviewed", "reviewed", "accepted", "rejected"] = "unreviewed"
    created_at: str = Field(default_factory=now_iso)


class Event(Contract):
    seq: int
    id: str
    type: str
    recorded_at: str
    run_id: str | None = None
    task_id: str | None = None
    job_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)


class Command(Contract):
    id: Identifier
    operation: Literal["direction", "apply_scene", "apply_design", "render_preview", "restore_revision", "update_camera", "save_shot", "generate", "accept_take", "reject_take", "repair_take", "pause", "resume", "cancel_job", "undo", "generate_image", "retire_unknown", "create_reference_asset", "generate_reference", "approve_reference", "import_reference", "crop_reference", "reconstruct_reference", "review_reconstruction", "place_reference", "bind_reference", "update_shot_plan", "render_motion", "compile_shot", "generate_package"]
    expected_revision: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Snapshot(Contract):
    reference_assets: list[dict[str, Any]] = Field(default_factory=list)
    reference_versions: list[dict[str, Any]] = Field(default_factory=list)
    reference_decisions: list[dict[str, Any]] = Field(default_factory=list)
    reference_templates: dict[str, Any] = Field(default_factory=dict)
    motion_previews: list[dict[str, Any]] = Field(default_factory=list)
    reconstructions: list[dict[str, Any]] = Field(default_factory=list)
    reconstruction_reviews: list[dict[str, Any]] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    design_defaults: RoomDesign = Field(default_factory=RoomDesign)
    undo_target_revision_id: str | None = None
    packages: list[dict[str, Any]] = Field(default_factory=list)
    production: dict[str, Any]
    scene: SceneRevision | None = None
    shots: list[ShotPlan] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    handoffs: list[dict[str, Any]] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    takes: list[Take] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    models: list[dict[str, Any]] = Field(default_factory=list)
    last_seq: int = 0


def apply_entity_patches(scene: SceneRevision, patches: list[EntityPatch], *, revision_id: str) -> SceneRevision:
    result = scene.model_dump(mode="json")
    by_id = {e["id"]: e for e in result["entities"]}
    for patch in patches:
        if patch.entity_id not in by_id:
            raise ValueError(f"Unknown entity {patch.entity_id}")
        owner = by_id[patch.entity_id]["state"].get("included_in_entity_id")
        if owner:
            raise ValueError(f"{patch.entity_id} is part of {owner}; edit the composite asset instead")
        changes = patch.model_dump(mode="json", exclude_none=True, exclude={"entity_id"})
        if "state" in changes:
            protected = {"artifact_id", "asset_status", "quality_status", "included_in_entity_id", "reference_pin", "reconstruction_id", "reconstruction_review_id", "representation", "reference_binding_kind", "reference_binding_note"}
            if any(key in protected or key.endswith("_artifact_id") for key in changes["state"]):
                raise ValueError("Scene edits cannot replace pinned asset provenance or composition ownership")
            if "intensity" in changes["state"]:
                intensity=changes["state"]["intensity"]
                if by_id[patch.entity_id]["kind"]!='light' or isinstance(intensity,bool) or not isinstance(intensity,(int,float)) or not 0<=intensity<=1000:
                    raise ValueError("Light intensity must be finite and between 0 and 1000 authored units")
            changes["state"] = by_id[patch.entity_id]["state"] | changes["state"]
        by_id[patch.entity_id].update(changes)
    result.update(id=revision_id, parent_id=scene.id, restored_from_id=None, created_at=now_iso())
    return SceneRevision.model_validate(result)
