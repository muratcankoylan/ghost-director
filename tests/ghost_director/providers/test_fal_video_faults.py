"""Actual fal video runner with mocked transport; no live provider requests."""
import base64
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
from PIL import Image

from ghost_director.config import Settings
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.providers.http import ProviderError, ProviderHTTP, safe_error_detail
from ghost_director.providers.runner import ProviderRunner


class FalVideoFaultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.settings = Settings("test-meta-key", "test-router-key", "test-fal-key", True, Path(self.temp.name), video_model="fal-ai/veo3.1/reference-to-video")
        self.store = ProductionStore(self.temp.name, opening_entries=(), secrets=self.settings.secrets)
        self.calls = []
        self.respond = lambda request: httpx.Response(500)
        def transport(request):
            self.calls.append(request)
            return self.respond(request)
        self.http = ProviderHTTP(self.settings, httpx.MockTransport(transport))
        self.runner = ProviderRunner(self.store, self.settings, self.http)
        raw = BytesIO()
        Image.new("RGB", (2, 2)).save(raw, format="PNG")
        self.reference = self.store.publish_artifact(raw.getvalue(), kind="image", mime_type="image/png")
        self.locator = "https://queue.fal.run/fal-ai/veo3.1/requests/remote/status"

    async def asyncTearDown(self):
        await self.http.close()
        self.temp.cleanup()

    def confirmed_job(self, identifier="job", category="video"):
        self.store.create_job({"id": identifier, "provider": "fal", "model": self.settings.video_model})
        self.store.reserve(identifier, category, 1.92 if category == "video" else .1)
        self.store.begin_submission(identifier)
        self.store.record_submission(identifier, "remote", polling_locator=self.locator)
        return self.store.get_job(identifier)

    async def test_video_schema_conservative_reservation_and_get_only_content(self):
        def respond(request):
            if request.method == "POST":
                self.assertEqual(str(request.url), "https://queue.fal.run/fal-ai/veo3.1/reference-to-video")
                payload = json.loads(request.content)
                self.assertEqual(payload["duration"], "8s")
                self.assertEqual(payload["resolution"], "720p")
                self.assertEqual(payload["aspect_ratio"], "16:9")
                self.assertFalse(payload["generate_audio"])
                self.assertFalse(payload["auto_fix"])
                self.assertEqual(len(payload["image_urls"]), 1)
                self.assertTrue(payload["image_urls"][0].startswith("data:image/png;base64,"))
                self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 1.92)
                return httpx.Response(200, json={"request_id": "remote", "status_url": self.locator})
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"status": "COMPLETED"})
            if request.url.host == "queue.fal.run":
                return httpx.Response(200, json={"video": {"url": "https://fal.media/take.mp4?signature=private"}})
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom")
        self.respond = respond
        artifact, original = await self.runner.video("Hold the camera", [self.reference["id"]], duration=8)
        job = self.store.get_job(original["id"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["category"], "video")
        self.assertEqual(artifact["metadata"]["provider"], "fal")
        self.assertEqual(sum(request.method == "POST" for request in self.calls), 1)
        budget = self.store.budget()["categories"]["video"]
        self.assertEqual(budget["estimated_usd"], 1.6)
        self.assertEqual(budget["reported_usd"], 0)
        self.assertEqual(budget["reserved_usd"], 0)
        self.assertNotIn("signature=", json.dumps(self.store.snapshot()))

    async def test_invalid_duration_resolution_or_reference_count_has_no_effect(self):
        before = self.store.snapshot()
        for duration, resolution, refs in ((5, "720p", [self.reference["id"]]), (8, "480p", [self.reference["id"]]), (8, "720p", [self.reference["id"]] * 4)):
            with self.assertRaises(ValueError):
                await self.runner.video("Test", refs, duration=duration, resolution=resolution)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.snapshot(), before)

    async def test_image_to_video_single_first_frame_schema_and_same_bounded_charge(self):
        settings = replace(self.settings, video_model="fal-ai/veo3.1/image-to-video")
        runner = ProviderRunner(self.store, settings, self.http)
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            await runner.video("Preserve the first-frame set", [self.reference["id"]] * 2, duration=8)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.calls, [])
        def respond(request):
            if request.method == "POST":
                self.assertEqual(str(request.url), "https://queue.fal.run/fal-ai/veo3.1/image-to-video")
                payload = json.loads(request.content)
                self.assertIn("image_url", payload)
                self.assertNotIn("image_urls", payload)
                self.assertTrue(payload["image_url"].startswith("data:image/png;base64,"))
                self.assertEqual(payload["duration"], "8s")
                self.assertEqual(payload["resolution"], "720p")
                self.assertFalse(payload["generate_audio"])
                self.assertFalse(payload["auto_fix"])
                self.assertNotIn("safety_tolerance", payload)
                self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 1.92)
                return httpx.Response(200, json={"request_id": "remote", "status_url": self.locator})
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"status": "COMPLETED"})
            if request.url.host == "queue.fal.run":
                return httpx.Response(200, json={"video": {"url": "https://storage.googleapis.com/falserverless/output.mp4"}})
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=b"\0\0\0\x18ftypisom")
        self.respond = respond
        artifact, job = await runner.video("Preserve the first-frame set", [self.reference["id"]], duration=8)
        self.assertEqual(sum(request.method == "POST" for request in self.calls), 1)
        self.assertEqual(artifact["metadata"]["model"], settings.video_model)
        self.assertEqual(self.store.get_job(job["id"])["status"], "succeeded")
        self.assertEqual(self.store.budget()["categories"]["video"]["estimated_usd"], 1.6)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 0)

    async def test_read_error_after_post_keeps_hold_and_never_retries(self):
        def respond(request):
            raise httpx.ReadError("Synthetic interrupted response", request=request)
        self.respond = respond
        with self.assertRaises(ProviderError):
            await self.runner.video("Test", [self.reference["id"]], duration=8)
        job = self.store.snapshot()["jobs"][0]
        self.assertEqual(job["status"], "submission_unknown")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 1.92)
        recovery = self.store.recover()
        self.assertEqual(recovery["resumable_jobs"], [])
        self.assertEqual(recovery["submission_unknown"][0]["id"], job["id"])

    async def test_endpoint_video_preserves_order_and_rejects_partial_constraints(self):
        runner=ProviderRunner(self.store,replace(self.settings,video_model='fal-ai/veo3.1/first-last-frame-to-video'),self.http)
        second=self.store.publish_artifact(b'endpoint-image',kind='image',mime_type='image/png')
        before=self.store.snapshot()
        for count in (1,3):
            with self.assertRaisesRegex(ValueError,'exactly two'):
                await runner.video('Place cup', [self.reference['id']]*count)
        self.assertEqual(self.store.snapshot(),before)
        self.assertEqual(self.calls,[])
        def respond(request):
            payload=json.loads(request.content)
            self.assertTrue(str(request.url).endswith('/first-last-frame-to-video'))
            self.assertEqual(base64.b64decode(payload['last_frame_url'].split(',')[1]),b'endpoint-image')
            self.assertNotEqual(payload['first_frame_url'],payload['last_frame_url'])
            self.assertNotIn('image_urls',payload)
            self.assertFalse(payload['auto_fix'])
            self.assertNotIn('safety_tolerance',payload)
            self.assertEqual(self.store.budget()['categories']['video']['reserved_usd'],1.92)
            return httpx.Response(200,json={'request_id':'remote','status_url':self.locator})
        self.respond=respond
        runner.poll_fal_video=AsyncMock(return_value=({},{}))
        await runner.video('Place cup',[self.reference['id'],second['id']])
        self.assertEqual(len(self.calls),1)

    async def test_truthy_nonstring_receipts_immediately_become_unknown(self):
        for receipt in ({"request_id": ["bad"], "status_url": self.locator}, {"request_id": "remote", "status_url": [self.locator]}):
            with self.subTest(receipt_keys=list(receipt)):
                self.respond = lambda request: httpx.Response(200, json=receipt)
                # Isolate the second experiment; unretired unknowns intentionally block it.
                with TemporaryDirectory() as directory:
                    store = ProductionStore(directory, opening_entries=())
                    image = store.publish_artifact(self.store.artifact_path(self.reference["id"]).read_bytes(), kind="image", mime_type="image/png")
                    runner = ProviderRunner(store, replace(self.settings, root=Path(directory)), self.http)
                    with self.assertRaises(ProviderError):
                        await runner.video("Test", [image["id"]], duration=8)
                    self.assertEqual(store.snapshot()["jobs"][0]["status"], "submission_unknown")
                    self.assertEqual(store.budget()["committed_usd"], 1.92)

    async def test_known_job_download_failure_recovers_without_resubmission(self):
        job = self.confirmed_job()
        def respond(request):
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"status": "COMPLETED"})
            if request.url.host == "queue.fal.run":
                return httpx.Response(200, json={"video": {"url": "https://fal.media/take.mp4"}})
            raise httpx.ReadError("Interrupted download", request=request)
        self.respond = respond
        with self.assertRaises(httpx.ReadError):
            await self.runner.poll_fal_video(job)
        reopened = ProductionStore(self.temp.name, opening_entries=())
        recovered = reopened.recover()["resumable_jobs"]
        self.assertEqual([row["remote_id"] for row in recovered], ["remote"])
        self.assertEqual(reopened.budget()["committed_usd"], 1.92)
        original = self.respond
        self.respond = lambda request: httpx.Response(200, content=b"\0\0\0\x18ftypisom") if request.url.host == "fal.media" else original(request)
        artifact, _ = await ProviderRunner(reopened, self.settings, self.http).poll_fal_video(recovered[0])
        self.assertEqual(artifact["kind"], "video")
        self.assertTrue(all(request.method == "GET" for request in self.calls))

    async def test_missing_result_or_remote_failure_never_releases_unknown_billing(self):
        for status in ("COMPLETED", "FAILED", "CANCELLED"):
            with self.subTest(status=status):
                job = self.confirmed_job(identifier="job-" + status)
                self.respond = lambda request: httpx.Response(200, json={"status": status} if request.url.path.endswith("/status") else {"video": None})
                with self.assertRaises(ProviderError):
                    await self.runner.poll_fal_video(job)
                self.assertEqual(self.store.get_job(job["id"])["status"], "failed")
                self.assertEqual(self.store.get_job(job["id"])["reserved_usd"], 1.92)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 5.76)

    async def test_other_provider_active_video_blocks_fal_video_and_releases_only_new_hold(self):
        self.store.create_job({"id": "openrouter-active", "provider": "openrouter", "model": "bytedance/seedance-2.5"})
        self.store.reserve("openrouter-active", "video", .7)
        self.store.begin_submission("openrouter-active")
        with self.assertRaises(Conflict):
            await self.runner.video("Test", [self.reference["id"]], duration=8)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.budget()["committed_usd"], .7)

    async def test_completed_result_400_422_is_terminal_for_video_and_3d_with_hold(self):
        for category in ("video", "assets_3d"):
            for status in (400, 422):
                with self.subTest(category=category, status=status), TemporaryDirectory() as directory:
                    store = ProductionStore(directory, opening_entries=(), secrets=self.settings.secrets)
                    store.create_job({"id": "known", "provider": "fal", "model": self.settings.video_model})
                    store.reserve("known", category, 1.92)
                    store.begin_submission("known")
                    store.record_submission("known", "remote", polling_locator=self.locator)
                    def respond(request):
                        self.assertEqual(request.method, "GET")
                        if request.url.path.endswith("/status"):
                            return httpx.Response(200, json={"status": "COMPLETED"})
                        return httpx.Response(status, json={"detail": [{"msg": "Synthetic terminal rejection " + self.settings.fal_key, "input": "NEVER_EXPORT_INPUT"}]})
                    self.respond = respond
                    runner = ProviderRunner(store, self.settings, self.http)
                    poll = runner.poll_fal_video if category == "video" else runner.poll_fal
                    with self.assertRaises(ProviderError):
                        await poll(store.get_job("known"))
                    job = store.get_job("known")
                    self.assertEqual(job["status"], "failed")
                    self.assertIn("Synthetic terminal rejection", job["error"])
                    self.assertNotIn(self.settings.fal_key, job["error"])
                    self.assertNotIn("NEVER_EXPORT_INPUT", json.dumps(store.snapshot()))
                    self.assertEqual(store.budget()["categories"][category]["reserved_usd"], 1.92)
                    self.assertEqual(store.recover()["resumable_jobs"], [])
                    # Terminal failure releases the concurrency exclusion, not money.
                    store.create_job({"id": "next"})
                    store.reserve("next", "video", .1)
                    store.begin_submission("next")
        self.assertTrue(all(request.method == "GET" for request in self.calls))

    async def test_completed_result_5xx_and_transport_stay_resumable_get_only(self):
        for category in ("video", "assets_3d"):
            for fault in ("503", "transport"):
                with self.subTest(category=category, fault=fault), TemporaryDirectory() as directory:
                    store = ProductionStore(directory, opening_entries=())
                    store.create_job({"id": "known", "provider": "fal", "model": self.settings.video_model})
                    store.reserve("known", category, 1.92)
                    store.begin_submission("known")
                    store.record_submission("known", "remote", polling_locator=self.locator)
                    def respond(request):
                        self.assertEqual(request.method, "GET")
                        if request.url.path.endswith("/status"):
                            return httpx.Response(200, json={"status": "COMPLETED"})
                        if fault == "transport":
                            raise httpx.ReadError("Transient result response", request=request)
                        return httpx.Response(503)
                    self.respond = respond
                    runner = ProviderRunner(store, self.settings, self.http)
                    poll = runner.poll_fal_video if category == "video" else runner.poll_fal
                    with self.assertRaises(ProviderError):
                        await poll(store.get_job("known"))
                    self.assertEqual(store.get_job("known")["status"], "queued")
                    recovered = store.recover()["resumable_jobs"]
                    self.assertEqual([row["remote_id"] for row in recovered], ["remote"])
                    self.assertEqual(store.budget()["committed_usd"], 1.92)
                    def success(request):
                        self.assertEqual(request.method, "GET")
                        if request.url.path.endswith("/status"):
                            return httpx.Response(200, json={"status": "COMPLETED"})
                        if request.url.host == "queue.fal.run":
                            return httpx.Response(200, json={"video" if category == "video" else "model_glb": {"url": "https://fal.media/result"}})
                        return httpx.Response(200, content=b"\0\0\0\x18ftypisom" if category == "video" else b"glTF-fixture")
                    self.respond = success
                    await poll(recovered[0])
                    self.assertEqual(store.get_job("known")["status"], "succeeded")
        self.assertTrue(all(request.method == "GET" for request in self.calls))

    async def test_fal_video_materializes_after_terminal_commit_but_3d_does_not(self):
        video = self.confirmed_job()
        artifact = self.store.publish_artifact(b"\0\0\0\x18ftypisom", kind="video", mime_type="video/mp4")
        self.store.finish_job(video["id"], result_refs=[artifact["id"]], cost_usd=1.6, cost_basis="estimated")
        geometry = self.confirmed_job("geometry", category="assets_3d")
        self.store.finish_job(geometry["id"], result_refs=[artifact["id"]], cost_usd=.02, cost_basis="estimated")
        recovery = self.store.recover()
        self.assertEqual([row["id"] for row in recovery["materializable_jobs"]], [video["id"]])
        self.store.upsert_record("takes", {"id": "existing-unreviewed", "job_id": video["id"], "status": "unreviewed"})
        self.assertEqual(self.store.recover()["materializable_jobs"], [])

    async def test_service_routes_fal_video_recovery_to_video_poller(self):
        from ghost_director.service import ProductionService
        run = self.store.create_run("run")
        job = self.store.create_job({"id": "resumable", "provider": "fal", "model": self.settings.video_model, "run_id": run["id"]})
        self.store.reserve(job["id"], "video", 1.92)
        self.store.begin_submission(job["id"])
        self.store.record_submission(job["id"], "remote", polling_locator=self.locator)
        with patch("ghost_director.service.ProviderRunner") as constructor:
            fake = constructor.return_value
            fake.poll_fal_video = AsyncMock(return_value=({}, job))
            fake.poll_fal = AsyncMock()
            fake.poll_video = AsyncMock()
            service = ProductionService(self.store, self.settings)
            await service.resume(self.store.recover())
        fake.poll_fal_video.assert_awaited_once()
        fake.poll_fal.assert_not_awaited()
        fake.poll_video.assert_not_awaited()


