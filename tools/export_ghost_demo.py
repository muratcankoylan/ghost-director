#!/usr/bin/env python3
"""Export reviewed creative assets; import them into a fresh, paused production.

No provider client is imported and no source SQLite state is modified. Bundles
contain selected creative evidence, never executable job/command history.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
from urllib.parse import quote, urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ghost_director.domain.models import AgentTask, Observation, SceneRevision, ShotPlan, Take
from ghost_director.jobs.store import ProductionStore
from ghost_director.runtime.redaction import Redactor
from tools.ghost_demo_references import collect_reference_lineage, validate_reference_lineage, import_reference_registry

FORMAT = "ghost-director-demo/1"
ALLOWED_KINDS = {"image", "frame", "mask", "video", "model", "blender_scene", "prompt", "text", "observation", "design_receipt", "render_receipt", "reference_original", "motion_receipt", "normalization_receipt"}
MAX_FILE_BYTES = 250_000_000
MAX_BUNDLE_BYTES = 1_000_000_000
URL = re.compile(r"https?://[^\s\"<>\x00]+")
BINARY_URL = re.compile(rb"https?://[^\s\"<>\x00]+")
KEY = re.compile(rb"\b(?:sk-[A-Za-z0-9_-]{16,}|fal_[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,})\b")
PRIVATE_FIELDS = {"authorization", "api_key", "apikey", "access_token", "refresh_token", "secret", "password", "cookie", "set_cookie", "headers", "request", "raw_request", "response", "raw_response", "request_ref", "context_ref", "context_id", "owner_pid", "polling_locator", "remote_id"}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Bundle path must be a relative POSIX path.")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts or parsed.as_posix() != relative:
        raise ValueError("Bundle path is outside its root.")
    current = root
    for part in parsed.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinks are not permitted in demo bundles.")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError("Bundle path escapes its root.")
    return current


def read_file(root, relative, expected=None):
    path = safe_path(root, relative)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Required demo file is missing or exceeds the size limit.")
    data = path.read_bytes()
    if expected is not None and digest(data) != expected:
        raise ValueError("Demo file hash does not match its recorded version.")
    return data


class ReleaseSanitizer:
    def __init__(self, secrets=()):
        variants = set()
        for secret in secrets:
            if secret:
                variants.update((secret, quote(secret, safe=""), base64.b64encode(secret.encode()).decode(), json.dumps(secret)[1:-1]))
        self.redactor = Redactor(tuple(variants))
        self.needles = tuple(value.encode() for value in variants)

    def value(self, value):
        if isinstance(value, dict):
            return {str(key): self.value(child) for key, child in value.items()
                    if str(key).lower().replace("-", "_") not in PRIVATE_FIELDS}
        if isinstance(value, (list, tuple)):
            return [self.value(child) for child in value]
        if isinstance(value, str):
            value = self.redactor.text(value)
            value = re.sub(r"data:[^\s\"<>]+", "[EMBEDDED_DATA_OMITTED]", value)
            value = re.sub(r"(?:/Users/|/home/|/private/|[A-Za-z]:\\\\)[^\s\"<>]+", "[LOCAL_PATH_OMITTED]", value)
            # Redactor strips signed queries. Also strip URL user information.
            return URL.sub(lambda match: match.group(0).replace(urlsplit(match.group(0)).netloc, urlsplit(match.group(0)).hostname or "") if urlsplit(match.group(0)).username else match.group(0), value)
        return value

    def check(self, data: bytes):
        if any(needle in data for needle in self.needles) or KEY.search(data):
            raise ValueError("Sensitive credential content detected; no bundle was published.")
        cursor = 0
        while match := BINARY_URL.search(data, cursor):
            raw = match.group(0)
            # C2PA provenance stores IPTC vocabulary URIs in CBOR text strings,
            # without a trailing quote/NUL. Respect their explicit byte length;
            # adjacent CBOR fields are not part of the URL and must remain intact.
            for width, marker in ((1, 0x78), (2, 0x79), (4, 0x7A), (8, 0x7B)):
                start = match.start()
                if start > width and data[start - width - 1] == marker:
                    size = int.from_bytes(data[start - width:start], "big")
                    if 8 <= size <= MAX_FILE_BYTES and start + size <= len(data):
                        candidate = data[start:start + size]
                        try:
                            candidate.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        raw = candidate
                        break
            # C2PA also embeds DER X.509 certificates. GeneralName tag [6]
            # contains an IA5 URI with a short/long DER length, not a delimiter.
            start = match.start()
            if start >= 2 and data[start - 2] == 0x86 and data[start - 1] < 0x80:
                size = data[start - 1]
                if size >= 8 and start + size <= len(data):
                    raw = data[start:start + size]
            else:
                for width in range(1, 5):
                    if start >= width + 2 and data[start - width - 2:start - width] == bytes((0x86, 0x80 + width)):
                        size = int.from_bytes(data[start - width:start], "big")
                        if 8 <= size <= MAX_FILE_BYTES and start + size <= len(data):
                            raw = data[start:start + size]
                        break
            cursor = match.start() + len(raw)
            parsed = urlsplit(raw.decode("utf-8", errors="ignore"))
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("Signed or credential-bearing URL detected in a binary artifact.")


def checked_media(data, kind, mime):
    """Reject executable/text disguises and GLBs requiring remote fetches."""
    if kind not in ALLOWED_KINDS:
        raise ValueError("Artifact kind is excluded from release bundles.")
    if kind in {"image", "frame", "mask", "reference_original"}:
        if not ((mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or (mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")) or (mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
            raise ValueError("Only PNG/JPEG/WebP raster artifacts can be exported.")
    elif kind == "video":
        if mime != "video/mp4" or data[4:8] != b"ftyp":
            raise ValueError("Only MP4 video artifacts can be exported.")
    elif kind == "model":
        if mime != "model/gltf-binary" or len(data) < 20 or data[:4] != b"glTF":
            raise ValueError("Model must be a self-contained GLB.")
        _, version, length = struct.unpack_from("<4sII", data)
        chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
        if version != 2 or length != len(data) or chunk_type != 0x4E4F534A or 20 + chunk_length > len(data):
            raise ValueError("Invalid GLB container.")
        scene = json.loads(data[20:20 + chunk_length])
        for row in scene.get("buffers", []) + scene.get("images", []):
            if "uri" in row:
                raise ValueError("GLB external or data URIs must be embedded as buffer views before export.")
    elif kind == "blender_scene":
        if not data.startswith(b"BLENDER"):
            raise ValueError("Only uncompressed Blender files can be scanned for release.")
    elif mime not in {"text/plain", "application/json"}:
        raise ValueError("Selected text evidence must be plain text or JSON.")


def decompress_blend(path: Path) -> bytes:
    """Bound a Zstandard authoring file's expansion without opening Blender."""
    executable = shutil.which("zstd")
    if not executable:
        raise ValueError("The zstd executable is required to export compressed Blender sources.")
    process = subprocess.Popen([executable, "--decompress", "--stdout", "--no-progress", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(60, process.kill)
    timer.daemon = True
    timer.start()
    chunks, size = [], 0
    try:
        while chunk := process.stdout.read(1_000_000):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError("Decompressed Blender source exceeds the release size limit.")
            chunks.append(chunk)
        if process.wait() != 0:
            raise ValueError("Blender source decompression failed or exceeded its time limit.")
        return b"".join(chunks)
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def artifact_content(root, record, sanitizer, *, context_manifest=None):
    data = read_file(root, record.get("path", record["relative_path"]), record["sha256"])
    kind, mime = record["kind"], record["mime_type"]
    if kind not in ALLOWED_KINDS:
        raise ValueError("Provider requests, raw responses, contexts and unclassified documents cannot be exported.")
    transformation = "byte-identical"
    if kind == "blender_scene" and data.startswith(b"\x28\xb5\x2f\xfd"):
        data = decompress_blend(safe_path(root, record.get("path", record["relative_path"])))
        transformation = "Zstandard decompression; Blender datablocks unchanged; source compressed hash retained"
    elif mime == "application/json":
        value = json.loads(data)
        if kind in {"design_receipt", "render_receipt"}:
            if not isinstance(value, dict):
                raise ValueError("Local render and design receipts must be JSON objects.")
            allowed = {"scene_revision_id", "scene_id", "blender_version", "source_sha256", "scene_sha256", "scene_file_sha256", "room", "room_mesh_count", "surface_slots", "room_sha256", "blend_sha256", "asset_mapping_sha256", "blend_source_sha256", "renderer_profile", "quality", "requested_quality", "render_policy", "render_kind", "lights", "renders"}
            value = {key: child for key, child in value.items() if key in allowed}
            if "renders" in value:
                fields = {"camera_id", "sha256", "width", "height", "camera", "resolved_focus_distance_m", "depth_of_field", "aperture_fstop", "render_seconds"}
                value["renders"] = [{key: child for key, child in row.items() if key in fields} for row in value["renders"]]
        if kind == "prompt" and isinstance(value, dict) and "messages" in value:
            # Application system prompts contain imported Studio OS text. Keep
            # user task text; preserve source hash so this is not an exact-call claim.
            value["messages"] = [row for row in value["messages"] if row.get("role") == "user"]
            value["export_view"] = "Historical application prompt derivative; user messages retained, loaded system/context text omitted"
            if context_manifest is not None:
                value["context_manifest"] = context_manifest
        data = encoded(sanitizer.value(value))
        transformation = "sanitized JSON; provider transport and system/context text omitted"
    elif mime == "text/plain":
        data = sanitizer.value(data.decode()).encode()
        transformation = "sanitized text"
    checked_media(data, kind, mime)
    sanitizer.check(data)
    return data, transformation


def context_pins(production, artifact):
    """Explicitly project provenance; never recursively copy loaded context text."""
    if artifact["kind"] != "context":
        raise ValueError("Task context reference must identify a context artifact.")
    context = json.loads(read_file(production, artifact["path"], artifact["sha256"]))
    pin_fields = ("path", "sha256", "bytes", "tier")
    resolver = context.get("resolver", {})
    return {
        "source_sha256": artifact["sha256"],
        **{key: context[key] for key in ("role_id", "persona_id", "domain", "loaded_bytes", "context_source") if key in context},
        "files": [{key: row[key] for key in pin_fields if key in row} for row in context.get("files", [])],
        "resolver": {**{key: resolver[key] for key in (*pin_fields, "bundle_sha256", "source_commit") if key in resolver},
                     "registry_pins": [{key: row[key] for key in pin_fields if key in row} for row in resolver.get("registry_pins", [])]},
        "loaded_text_omitted": True,
    }


def export_bundle(production: Path, output: Path, *, artifact_ids=(), take_ids=(), run_ids=(), revision_id=None, secrets=(), include_uploaded_originals=False, review_note="Owner review pending; packaging is not publication or a license grant."):
    production, output = production.resolve(), output.absolute()
    if output.exists():
        raise ValueError("Export destination must not exist.")
    sanitizer = ReleaseSanitizer(secrets)
    source_db = safe_path(production, "runtime.sqlite3")
    with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        settings = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM settings")}
        if not settings.get("accepted_head"):
            raise ValueError("Production has no accepted scene revision.")
        artifacts = {row["id"]: row for (raw,) in db.execute("SELECT data FROM artifacts") for row in [json.loads(raw)]}
        chosen_scene = db.execute("SELECT artifact_id FROM scenes WHERE id=?", (revision_id or settings["accepted_head"],)).fetchone()
        if not chosen_scene:
            raise ValueError("Selected scene revision is not recorded in this production.")
        scene_artifact = chosen_scene[0]
        scene = json.loads(read_file(production, artifacts[scene_artifact]["path"], artifacts[scene_artifact]["sha256"]))
        records = {}
        for collection, raw in db.execute("SELECT collection,data FROM records ORDER BY rowid"):
            records.setdefault(collection, []).append(json.loads(raw))
        all_runs = {row["id"]: row for (raw,) in db.execute("SELECT data FROM runs") for row in [json.loads(raw)]}
        all_tasks = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM tasks ORDER BY rowid")]
        all_dispositions = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM dispositions ORDER BY rowid")]
        sequence = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    SceneRevision.model_validate(scene)
    selected = set(artifact_ids)
    asset_keys, blend_id = {}, None
    for entity in scene["entities"]:
        state = entity.get("state", {})
        if state.get("artifact_id"):
            selected.add(state["artifact_id"])
            if entity.get("asset_id"):
                asset_keys[entity["asset_id"]] = state["artifact_id"]
        if state.get("blend_artifact_id"):
            if blend_id and blend_id != state["blend_artifact_id"]:
                raise ValueError("Accepted scene refers to conflicting Blender sets.")
            blend_id = state["blend_artifact_id"]
            selected.add(blend_id)
        if state.get("reference_artifact_id"):
            selected.add(state["reference_artifact_id"])
        if state.get("design_receipt_artifact_id"):
            selected.add(state["design_receipt_artifact_id"])
        if state.get("normalization_receipt_artifact_id"):
            selected.add(state["normalization_receipt_artifact_id"])
    takes = [row for row in records.get("takes", []) if row["id"] in set(take_ids)]
    if set(take_ids) != {row["id"] for row in takes}:
        raise ValueError("Selected take does not exist.")
    packages = [row for row in records.get("packages", []) if row["id"] in {take["package_id"] for take in takes} or row.get("run_id") in set(run_ids)]
    if not {take["package_id"] for take in takes}.issubset({package["id"] for package in packages}):
        raise ValueError("Selected take has no immutable prompt package.")
    observations = [row for row in records.get("observations", []) if row["take_id"] in set(take_ids)]
    for take in takes:
        selected.update(ref for ref in (take["artifact_id"], take.get("poster_id")) if ref)
    for package in packages:
        selected.update(package.get("reference_ids", []))
        selected.update(package.get("planning_reference_ids", []))
        if package.get("prompt_ref"):
            selected.add(package["prompt_ref"])
    for observation in observations:
        selected.update(observation.get("evidence_ids", []))
    selected_runs = set(run_ids) | {row["run_id"] for row in (*takes, *packages) if row.get("run_id")}
    reference_lineage, reference_artifacts = collect_reference_lineage(records, scene, records.get("shots", []), packages, selected_runs)
    selected.update(reference_artifacts)
    selected_runs.update(item['run_id'] for collection in ('motion_previews', 'reconstructions') for item in reference_lineage[collection] if item.get('run_id'))
    # Older recovered takes can belong to a different run from their planner.
    # Follow the actual immutable prompt output, not a guessed adjacent run.
    prompt_refs = {package.get("prompt_ref") for package in packages}
    selected_runs.update(task["run_id"] for task in all_tasks
                         if prompt_refs.intersection([*task.get("output_refs", []), *task.get("result", {}).get("output_refs", [])]))
    if not selected_runs.issubset(all_runs):
        raise ValueError("Selected historical run does not exist.")
    runs = [{key: row[key] for key in ("id", "name", "mode", "status", "created_at", "updated_at", "initial_scene_revision", "shot_id") if key in row}
            for row in all_runs.values() if row["id"] in selected_runs]
    tasks, prompt_contexts = [], {}
    for original in all_tasks:
        if original["run_id"] not in selected_runs:
            continue
        task = {key: original[key] for key in ("id", "run_id", "role_id", "role_label", "model", "persona_id", "scene_revision_id", "goal", "status", "attempt", "created_at", "updated_at", "prompt_ref", "dependency_ids", "input_refs") if key in original}
        outputs = list(dict.fromkeys([*original.get("output_refs", []), *original.get("result", {}).get("output_refs", [])]))
        task["output_refs"] = outputs
        task["result"] = {"output_refs": outputs}
        if original.get("result", {}).get("error"):
            task["result"]["error"] = original["result"]["error"]
        selected.update([*task.get("input_refs", []), *outputs])
        if task.get("prompt_ref"):
            selected.add(task["prompt_ref"])
        if original.get("context_ref"):
            context = artifacts.get(original["context_ref"])
            if not context:
                raise ValueError("Selected task context provenance is missing.")
            task["context_manifest"] = context_pins(production, context)
            if task.get("prompt_ref"):
                prompt_contexts[task["prompt_ref"]] = task["context_manifest"]
        tasks.append(task)
    selected_tasks = {task["id"] for task in tasks}
    if any(not set(task.get("dependency_ids", [])).issubset(selected_tasks) for task in tasks):
        raise ValueError("Selected task has an unselected dependency.")
    handoffs = [row for row in records.get("handoffs", []) if row.get("from_task_id") in selected_tasks and row.get("to_task_id") in selected_tasks]
    for row in handoffs:
        selected.update([*row.get("input_refs", []), *row.get("artifact_refs", [])])
    dispositions = [row for row in all_dispositions if row["take_id"] in set(take_ids)]
    for artifact_id in list(selected):
        receipt_id = artifacts.get(artifact_id, {}).get("metadata", {}).get("receipt_artifact_id")
        if receipt_id:
            if artifacts.get(receipt_id, {}).get("kind") != "render_receipt":
                raise ValueError("Render provenance must identify a recorded render receipt.")
            selected.add(receipt_id)
    if not selected.issubset(artifacts):
        raise ValueError("An explicitly selected or required artifact is missing.")
    uploaded_originals = [identifier for identifier in selected if artifacts[identifier]['kind'] == 'reference_original']
    if uploaded_originals and not include_uploaded_originals:
        raise ValueError("Reference lineage includes byte-original uploads, which may retain EXIF metadata. Export requires explicit include_uploaded_originals=True (CLI: --include-uploaded-originals); no original was silently replaced by a derivative.")
    manifest_path = production / "assets/asset-manifest.json"
    asset_manifest = json.loads(read_file(production, "assets/asset-manifest.json")) if manifest_path.is_file() else {"assets": [], "textures": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ghost-demo-", dir=output.parent))
    files, exported, total = {}, [], 0

    def write(relative, data):
        nonlocal total
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("Demo bundle exceeds its size limit.")
        sanitizer.check(data)
        path = safe_path(stage, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[relative] = {"sha256": digest(data), "bytes": len(data)}

    try:
        for artifact_id in sorted(selected):
            record = artifacts[artifact_id]
            data, transformation = artifact_content(production, record, sanitizer, context_manifest=prompt_contexts.get(artifact_id))
            relative = "artifacts/" + digest(data)
            if relative not in files:
                write(relative, data)
            exported.append(sanitizer.value({key: record[key] for key in ("id", "kind", "mime_type", "name", "created_at", "metadata") if key in record}) | {
                "relative_path": relative, "sha256": digest(data), "bytes": len(data),
                "source_sha256": record["sha256"], "export_transformation": transformation,
            })
        textures = []
        for row in asset_manifest.get("textures", []):
            relative = "assets/" + row["path"]
            data = read_file(production, relative, row["sha256"])
            checked_media(data, "image", "image/png" if row["path"].endswith(".png") else "image/jpeg")
            write(relative, data)
            textures.append(sanitizer.value(row))
        asset_rows = []
        for key, artifact_id in asset_keys.items():
            record = artifacts[artifact_id]
            source = next((row for row in asset_manifest.get("assets", []) if row.get("id") == key and row.get("sha256") == record["sha256"]), {})
            asset_rows.append(sanitizer.value({**source, "id": key, "artifact_id": artifact_id, "license": source.get("license") or record.get("metadata", {}).get("license") or "Owner license decision pending"}))
        # Keep source scenes for historical take provenance without treating them
        # as revisions approved in a new importing user's production.
        source_scenes = [scene]
        with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as db:
            historical_revisions = {row["scene_revision_id"] for row in (*takes, *tasks) if row.get("scene_revision_id")}
            historical_revisions.update(row["initial_scene_revision"] for row in runs if row.get("initial_scene_revision"))
            for revision in sorted(historical_revisions - {scene["id"]}):
                row = db.execute("SELECT artifact_id FROM scenes WHERE id=?", (revision,)).fetchone()
                if not row or row[0] not in artifacts:
                    raise ValueError("Selected take's source scene is unavailable.")
                record = artifacts[row[0]]
                source_scenes.append(json.loads(read_file(production, record["path"], record["sha256"])))
        payload = sanitizer.value({"scene": scene, "source_scenes": source_scenes, "shots": records.get("shots", []), "takes": takes, "packages": packages, "observations": observations, "runs": runs, "tasks": tasks, "handoffs": handoffs, "dispositions": dispositions, **reference_lineage})
        validate_reference_lineage(payload, {artifact["id"]: artifact for artifact in exported})
        write("creative.json", encoded(payload))
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            commit = None
        manifest = sanitizer.value({
            "format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {"production_id": settings["production_id"], "accepted_revision_id": scene["id"], "accepted_head_at_export": settings["accepted_head"], "historical_scene_selection": scene["id"] != settings["accepted_head"], "selected_run_ids": sorted(selected_runs), "last_seq": sequence, "repository_head": commit, "working_tree_may_include_uncommitted_changes": True},
            "review_note": review_note, "license": "No new license granted; preserve per-asset and upstream terms.",
            "uploaded_originals_included": bool(uploaded_originals),
            "artifacts": exported, "assets": asset_rows, "textures": textures, "blend_artifact_id": blend_id,
            "exclusions": ["SQLite and WAL", "credentials", "jobs and billing", "commands and original events", "provider request/response blobs", "loaded role/system contexts", "unselected media"],
            "files": files,
        })
        final = encoded(manifest)
        sanitizer.check(final)
        (stage / "manifest.json").write_bytes(final)
        stage.rename(output)
        return {"bundle": str(output), "files": len(files), "bytes": total + len(final), "artifacts": len(exported), "takes": len(takes), "tasks": len(tasks), "handoffs": len(handoffs), "source_revision": scene["id"]}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_bundle(bundle: Path):
    bundle = bundle.resolve()
    manifest = json.loads(read_file(bundle, "manifest.json"))
    if manifest.get("format") != FORMAT or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported demo bundle format.")
    if len(manifest["files"]) > 1000 or sum(row["bytes"] for row in manifest["files"].values()) > MAX_BUNDLE_BYTES:
        raise ValueError("Bundle exceeds limits.")
    sanitizer = ReleaseSanitizer()
    sanitizer.check(encoded(manifest))
    for path, record in manifest["files"].items():
        data = read_file(bundle, path, record["sha256"])
        if len(data) != record["bytes"]:
            raise ValueError("Bundle file length differs from its manifest.")
        sanitizer.check(data)
    for artifact in manifest["artifacts"]:
        if artifact["relative_path"] not in manifest["files"]:
            raise ValueError("Artifact is not in the bundle file inventory.")
        data = read_file(bundle, artifact["relative_path"], artifact["sha256"])
        checked_media(data, artifact["kind"], artifact["mime_type"])
    if "creative.json" not in manifest["files"]:
        raise ValueError("Creative snapshot is missing.")
    creative = json.loads(read_file(bundle, "creative.json"))
    SceneRevision.model_validate(creative["scene"])
    for key, model in (("shots", ShotPlan), ("takes", Take), ("observations", Observation)):
        for value in creative.get(key, []):
            model.model_validate(value)
    tasks = creative.get("tasks", [])
    task_ids = {task["id"] for task in tasks}
    if len(tasks) != len(task_ids):
        raise ValueError("Historical task IDs must be unique.")
    artifacts = {artifact["id"]: artifact for artifact in manifest["artifacts"]}
    if len(artifacts) != len(manifest["artifacts"]):
        raise ValueError("Bundle artifact identities must be unique.")
    artifact_ids = set(artifacts)
    validate_reference_lineage(creative, artifacts)
    for task in tasks:
        AgentTask.model_validate({key: value for key, value in task.items() if key in AgentTask.model_fields})
        if not set(task.get("dependency_ids", [])).issubset(task_ids):
            raise ValueError("Historical task dependency is missing.")
        refs = [*task.get("input_refs", []), *task.get("output_refs", []), *task.get("result", {}).get("output_refs", [])]
        if task.get("prompt_ref"):
            refs.append(task["prompt_ref"])
        if not set(refs).issubset(artifact_ids):
            raise ValueError("Historical task artifact is missing.")
    for handoff in creative.get("handoffs", []):
        if handoff.get("from_task_id") not in task_ids or handoff.get("to_task_id") not in task_ids:
            raise ValueError("Historical handoff endpoint is missing.")
    return manifest, creative


def import_bundle(bundle: Path, destination: Path):
    manifest, creative = verify_bundle(bundle)
    destination = destination.absolute()
    if destination.exists():
        raise ValueError("Import needs a new destination; existing productions are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ghost-import-", dir=destination.parent))
    try:
        store = ProductionStore(stage, opening_entries=())
        store.set_paused(True)
        imported = {}
        # Receipt dependencies precede images, so immutable artifact metadata can
        # bind local provenance on its first publication instead of dangling IDs.
        for original in sorted(manifest["artifacts"], key=lambda row: row["kind"] not in {"design_receipt", "render_receipt"}):
            data = read_file(bundle.resolve(), original["relative_path"], original["sha256"])
            metadata = dict(original.get("metadata", {}))
            if metadata.get("receipt_artifact_id"):
                receipt = imported.get(metadata["receipt_artifact_id"])
                if not receipt or receipt["kind"] != "render_receipt":
                    raise ValueError("Imported image render receipt is missing.")
                metadata["receipt_artifact_id"] = receipt["id"]
            imported[original["id"]] = store.publish_artifact(data, kind=original["kind"], mime_type=original["mime_type"], metadata={
                **metadata, "name": original.get("name", original["id"]),
                "demo_source_artifact_id": original["id"], "demo_source_sha256": original["source_sha256"],
                "export_transformation": original["export_transformation"], "evidence_origin": "imported recorded artifact",
            })
        imported_task_ids = {task["id"]: "task-import-" + uuid4().hex[:16] for task in creative.get("tasks", [])}

        def rebind(value):
            if isinstance(value, dict):
                result = {}
                for key, child in value.items():
                    if key.endswith("artifact_id") and isinstance(child, str) and child not in imported:
                        continue
                    result[key] = rebind(child)
                return result
            if isinstance(value, list):
                return [rebind(child) for child in value]
            if isinstance(value, str):
                if value in imported_task_ids:
                    return imported_task_ids[value]
                if value in imported:
                    return imported[value]["id"]
                for source_id, new in imported.items():
                    if value == f"/api/productions/{manifest['source']['production_id']}/artifacts/{source_id}/content":
                        return new["url"]
            return value

        import_reference_registry(store, creative, rebind)

        assets = stage / "assets"
        assets.mkdir()
        mapping, asset_rows = {}, []
        for row in manifest["assets"]:
            artifact = imported[row["artifact_id"]]
            if artifact["kind"] != "model" or not re.fullmatch(r"[A-Za-z0-9_.-]+", row["id"]):
                raise ValueError("Invalid imported model mapping.")
            asset_key = rebind(row["id"])
            path = asset_key + ".glb"
            shutil.copyfile(store.artifact_path(artifact["id"]), assets / path)
            mapping[asset_key] = path
            asset_rows.append({**row, "id": asset_key, "path": path, "sha256": artifact["sha256"], "artifact_id": artifact["id"], "bytes": artifact["bytes"]})
        if manifest.get("blend_artifact_id"):
            artifact = imported[manifest["blend_artifact_id"]]
            if artifact["kind"] != "blender_scene":
                raise ValueError("Blender mapping must identify a Blender artifact.")
            shutil.copyfile(store.artifact_path(artifact["id"]), assets / "dinner-set.blend")
        for row in manifest["textures"]:
            relative = "assets/" + row["path"]
            if relative not in manifest["files"]:
                raise ValueError("Texture is outside the verified inventory.")
            target = safe_path(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_file(bundle.resolve(), relative, row["sha256"]))
        (assets / "asset-map.json").write_bytes(encoded(mapping))
        (assets / "asset-manifest.json").write_bytes(encoded({"schema_version": "1.0", "assets": asset_rows, "textures": manifest["textures"], "status": "Imported demo; original quality and license limitations retained"}))
        scene = rebind(creative["scene"])
        scene.update(id="scene-import-" + uuid4().hex[:16], parent_id=None, restored_from_id=None)
        for entity in scene["entities"]:
            state = entity.get("state", {})
            if state.get("artifact_id"):
                entity["asset_url"] = store.artifact(state["artifact_id"])["url"]
            elif entity.get("asset_url"):
                entity["asset_url"] = None
        scene = SceneRevision.model_validate(scene).model_dump(mode="json")
        (assets / "scene.template.json").write_bytes(encoded(scene))
        store.publish_scene(scene, expected_revision=None, actor="demo-import")
        for shot in creative.get("shots", []):
            shot = rebind(shot)
            shot["scene_revision_id"] = scene["id"]
            shot["reference_ids"] = [ref for ref in shot.get("reference_ids", []) if ref in {row["id"] for row in imported.values()}]
            store.upsert_record("shots", shot)
        source = store.publish_artifact({"source": manifest["source"], "review_note": manifest["review_note"], "creative": creative}, kind="document", metadata={"name": "Imported demo source lineage; historical evidence"})
        run = store.create_run(metadata={"name": "Imported demo · recorded evidence", "evidence_origin": "imported", "source_lineage_ref": source["id"], "source_run_ids": [row["id"] for row in creative.get("runs", [])], "provider_calls_executed": 0})
        pending = list(creative.get("tasks", []))
        created = set()
        while pending:
            ready = [task for task in pending if set(task.get("dependency_ids", [])).issubset(created)]
            if not ready:
                raise ValueError("Historical task dependencies contain a cycle.")
            for original in ready:
                record = rebind(original)
                record.update(run_id=run["id"], status="imported", evidence_origin="imported", source_task_id=original["id"], source_run_id=original["run_id"], source_status=original["status"], provider_calls_executed=0)
                store.create_task(record)
                created.add(original["id"])
                pending.remove(original)
        for original in creative.get("handoffs", []):
            record = rebind(original)
            record.update(id="handoff-import-" + uuid4().hex[:16], run_id=run["id"], evidence_origin="imported", source_handoff_id=original["id"], source_run_id=original.get("run_id"), provider_calls_executed=0)
            store.upsert_record("handoffs", record, run_id=run["id"], event_type="demo.handoff_imported")
        for collection in ("packages", "takes", "observations", "motion_previews", "reconstructions", "reconstruction_reviews"):
            for record in creative.get(collection, []):
                record = rebind(record)
                if "run_id" in record:
                    record["run_id"] = run["id"]
                if collection in {"motion_previews", "reconstructions"}:
                    record.update(evidence_origin="imported recorded artifact", provider_calls_executed=0)
                if collection == "reconstruction_reviews":
                    from ghost_director.reference_reconstruction import ReconstructionReview
                    record["actor"] = ("Imported historical record: " + record["actor"])[:120]
                    record["artifact_sha256"] = store.artifact(record["artifact_id"])["sha256"]
                    record = ReconstructionReview.model_validate(record).model_dump(mode="json")
                if collection == "packages":
                    # Rebound media and scene IDs are new local inputs. The source
                    # digest/approval is evidence only, never authorization to pay.
                    record.pop("review_sha256", None)
                    record.pop("approval", None)
                    record.update(imported_requires_recompile=True, evidence_origin="imported recorded package; recompile and review locally before generation")
                store.upsert_record(collection, record, run_id=run["id"], event_type="demo.record_imported")
        for disposition in creative.get("dispositions", []):
            store.append_event("demo.disposition_imported", {**rebind(disposition), "evidence_origin": "imported", "provider_calls_executed": 0}, run_id=run["id"])
        store.append_event("demo.imported", {"source": manifest["source"], "lineage_ref": source["id"], "provider_calls_executed": 0, "source_jobs_and_budget_imported": False}, run_id=run["id"])
        store.finish_run(run["id"])
        stage.rename(destination)
        return {"production": str(destination), "revision_id": scene["id"], "paused": True, "provider_jobs": 0, "spent_usd": 0, "artifacts": len(imported), "tasks": len(imported_task_ids), "handoffs": len(creative.get("handoffs", []))}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Read-only export of an explicitly selected demo")
    export.add_argument("--production", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--artifact", action="append", default=[])
    export.add_argument("--take", action="append", default=[])
    export.add_argument("--run", action="append", default=[], help="Include sanitized historical crew tasks, handoffs and their input/output artifacts")
    export.add_argument("--revision", help="Pin a recorded accepted scene revision instead of the current head")
    export.add_argument("--env-file", type=Path, help="Read known secret values privately for binary scanning; never exported")
    export.add_argument("--review-note", default="Owner review pending; packaging is not publication or a license grant.")
    export.add_argument("--include-uploaded-originals", action="store_true", help="Explicitly include required byte-original uploads, including any retained metadata; otherwise such bundles fail before writing")
    verify = sub.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    restore = sub.add_parser("import")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--production", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            from ghost_director.config import load_settings
            if args.env_file is not None and not args.env_file.is_file():
                raise ValueError("Explicit export credential-scan configuration does not exist.")
            settings = load_settings(args.env_file)
            result = export_bundle(args.production, args.output, artifact_ids=args.artifact, take_ids=args.take, run_ids=args.run, revision_id=args.revision, secrets=settings.secrets, include_uploaded_originals=args.include_uploaded_originals, review_note=args.review_note)
        elif args.command == "verify":
            manifest, _ = verify_bundle(args.bundle)
            result = {"verified": True, "files": len(manifest["files"]), "artifacts": len(manifest["artifacts"])}
        else:
            result = import_bundle(args.bundle, args.production)
        print(json.dumps(result, indent=2))
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        # Paths and content from untrusted bundles are not reflected in errors.
        print(json.dumps({"error": "Bundle operation failed", "type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
