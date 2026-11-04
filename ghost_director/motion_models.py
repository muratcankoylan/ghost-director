"""Executable motion boundaries, independent of scene models to avoid import cycles.

All positions are meters in the scene's right-handed Z-up frame. Rotations are
unwrapped XYZ Euler radians. Linear interpolation is deliberate and shared with
Blender; rig gestures are outside this root-motion contract.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Vector3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
MotionId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]


class MotionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CameraKeyframe(MotionContract):
    time_seconds: FiniteFloat = Field(ge=0, le=30)
    position: Vector3
    target: Vector3
    focal_length_mm: FiniteFloat = Field(default=50, ge=12, le=300)

    @model_validator(mode="after")
    def viewing_direction(self):
        if math.dist(self.position, self.target) < .001:
            raise ValueError("Camera target must be at least 1 mm from its position")
        if max(map(abs, (*self.position, *self.target))) > 1000:
            raise ValueError("Camera motion exceeds production bounds")
        return self


class EntityKeyframe(MotionContract):
    time_seconds: FiniteFloat = Field(ge=0, le=30)
    position: Vector3
    rotation: Vector3 = (0, 0, 0)

    @model_validator(mode="after")
    def production_bounds(self):
        if max(map(abs, self.position)) > 1000:
            raise ValueError("Entity motion exceeds production bounds")
        if max(map(abs, self.rotation)) > math.tau * 10:
            raise ValueError("Entity rotation exceeds ten full turns")
        return self


def _ordered(keys):
    if not keys or keys[0].time_seconds != 0:
        raise ValueError("Every motion track must start at zero seconds")
    if any(a.time_seconds >= b.time_seconds for a, b in zip(keys, keys[1:])):
        raise ValueError("Motion keyframe times must increase strictly")


class EntityTrack(MotionContract):
    entity_id: MotionId
    keyframes: list[EntityKeyframe] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def chronological(self):
        _ordered(self.keyframes)
        return self


class MotionPlan(MotionContract):
    version: Literal[1] = 1
    interpolation: Literal["linear"] = "linear"
    camera_keyframes: list[CameraKeyframe] = Field(min_length=2, max_length=64)
    tracks: list[EntityTrack] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def executable(self):
        _ordered(self.camera_keyframes)
        if len({track.entity_id for track in self.tracks}) != len(self.tracks):
            raise ValueError("Each entity may have only one motion track")
        # A valid direction at both endpoints can still cross zero between them.
        for first, last in zip(self.camera_keyframes, self.camera_keyframes[1:]):
            a = [p - t for p, t in zip(first.position, first.target)]
            b = [p - t - start for p, t, start in zip(last.position, last.target, a)]
            square = sum(value * value for value in b)
            closest = max(0, min(1, -sum(x*y for x, y in zip(a, b)) / square)) if square else 0
            if math.sqrt(sum((x + closest*y)**2 for x, y in zip(a, b))) < .001:
                raise ValueError("Camera path crosses its target between keyframes")
        return self
