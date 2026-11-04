"""Release boundary tests use synthetic media; they make no provider calls."""
import json
from pathlib import Path
import struct
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io

from ghost_director.domain.models import SceneRevision, Entity, Camera, ShotPlan, Take, Observation
from ghost_director.jobs.store import ProductionStore
from tools.export_ghost_demo import ReleaseSanitizer, checked_media, digest, encoded, export_bundle, import_bundle, verify_bundle


def glb(extra=None):
    payload = json.dumps({"asset": {"version": "2.0"}, **(extra or {})}).encode()
    payload += b" " * (-len(payload) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(payload), len(payload), 0x4E4F534A) + payload


class DemoBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.store = ProductionStore(self.source, opening_entries=())
        self.model = self.store.publish_artifact(glb(), kind="model", mime_type="model/gltf-binary", metadata={"asset_key": "room-v1", "license": "Owner decision pending"})
        self.blend = self.store.publish_artifact(b"BLENDER-v300 synthetic fixture", kind="blender_scene", mime_type="application/octet-stream")
        # Headers suffice for the release container policy; these are not visual acceptance fixtures.
        self.image = self.store.publish_artifact(b"\x89PNG\r\n\x1a\nfixture", kind="image", mime_type="image/png")
        scene = SceneRevision(id="source-scene", entities=[Entity(id="room", name="Room", kind="room", asset_id="room-v1", asset_url=self.model["url"], state={"artifact_id": self.model["id"], "blend_artifact_id": self.blend["id"], "reference_artifact_id": self.image["id"]})], cameras=[Camera(id="cam", name="Camera", position=(0, -2, 1), target=(0, 0, 1))])
        self.store.publish_scene(scene.model_dump(mode="json"), expected_revision=None, actor="fixture-owner")
        self.store.upsert_record("shots", ShotPlan(id="shot", name="Shot", scene_revision_id=scene.id, camera_id="cam").model_dump(mode="json"))
        self.request = self.store.publish_artifact({"private": "provider request must be excluded"}, kind="request")
        (self.source / ".env.local").write_text("PRIVATE_SENTINEL=must-not-export\n")
        self.output = self.root / "bundle"

    def export(self, **kwargs):
        return export_bundle(self.source, self.output, **kwargs)

    def test_export_is_read_only_and_import_has_new_scene_zero_spend_no_jobs(self):
        before = self.store.snapshot()
        report = self.export()
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(report["artifacts"], 3)
        manifest, creative = verify_bundle(self.output)
        self.assertEqual(creative["scene"]["id"], "source-scene")
        self.assertFalse(any(path.name.startswith(".env") or "sqlite" in path.name for path in self.output.rglob("*")))
        self.assertNotIn(self.request["id"], {row["id"] for row in manifest["artifacts"]})
        restored = self.root / "restored"
        receipt = import_bundle(self.output, restored)
        store = ProductionStore(restored, opening_entries=())
        self.assertTrue(receipt["paused"])
        self.assertTrue(store.snapshot()["production"]["paused"])
        self.assertEqual(store.budget()["committed_usd"], 0)
        self.assertEqual(store.snapshot()["jobs"], [])
        self.assertNotEqual(store.scene()["id"], "source-scene")
        entity = store.scene()["entities"][0]
        self.assertNotEqual(entity["state"]["artifact_id"], self.model["id"])
        self.assertEqual(store.artifact(entity["state"]["artifact_id"])["sha256"], self.model["sha256"])
        self.assertEqual(store.artifact_path(entity["state"]["blend_artifact_id"]).read_bytes(), self.store.artifact_path(self.blend["id"]).read_bytes())
        self.assertTrue((restored / "assets/room-v1.glb").exists())
        self.assertTrue((restored / "assets/dinner-set.blend").exists())
        self.assertTrue(any(event["type"] == "demo.imported" for event in store.events()))

    def test_import_resets_undo_pointer_but_preserves_original_lineage(self):
        scene = self.store.scene()
        scene.update(id="undo-scene", parent_id="source-scene", restored_from_id="source-scene")
        self.store.publish_scene(scene, expected_revision="source-scene", actor="fixture-owner")
        self.export()
        destination = self.root / "restored"
        import_bundle(self.output, destination)
        restored = ProductionStore(destination, opening_entries=())
        snapshot = restored.snapshot()
        self.assertIsNone(snapshot["scene"]["restored_from_id"])
        self.assertIsNone(snapshot["undo_target_revision_id"])
        lineage = next(row for row in snapshot["artifacts"] if row["metadata"].get("name") == "Imported demo source lineage; historical evidence")
        original = json.loads(restored.artifact_path(lineage["id"]).read_text())
        self.assertEqual(original["creative"]["scene"]["restored_from_id"], "source-scene")

    def test_new_design_and_preview_receipts_are_sanitized_and_rebound(self):
        design = self.store.publish_artifact({"scene_revision_id": "edited", "source_sha256": "abc", "private_path": str(self.source / "source.blend")}, kind="design_receipt")
        render = self.store.publish_artifact({"scene_id": "edited", "quality": {"samples": 48}, "requested_quality": "final", "renders": [{"sha256": "def", "path": str(self.source / "render.png")}]}, kind="render_receipt")
        preview = self.store.publish_artifact(b"\x89PNG\r\n\x1a\npreview", kind="image", mime_type="image/png", metadata={"receipt_artifact_id": render["id"]})
        scene = self.store.scene()
        scene.update(id="edited", parent_id=scene["id"])
        scene["entities"][0]["state"]["design_receipt_artifact_id"] = design["id"]
        self.store.publish_scene(scene, expected_revision="source-scene", actor="fixture-owner")
        self.export(artifact_ids=[preview["id"]])
        manifest, _ = verify_bundle(self.output)
        self.assertTrue({design["id"], render["id"], preview["id"]}.issubset({row["id"] for row in manifest["artifacts"]}))
        destination = self.root / "restored"
        import_bundle(self.output, destination)
        restored = ProductionStore(destination, opening_entries=())
        receipt_id = restored.scene()["entities"][0]["state"]["design_receipt_artifact_id"]
        self.assertNotEqual(receipt_id, design["id"])
        self.assertEqual(restored.artifact(receipt_id)["kind"], "design_receipt")
        self.assertNotIn(str(self.source), restored.artifact_path(receipt_id).read_text())
        image = next(row for row in restored.snapshot()["artifacts"] if row["metadata"].get("demo_source_artifact_id") == preview["id"])
        local_render = restored.artifact(image["metadata"]["receipt_artifact_id"])
        self.assertEqual(local_render["kind"], "render_receipt")
        self.assertNotIn(str(self.source), restored.artifact_path(local_render["id"]).read_text())
        receipt = json.loads(restored.artifact_path(local_render["id"]).read_text())
        self.assertEqual(receipt['quality'], {'samples': 48})
        self.assertEqual(receipt['requested_quality'], 'final')

    def test_take_selection_exports_dependencies_and_keeps_historical_authority(self):
        video = self.store.publish_artifact(b"\0\0\0\x18ftypisomfixture", kind="video", mime_type="video/mp4")
        prompt = self.store.publish_artifact({"messages": [{"role": "system", "content": "licensed-context-exclude"}, {"role": "user", "content": "Keep the cup still."}], "context_id": "private-context"}, kind="prompt")
        package = {"id": "package", "scene_revision_id": "source-scene", "prompt_ref": prompt["id"], "reference_ids": [self.image["id"]]}
        self.store.upsert_record("packages", package)
        take = Take(id="take", shot_id="shot", scene_revision_id="source-scene", package_id="package", job_id="historical-job", artifact_id=video["id"], poster_id=self.image["id"], status="accepted")
        self.store.upsert_record("takes", take.model_dump(mode="json"))
        observation = Observation(id="observation", take_id="take", assertion="Cup stable", status="unknown", detail="Sparse samples only", evidence_ids=[self.image["id"]])
        self.store.upsert_record("observations", observation.model_dump(mode="json"))
        self.export(take_ids=["take"])
        manifest, _ = verify_bundle(self.output)
        exported_prompt = next(row for row in manifest["artifacts"] if row["id"] == prompt["id"])
        text = (self.output / exported_prompt["relative_path"]).read_text()
        self.assertNotIn("licensed-context-exclude", text)
        self.assertNotIn("private-context", text)
        self.assertIn("Keep the cup still", text)
        restored = self.root / "restored"
        import_bundle(self.output, restored)
        store = ProductionStore(restored, opening_entries=())
        imported_take = store.get_record("takes", "take")
        self.assertEqual(imported_take["status"], "accepted")
        self.assertEqual(imported_take["scene_revision_id"], "source-scene")
        self.assertNotEqual(imported_take["scene_revision_id"], store.scene()["id"])
        self.assertEqual(store.get_record("observations", "observation")["status"], "unknown")
        self.assertEqual(store.snapshot()["jobs"], [])

    def test_imported_production_serves_real_api_snapshot_and_assets_without_provider_calls(self):
        from fastapi.testclient import TestClient
        from ghost_director.api import create_app
        from ghost_director.config import Settings
        from ghost_director.service import ProductionService
        self.export()
        restored = self.root / "restored"
        import_bundle(self.output, restored)
        store = ProductionStore(restored, opening_entries=())
        service = ProductionService(store, Settings("", "", "", False, restored))
        service.initialize()
        app = create_app(store, service.handle, recovery_handler=service.resume, shutdown_handler=service.runner.http.close)
        with patch("httpx.AsyncClient.request", side_effect=AssertionError("Imported demo attempted provider access")):
            with TestClient(app) as client:
                snapshot = client.get("/api/productions/ghost-director-hackathon/snapshot")
                self.assertEqual(snapshot.status_code, 200)
                data = snapshot.json()
                self.assertTrue(data["production"]["paused"])
                self.assertEqual(data["jobs"], [])
                self.assertEqual(data["budget"]["committed_usd"], 0)
                asset_url = data["scene"]["entities"][0]["asset_url"]
                content = client.get(asset_url)
                self.assertEqual(content.status_code, 200)
                self.assertEqual(content.content, glb())

    def test_selected_runs_export_real_task_outputs_context_pins_and_historical_decisions(self):
        direction = self.store.create_run("direction", metadata={"name": "Direction", "private_extra": "RUN_PRIVATE_OMITTED"})
        planning = self.store.create_run("planning")
        recovered = self.store.create_run("recovered-review")
        context = self.store.publish_artifact({"role_id": "role.producer.producer-orchestrator", "files": [{"path": "agents/producer.md", "sha256": "a" * 64, "bytes": 25, "content": "LOADED_CONTEXT_OMITTED"}], "resolver": {"source_commit": "commit", "bundle": {"private": "RESOLVER_CONTENT_OMITTED"}, "registry_pins": [{"path": "registry/roles.json", "sha256": "b" * 64, "bytes": 30}]}}, kind="context")
        prompt = self.store.publish_artifact({"messages": [{"role": "system", "content": "SYSTEM_CONTEXT_OMITTED"}, {"role": "user", "content": [{"type": "text", "text": "Direct this scene."}, {"type": "image_ref", "artifact_id": self.image["id"]}]}], "context_id": context["id"]}, kind="prompt")
        output = self.store.publish_artifact("Keep the cup still.", kind="text", mime_type="text/plain")
        for task_id, role, run, dependencies, status in (("producer", "Producer", direction, [], "completed"), ("dp", "Director of Photography", direction, ["producer"], "completed"), ("planner", "Generation Planner", planning, [], "completed"), ("supervisor", "Script Supervisor", recovered, [], "running")):
            self.store.create_task({"id": task_id, "run_id": run["id"], "role_id": "role.test", "role_label": role, "model": "muse-wonder-1.0", "goal": "Bounded assignment", "status": status, "input_refs": [self.image["id"]], "output_refs": [], "result": {"output_refs": [output["id"]], "job_id": "PRIVATE_JOB_LINK_OMITTED"}, "prompt_ref": prompt["id"], "context_ref": context["id"], "dependency_ids": dependencies})
        self.store.upsert_record("handoffs", {"id": "handoff", "run_id": direction["id"], "from_task_id": "producer", "to_task_id": "dp", "input_refs": [], "summary": "Recorded dependency; consumption unrecorded"})
        video = self.store.publish_artifact(b"\0\0\0\x18ftypisomfixture", kind="video", mime_type="video/mp4")
        self.store.upsert_record("packages", {"id": "package", "run_id": recovered["id"], "prompt_ref": output["id"], "reference_ids": [self.image["id"]], "conditioning_mode": "first_last_frame", "proposed_end_state": {"accepted": False}})
        take = Take(id="take", shot_id="shot", scene_revision_id="source-scene", package_id="package", job_id="historical-video", artifact_id=video["id"], run_id=recovered["id"])
        self.store.upsert_record("takes", take.model_dump(mode="json"))
        self.store.reject_take("take", actor="fixture-owner", reason="Duplicate cup observed", expected_revision="source-scene", run_id=recovered["id"])
        self.store.create_job({"id": "private-job", "provider": "meta", "model": "muse-wonder-1.0", "run_id": direction["id"], "task_id": "producer", "request_ref": self.request["id"]})
        self.store.reserve("private-job", "meta", 1)
        self.store.upsert_record("calls", {"id": "private-call", "run_id": direction["id"], "raw_response": "CALL_TRANSPORT_OMITTED"})
        before = self.store.snapshot()
        report = self.export(run_ids=[direction["id"]], take_ids=[take.id])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(report["tasks"], 4)
        manifest, creative = verify_bundle(self.output)
        self.assertEqual(set(manifest["source"]["selected_run_ids"]), {"direction", "planning", "recovered-review"})
        self.assertEqual(len(creative["handoffs"]), 1)
        all_bytes = b"\n".join(path.read_bytes() for path in self.output.rglob("*") if path.is_file())
        for excluded in (b"LOADED_CONTEXT_OMITTED", b"SYSTEM_CONTEXT_OMITTED", b"RESOLVER_CONTENT_OMITTED", b"RUN_PRIVATE_OMITTED", b"PRIVATE_JOB_LINK_OMITTED", b"CALL_TRANSPORT_OMITTED"):
            self.assertNotIn(excluded, all_bytes)
        prompt_row = next(row for row in manifest["artifacts"] if row["id"] == prompt["id"])
        derivative = json.loads((self.output / prompt_row["relative_path"]).read_text())
        self.assertTrue(derivative["context_manifest"]["loaded_text_omitted"])
        self.assertEqual(derivative["context_manifest"]["files"][0]["sha256"], "a" * 64)
        self.assertEqual(derivative["messages"][0]["role"], "user")
        self.assertEqual(prompt_row["source_sha256"], prompt["sha256"])
        self.assertNotEqual(prompt_row["sha256"], prompt["sha256"])
        restored = self.root / "restored"
        import_bundle(self.output, restored)
        store = ProductionStore(restored, opening_entries=())
        snapshot = store.snapshot()
        self.assertEqual(snapshot["jobs"], [])
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(store.budget()["committed_usd"], 0)
        self.assertTrue(all(row["reserved_usd"] == 0 for row in store.budget()["categories"].values()))
        self.assertEqual(len(snapshot["tasks"]), 4)
        for task in snapshot["tasks"]:
            self.assertEqual(task["status"], "imported")
            self.assertEqual(task["provider_calls_executed"], 0)
            self.assertEqual(task["evidence_origin"], "imported")
            self.assertEqual(task["output_refs"], task["result"]["output_refs"])
            self.assertEqual(store.artifact_path(task["output_refs"][0]).read_text(), "Keep the cup still.")
            self.assertNotIn("job_id", task["result"])
        self.assertEqual(next(task for task in snapshot["tasks"] if task["source_task_id"] == "supervisor")["source_status"], "running")
        handoff = snapshot["handoffs"][0]
        self.assertEqual(handoff["evidence_origin"], "imported")
        self.assertEqual(handoff["input_refs"], [])
        self.assertTrue(handoff["from_task_id"].startswith("task-import-"))
        event = next(event for event in store.events() if event["type"] == "demo.disposition_imported")
        self.assertEqual(event["payload"]["actor"], "fixture-owner")
        self.assertEqual(event["payload"]["reason"], "Duplicate cup observed")
        self.assertFalse(any(event["type"] in {"take.accepted", "take.rejected", "task.running", "task.completed"} for event in store.events()))
        self.assertEqual(store.get_record("packages", "package")["conditioning_mode"], "first_last_frame")
        self.assertEqual(store.get_record("packages", "package")["proposed_end_state"], {"accepted": False})
        store.recover()
        self.assertEqual(store.snapshot()["tasks"], snapshot["tasks"])

    def test_unknown_historical_run_does_not_publish_partial_bundle(self):
        with self.assertRaisesRegex(ValueError, "run"):
            self.export(run_ids=["nonexistent"])
        self.assertFalse(self.output.exists())

    def test_explicit_missing_credential_scan_config_fails_before_export(self):
        from tools.export_ghost_demo import main
        argv = ["export_ghost_demo.py", "export", "--production", str(self.source), "--output", str(self.output), "--env-file", str(self.root / "missing.env")]
        with patch("sys.argv", argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)
        self.assertFalse(self.output.exists())

    def test_provider_requests_are_rejected_even_when_explicitly_selected(self):
        with self.assertRaisesRegex(ValueError, "request"):
            self.export(artifact_ids=[self.request["id"]])
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".ghost-demo-*")))

    def test_known_secret_in_binary_fails_without_partial_bundle(self):
        secret = "TEST_ONLY_SENSITIVE_SENTINEL"
        poisoned = self.store.publish_artifact(b"\x89PNG\r\n\x1a\n" + secret.encode(), kind="image", mime_type="image/png")
        with self.assertRaisesRegex(ValueError, "Sensitive"):
            self.export(artifact_ids=[poisoned["id"]], secrets=(secret,))
        self.assertFalse(self.output.exists())

    def test_sanitizer_removes_credentials_signed_queries_data_and_local_paths(self):
        sanitizer = ReleaseSanitizer(("TEST_SECRET_SENTINEL",))
        result = sanitizer.value({"authorization": "bad", "raw_request": {"anything": "bad"}, "text": "TEST_SECRET_SENTINEL https://user:pass@cdn.example.test/image?signature=abc#private /Users/private/file data:image/png;base64,abc"})
        self.assertNotIn("authorization", result)
        self.assertNotIn("raw_request", result)
        for private in ("TEST_SECRET_SENTINEL", "signature", "user:pass", "/Users/private", "data:image"):
            self.assertNotIn(private, result["text"])
        sanitizer.check(encoded(result))

    def test_symlink_source_and_path_escape_in_bundle_are_rejected(self):
        path = self.store.artifact_path(self.model["id"])
        outside = self.root / "outside.glb"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.export()
        path.unlink()
        path.write_bytes(outside.read_bytes())
        self.export()
        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["files"]["../outside.glb"] = {"sha256": digest(outside.read_bytes()), "bytes": outside.stat().st_size}
        (self.output / "manifest.json").write_bytes(encoded(manifest))
        with self.assertRaises(ValueError):
            verify_bundle(self.output)

    def test_tamper_and_overwrite_are_rejected(self):
        self.export()
        with self.assertRaises(ValueError):
            self.export()
        (self.output / "creative.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "hash"):
            verify_bundle(self.output)
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "precious").write_text("keep")
        with self.assertRaises(ValueError):
            import_bundle(self.output, existing)
        self.assertEqual((existing / "precious").read_text(), "keep")

    def test_explicit_revision_pins_historical_source_without_claiming_it_is_current_head(self):
        later = self.store.scene()
        later.update(id="later-scene", parent_id="source-scene")
        later["entities"][0]["position"] = [1, 0, 0]
        self.store.publish_scene(later, expected_revision="source-scene", actor="fixture-owner")
        self.export(revision_id="source-scene")
        manifest, creative = verify_bundle(self.output)
        self.assertEqual(creative["scene"]["id"], "source-scene")
        self.assertEqual(manifest["source"]["accepted_head_at_export"], "later-scene")
        self.assertTrue(manifest["source"]["historical_scene_selection"])

    def test_external_glb_resources_and_executable_disguises_are_rejected(self):
        for extra in ({"images": [{"uri": "https://cdn.example.test/texture?token=private"}]}, {"buffers": [{"uri": "../secret.bin"}]}):
            with self.assertRaisesRegex(ValueError, "URIs"):
                checked_media(glb(extra), "model", "model/gltf-binary")
        with self.assertRaises(ValueError):
            checked_media(b"<svg onload='run()'/>", "image", "image/svg+xml")

    def test_cbor_provenance_uri_boundary_preserved_but_real_signed_urls_rejected(self):
        uri = b"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
        content = b"\0\0\0\x18ftypisom" + bytes((0x78, len(uri))) + uri + b"adjacent#CBOR-field"
        sanitizer = ReleaseSanitizer()
        sanitizer.check(content)
        pki = b"http://pki.goog/example.crl"
        sanitizer.check(content + bytes((0x86, len(pki))) + pki + b"\x01\xff#DER-next-field")
        video = self.store.publish_artifact(content, kind="video", mime_type="video/mp4")
        self.export(artifact_ids=[video["id"]])
        manifest, _ = verify_bundle(self.output)
        saved = next(row for row in manifest["artifacts"] if row["id"] == video["id"])
        self.assertEqual((self.output / saved["relative_path"]).read_bytes(), content)
        signed = b"https://fal.media/output.mp4?signature=private"
        for payload in (bytes((0x78, len(signed))) + signed, content + bytes((0x78, len(signed))) + signed, bytes((0x86, len(signed))) + signed):
            with self.assertRaisesRegex(ValueError, "Signed"):
                sanitizer.check(payload)

    @unittest.skipUnless(shutil.which("zstd"), "zstd is an export-only prerequisite for compressed Blender sources")
    def test_compressed_blender_source_is_scanned_after_bounded_decompression(self):
        plain = b"BLENDER-v300 synthetic compressed fixture"
        compressed = subprocess.run([shutil.which("zstd"), "--compress", "--stdout"], input=plain, capture_output=True, check=True).stdout
        blend = self.store.publish_artifact(compressed, kind="blender_scene", mime_type="application/octet-stream")
        scene = self.store.scene()
        scene["parent_id"] = scene["id"]
        scene["id"] = "compressed-scene"
        scene["entities"][0]["state"]["blend_artifact_id"] = blend["id"]
        self.store.publish_scene(scene, expected_revision="source-scene", actor="fixture-owner")
        self.export()
        manifest, _ = verify_bundle(self.output)
        record = next(row for row in manifest["artifacts"] if row["id"] == blend["id"])
        self.assertEqual((self.output / record["relative_path"]).read_bytes(), plain)
        self.assertEqual(record["source_sha256"], digest(compressed))
        self.assertIn("decompression", record["export_transformation"])
        with patch("tools.export_ghost_demo.MAX_FILE_BYTES", 10):
            from tools.export_ghost_demo import decompress_blend
            with self.assertRaisesRegex(ValueError, "size limit"):
                decompress_blend(self.store.artifact_path(blend["id"]))


if __name__ == "__main__":
    unittest.main()