class ProviderErrorDetailTests(unittest.TestCase):
    def test_fal_detail_array_exports_only_sanitized_message_fields(self):
        response = httpx.Response(422, json={"detail": [
            {"msg": "Reference failed TEST_DETAIL_SECRET https://fal.media/private?sig=hidden", "loc": ["private-location"], "input": "PRIVATE_REQUEST_INPUT", "ctx": {"error": "PRIVATE_CONTEXT"}},
            {"msg": {"nested": "NOT_A_MESSAGE"}},
            "NOT_A_RECORD",
        ]})
        result = safe_error_detail(response, ("TEST_DETAIL_SECRET",))
        self.assertIn("Reference failed", result)
        for forbidden in ("TEST_DETAIL_SECRET", "private-location", "PRIVATE_REQUEST_INPUT", "PRIVATE_CONTEXT", "NOT_A_MESSAGE", "NOT_A_RECORD", "sig="):
            self.assertNotIn(forbidden, result)

    def test_error_detail_redacts_known_secret_encodings_urls_and_embedded_media(self):
        secret = 'TEST/credential+with="punctuation"'
        variants = [secret, quote(secret, safe=""), json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode()]
        message = "402 Insufficient credits " + " ".join(variants) + " https://example.test/video?signature=secret data:image/png;base64,ABCDEF"
        response = httpx.Response(402, json={"error": {"message": message}, "request": {"authorization": "NEVER_REFLECT_BODY"}})
        result = safe_error_detail(response, (secret,))
        for value in variants + ["NEVER_REFLECT_BODY", "signature", "data:image"]:
            self.assertNotIn(value, result)
        self.assertIn("Insufficient credits", result)
        self.assertLessEqual(len(result), 600)

    def test_large_or_unstructured_errors_are_not_reflected(self):
        for response in (httpx.Response(500, content=b"x" * 50001), httpx.Response(500, json=["not a structured error"]), httpx.Response(500, json={"error": {"message": {"secret": "not text"}}})):
            self.assertEqual(safe_error_detail(response, ()), "")


if __name__ == "__main__":
    unittest.main()
