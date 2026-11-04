#!/usr/bin/env python3
"""Stage a new, reviewable reference-first experiment without importing a demo.

All billable stages require a separately recorded user authorization. Photograph
approval and mesh placement remain separate, explicit host review stages. This
tool deliberately has no final-video generation operation.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost_director.bootstrap import bootstrap_set
from ghost_director.config import load_settings
from ghost_director.domain.models import Room, SceneRevision, new_id, now_iso
from ghost_director.jobs.store import ProductionStore
from ghost_director.references import ReferenceRegistry
from ghost_director.service import ProductionService

TITLE = "The Conservator · fresh reference experiment"
ACTOR = "host-test-reviewer"
# A lower target and bounded approach retain the pitcher on the floor and the
# complete character. Tuple values prevent accidental mutation between stages.
APPROACH_CAMERA_PATH = (
    (0, (2.8, -2.4, 1.6), (.25, .3, .7), 28),
    (12, (2.5, -2.15, 1.6), (.25, .3, .7), 28),
)


def approach_camera_keyframes() -> list[dict]:
    return [{"time_seconds": time, "position": list(position), "target": list(target), "focal_length_mm": lens}
        for time, position, target, lens in APPROACH_CAMERA_PATH]


DEFINITIONS = {
    "character": {
        "name": "Mara · conservator",
        "description": "An original fictional adult female conservator, age 35, short dark brown hair tied into a small low bun, olive skin, brown eyes, terracotta cotton workwear jumpsuit and plain dark brown leather ankle boots. No accessories.",
        "prompt": "Photorealistic casting and costume reference, photographic skin pores and natural fabric texture. A completely fictional adult woman. Three equal vertical panels. Left: neutral full body front, head and both boots fully visible, arms slightly separated from torso and fingers relaxed. Middle: full body back of the exact same person and outfit, boots fully visible. Right: close-up of the same face with the same hairline. Plain light gray background throughout. Consistent physique, hair and terracotta jumpsuit across the three panels. No props, no furniture, no text, no table. Leave safe margins inside each panel. The character must occupy most of the full-height front and back panels.",
        "height_m": 1.72,
    },
    "prop": {
        "name": "Cobalt ceramic pitcher",
        "description": "An original small opaque matte cobalt-blue ceramic water pitcher, 32 cm tall, simple rounded body, one thick curved handle and a short pouring lip. Unglazed exterior, solid blue surface, no pattern or label.",
        "prompt": "One photographic product image of exactly one simple matte cobalt-blue ceramic pitcher. Three-quarter front view clearly showing the open rim, pouring lip and one broad handle on the right. Entire object visible with generous safe margin on neutral pale gray seamless background. Opaque fired ceramic, diffuse matte surface, no metallic reflection or glass transparency. Realistic product photography and physically plausible geometry. No other objects, no table, no stand, no hands, no labels, no text. The pitcher occupies about 75 percent of image height.",
        "height_m": 0.32,
    },
    "location": {
        "name": "Sunlit cream-plaster studio",
        "description": "An original empty 6 by 5 metre conservation studio, 3.2 metres high. Warm cream plaster walls and ceiling, pale warm limestone floor, one broad rectangular window in the back wall left of centre, simple door in the right wall. No furniture, tables, props or people.",
        "prompt": "One photorealistic architectural interior master photograph, eye-level 24 mm lens looking toward the north/back wall from the south/front of a simple rectangular 6 by 5 metre room, ceiling height 3.2 metres. Warm cream lime plaster walls, cream ceiling, pale warm limestone slab floor, understated narrow matching base trim. Exactly one broad plain rectangular window in the back wall left of centre, approximately 2.2 metres wide, sill 0.8 metre high. One plain closed door in the right wall. Soft directional afternoon sunlight from the window with readable warm shadows. Empty room, no furniture, no table, no chairs, no shelving, no pendant lights, no curtains, no objects, no people. Credible real architectural photography, restrained materials, clear spatial perspective. Keep all architecture coherent in this single master image.",
    },
}


def save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@contextmanager
def experiment_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def authorized(manifest: dict, *, cost_cap: float | None = None, reason: str | None = None) -> dict:
    if cost_cap is not None:
        if not math.isfinite(cost_cap) or not 0 < cost_cap <= 1 or not reason or len(reason.strip()) < 10:
            raise ValueError("Record the user's explicit authorization and a finite cap no greater than $1")
        if "authorization" in manifest:
            raise ValueError("Authorization already exists; inspect it rather than silently replacing it")
        manifest["authorization"] = {
            "scope": "3 new Meta images and 2 SAM 3D Objects reconstructions; no final video",
            "approved_by": "user", "recorded_by": ACTOR, "reason": reason.strip(),
            "cap_usd": cost_cap, "recorded_at": now_iso(),
            "maximum_image_attempts": 6, "maximum_reconstruction_attempts": 4,
            "estimated_initial_usd": 0.07, "initial_reserves_usd": 0.26,
        }
    approval = manifest.get("authorization")
    if not approval or approval.get("approved_by") != "user":
        raise ValueError("Paid generation requires separately recorded explicit user approval")
    return approval


def check_budget(store, manifest: dict, category: str, reserve: float) -> None:
    approval = authorized(manifest)
    budget = store.budget()
    if float(budget["committed_usd"]) + reserve > approval["cap_usd"] + 1e-9:
        raise ValueError("This request would exceed the approved experiment spending cap")
    jobs = store.snapshot()["jobs"]
    attempts = sum(job.get("category") == category for job in jobs)
    maximum = approval["maximum_image_attempts" if category == "meta" else "maximum_reconstruction_attempts"]
    if attempts >= maximum:
        raise ValueError("The approved experiment attempt count is exhausted")


async def command(service, operation: str, payload: dict):
    request = {"id": new_id("experiment-command"), "operation": operation,
        "expected_revision": service.store.scene()["id"], "payload": payload}
    receipt = service.store.submit_command(request)
    if receipt["duplicate"]:
        raise ValueError("This stage command was already claimed; inspect its existing receipt")
    try:
        result = await service.handle(request)
    except asyncio.CancelledError:
        # Preserve uncertainty exactly as the HTTP dispatcher does. Recovery can
        # inspect the owner and job rather than claiming a cancelled call failed.
        raise
    except Exception as error:
        service.store.complete_command(request["id"], error=f"{type(error).__name__}: {error}")
        raise
    service.store.complete_command(request["id"], result=result)
    return result


def manifest_asset(manifest: dict, key: str) -> dict:
    if key not in DEFINITIONS or key not in manifest.get("assets", {}):
        raise ValueError("Select one of this experiment's freshly created identities")
    return manifest["assets"][key]


def inspect_assets(store, manifest: dict) -> dict:
    registry = ReferenceRegistry(store)
    result = {}
    for key, entry in manifest["assets"].items():
        asset = registry.asset(entry["asset_id"])
        refs = registry.version(asset["revision_id"])["references"]
        result[key] = {"asset_id": asset["id"], "revision_id": asset["revision_id"],
            "approval_status": asset["approval_status"], "references": [
                {**ref, "path": str(store.artifact_path(ref["artifact_id"])),
                 "original_path": str(store.artifact_path(ref["original_artifact_id"]))} for ref in refs],
            "reconstructions": [{**rec, "path": str(store.artifact_path(rec["artifact_id"]))}
                for rec in store.list_records("reconstructions") if rec["asset_id"] == asset["id"]]}
    return result


async def execute(args):
    root = Path(args.root).resolve()
    checkpoint = root / "experiment.json"
    if args.stage == "init" and (root / "production").exists():
        partial = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {}
        if partial.get("version") != 1 or partial.get("assets") or partial.get("stages"):
            raise ValueError("Refusing an existing production; initialization requires a completely new experiment root")
    if args.stage != "init" and not checkpoint.is_file():
        raise ValueError("Initialize a new isolated experiment before running stages")
    manifest = json.loads(checkpoint.read_text()) if checkpoint.is_file() else {
        "version": 1, "title": TITLE, "created_at": now_iso(), "assets": {},
        "provenance": "New production; no imported character, table, perfume, photograph or demo assets.",
        "definitions": DEFINITIONS, "stages": {},
    }
    paid = args.stage in {"generate", "reconstruct"}
    if args.approve_paid:
        authorized(manifest, cost_cap=args.cost_cap, reason=args.approval_reason)
        save(checkpoint, manifest)
    if paid:
        authorized(manifest)
    settings = replace(load_settings(args.env_file), root=root / "production", allow_live=paid)
    store = ProductionStore(settings.root, secrets=settings.secrets, opening_entries=(), ceiling_usd=1,
        allocations={"meta": .12, "assets_3d": .40, "video": 0, "contingency": .48})
    service = ProductionService(store, settings)
    registry = ReferenceRegistry(store)
    try:
        if args.stage == "init":
            if store.scene() or store.snapshot()["jobs"] or store.list_records("reference_assets"):
                raise ValueError("Partial initialization cannot reuse any existing scene, job or reference identity")
            save(checkpoint, manifest)
            await bootstrap_set(store, settings)
            service.initialize()
            scene = SceneRevision.model_validate(store.scene())
            previous = scene.id
            scene.id, scene.parent_id, scene.name, scene.created_at = new_id("scene"), previous, TITLE, now_iso()
            store.publish_scene(scene.model_dump(mode="json"), expected_revision=previous, actor=ACTOR)
            for key, definition in DEFINITIONS.items():
                asset = await command(service, "create_reference_asset", {"category": key,
                    "name": definition["name"], "description": definition["description"]})
                manifest["assets"][key] = {"asset_id": asset["id"]}
            manifest["stages"]["init"] = {"completed_at": now_iso(), "scene_id": scene.id, "paid_calls": 0}
            save(checkpoint, manifest)
        elif args.stage == "generate":
            for key in ([args.asset] if args.asset else DEFINITIONS):
                entry = manifest_asset(manifest, key)
                asset = registry.asset(entry["asset_id"])
                if registry.version(asset["revision_id"])["references"]:
                    continue
                if entry.get("image_attempt_started"):
                    raise ValueError(f"{key} has an interrupted attempt; inspect its job before any retry")
                check_budget(store, manifest, "meta", .02)
                entry["image_attempt_started"] = now_iso()
                save(checkpoint, manifest)
                result = await command(service, "generate_reference", {"asset_id": asset["id"],
                    "expected_revision_id": asset["revision_id"], "prompt": DEFINITIONS[key]["prompt"]})
                entry["generated"] = result
                save(checkpoint, manifest)
        elif args.stage == "approve-references":
            for key, review in json.loads(Path(args.review_file).read_text()).items():
                entry = manifest_asset(manifest, key)
                asset = registry.asset(entry["asset_id"])
                if review["asset_id"] != asset["id"] or review["revision_id"] != asset["revision_id"]:
                    raise ValueError("Visual review must identify this exact fresh reference revision")
                registry.decide(asset["id"], asset["revision_id"], review["decision"], actor=ACTOR,
                    reason="Host visual test review; not user creative approval. " + review["notes"])
                entry["visual_review"] = review
                save(checkpoint, manifest)
        elif args.stage == "reconstruct":
            for key in ([args.asset] if args.asset else ("character", "prop")):
                if key == "location":
                    raise ValueError("Whole-room image reconstruction is unsupported; use the reviewed local design stage")
                entry = manifest_asset(manifest, key)
                if entry.get("reconstruction"):
                    continue
                asset = registry.asset(entry["asset_id"])
                if not asset["approved_revision_id"]:
                    raise ValueError("Inspect and approve the newly generated photograph before reconstruction")
                check_budget(store, manifest, "assets_3d", .10)
                result = await command(service, "reconstruct_reference", {"asset_id": asset["id"],
                    "revision_id": asset["approved_revision_id"], "state": "default", "height_m": DEFINITIONS[key]["height_m"]})
                entry["reconstruction"] = result
                save(checkpoint, manifest)
        elif args.stage == "review-place":
            for key, review in json.loads(Path(args.review_file).read_text()).items():
                entry = manifest_asset(manifest, key)
                record = entry["reconstruction"]
                if review["reconstruction_id"] != record["id"]:
                    raise ValueError("Mesh review must identify this experiment's exact reconstruction")
                result = await command(service, "review_reconstruction", {"reconstruction_id": record["id"],
                    "decision": review["decision"], "criteria": review["criteria"],
                    "notes": "Host visual test reviewer (Codex); not user approval. " + review["notes"]})
                entry["mesh_review"] = result
                if review["decision"] == "accepted" and not any(entity.get("state", {}).get("reconstruction_id") == record["id"] for entity in store.scene()["entities"]):
                    await command(service, "place_reference", {"reconstruction_id": record["id"],
                        "position": review["position"], "rotation": review.get("rotation", [0, 0, 0])})
                save(checkpoint, manifest)
        elif args.stage == "design":
            design = json.loads(Path(args.design_file).read_text())
            asset = registry.asset(manifest_asset(manifest, "location")["asset_id"])
            if design["revision_id"] != asset["approved_revision_id"] or not design.get("notes", "").strip():
                raise ValueError("Room adaptation must identify the approved master and observed matching limitations")
            room = Room.model_validate(design["room"])
            await command(service, "apply_design", {"room": room.model_dump(mode="json")})
            room_entity = next(entity for entity in store.scene()["entities"] if entity["kind"] == "room")
            await command(service, "bind_reference", {"entity_id": room_entity["id"], "asset_id": asset["id"],
                "revision_id": asset["approved_revision_id"], "state": "default", "notes": design["notes"]})
            manifest["stages"]["design"] = {"completed_at": now_iso(), **design}
        elif args.stage == "compose":
            scene = store.scene()
            ids = {entity["state"].get("reference_pin", {}).get("asset_id") for entity in scene["entities"]}
            if not {entry["asset_id"] for entry in manifest["assets"].values()}.issubset(ids):
                raise ValueError("Place reviewed fresh character/prop and bind the location before composing")
            if manifest["stages"].get("compose"):
                raise ValueError("The experiment already has a shot; revise it in the application")
            camera_keys = approach_camera_keyframes()
            shot = await command(service, "save_shot", {"workflow": "reference-motion-v1", "name": "The Conservator · slow approach", "duration": camera_keys[-1]["time_seconds"],
                "camera": {key: value for key, value in camera_keys[0].items() if key != "time_seconds"},
                "action": "One continuous slow camera approach in the approved studio. The conservator stands still beside the cobalt ceramic pitcher on the floor. Preserve the exact approved person, wardrobe, pitcher and architecture. No touching, extra objects or invented movement."})
            pins = [{"asset_id": entry["asset_id"], "revision_id": registry.asset(entry["asset_id"])["approved_revision_id"], "state": "default"} for entry in manifest["assets"].values()]
            revised = await command(service, "update_shot_plan", {"shot_id": shot["id"], "reference_pins": pins,
                "motion": {"camera_keyframes": camera_keys, "tracks": []}})
            manifest["stages"]["compose"] = {"completed_at": now_iso(), **revised}
        elif args.stage in {"motion", "compile"}:
            stage = manifest["stages"]["compose"]
            if args.stage in manifest["stages"]:
                raise ValueError("This local stage is already checkpointed; inspect or revise its shot before repeating")
            result = await command(service, "render_motion" if args.stage == "motion" else "compile_shot", {"shot_id": stage["shot_id"]})
            manifest["stages"][args.stage] = result
        manifest["inspection"] = inspect_assets(store, manifest)
        manifest["budget"] = store.budget()
        manifest["jobs"] = [{key: job.get(key) for key in ("id", "provider", "model", "category", "status", "cost_usd", "cost_basis", "remote_id")} for job in store.snapshot()["jobs"]]
        manifest["scene_id"] = store.scene()["id"] if store.scene() else None
        manifest["updated_at"] = now_iso()
        save(checkpoint, manifest)
        print(json.dumps({"checkpoint": str(checkpoint), "stage": args.stage, "scene_id": manifest["scene_id"],
            "inspection": manifest["inspection"], "budget": manifest["budget"], "jobs": manifest["jobs"]}, indent=2))
    finally:
        await service.runner.http.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="work/fresh-reference-example")
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("stage", choices=("init", "generate", "approve-references", "reconstruct", "review-place", "design", "compose", "motion", "compile", "inspect"))
    parser.add_argument("--asset", choices=tuple(DEFINITIONS))
    parser.add_argument("--approve-paid", action="store_true")
    parser.add_argument("--cost-cap", type=float, default=1)
    parser.add_argument("--approval-reason")
    parser.add_argument("--review-file")
    parser.add_argument("--design-file")
    args = parser.parse_args()
    if args.stage in {"approve-references", "review-place"} and not args.review_file:
        parser.error("This review stage requires --review-file with explicit visual assessments")
    if args.stage == "design" and not args.design_file:
        parser.error("Room adaptation requires --design-file with the observed master reference")
    with experiment_lock(Path(args.root).resolve()):
        asyncio.run(execute(args))


if __name__ == "__main__":
    main()
