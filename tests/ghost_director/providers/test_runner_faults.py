"""Transport faults and recovery through the actual paid-job runner, mocked HTTP."""

from io import BytesIO
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from PIL import Image

from ghost_director.config import Settings
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.providers.http import ProviderError, ProviderHTTP
from ghost_director.providers.runner import ProviderRunner


class RunnerFaultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.settings = Settings("test-meta", "test-video", "test-fal", True, Path(self.tmp.name), video_model="bytedance/seedance-2.5")
        self.store = ProductionStore(self.tmp.name, opening_entries=(), secrets=self.settings.secrets)
        self.calls = []
        self.response = lambda request: httpx.Response(500)
        def respond(request):
            self.calls.append(request)
            return self.response(request)
        self.http = ProviderHTTP(self.settings, httpx.MockTransport(respond))
        self.runner = ProviderRunner(self.store, self.settings, self.http)
        raw = BytesIO()
        Image.new("RGB", (2, 2)).save(raw, format="PNG")
        self.reference = self.store.publish_artifact(raw.getvalue(), kind="image", mime_type="image/png")

    async def asyncTearDown(self):
        await self.http.close()
        self.tmp.cleanup()

    async def test_http_408_is_unknown_and_retains_hold_without_retry(self):
        self.response = lambda request: httpx.Response(408)
        with self.assertRaises(ProviderError):
            await self.runner.image("An original image")
        self.assertEqual(len(self.calls), 1)
        job = self.store.snapshot()["jobs"][0]
        self.assertEqual(job["status"], "submission_unknown")
        self.assertEqual(self.store.budget()["categories"]["meta"]["reserved_usd"], .02)

    async def test_paused_claim_releases_never_submitted_reservation(self):
        self.store.set_paused(True)
        with self.assertRaises(Conflict):
            self.runner.prepare("meta", self.settings.image_model, {"prompt": "test"}, "meta", .02)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.budget()["committed_usd"], 0)
        self.assertEqual(self.store.snapshot()["jobs"][0]["status"], "cancelled")

    async def test_second_video_claim_releases_only_second_reservation(self):
        first = self.runner.prepare("openrouter", self.settings.video_model, {"prompt": "first"}, "video", 2)
        with self.assertRaises(Conflict):
            self.runner.prepare("openrouter", self.settings.video_model, {"prompt": "second"}, "video", 3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.get_job(first["id"])["status"], "submitting")
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 2)
        self.assertEqual(sorted(job["status"] for job in self.store.snapshot()["jobs"]), ["cancelled", "submitting"])

    async def test_malformed_video_receipt_immediately_marks_unknown(self):
        self.response = lambda request: httpx.Response(200, json=[])
        with self.assertRaises(ProviderError):
            await self.runner.video("Keep the scene fixed", [self.reference["id"]])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.snapshot()["jobs"][0]["status"], "submission_unknown")
        self.assertGreater(self.store.budget()["categories"]["video"]["reserved_usd"], 0)

    async def test_malformed_fal_receipt_immediately_marks_unknown(self):
        self.response = lambda request: httpx.Response(200, json={"request_id": "received-but-no-polling-locator"})
        with self.assertRaises(ProviderError):
            await self.runner.reconstruct(self.reference["id"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.snapshot()["jobs"][0]["status"], "submission_unknown")
        self.assertEqual(self.store.budget()["categories"]["assets_3d"]["reserved_usd"], .1)

    async def test_recovered_known_video_polls_and_downloads_without_any_post(self):
        self.store.create_run("run")
        self.store.create_job({"id": "paid-video", "provider": "openrouter", "model": self.settings.video_model, "run_id": "run"})
        self.store.reserve("paid-video", "video", 2)
        self.store.begin_submission("paid-video")
        self.store.record_submission("paid-video", "remote-id", polling_locator="https://openrouter.ai/api/v1/videos/remote-id")
        reopened = ProductionStore(self.tmp.name, opening_entries=())
        recovered = reopened.recover()["resumable_jobs"]
        self.assertEqual([job["remote_id"] for job in recovered], ["remote-id"])
        def respond(request):
            self.assertEqual(request.method, "GET")
            if request.url.host == "openrouter.ai":
                return httpx.Response(200, json={"status": "completed", "unsigned_urls": ["https://fal.media/recovered.mp4"], "usage": {"cost": 1.25}})
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypisom")
        self.response = respond
        artifact, _ = await ProviderRunner(reopened, self.settings, self.http).poll_video(recovered[0])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(artifact["kind"], "video")
        self.assertEqual(reopened.get_job("paid-video")["status"], "succeeded")
        self.assertEqual(reopened.budget()["categories"]["video"]["reported_usd"], 1.25)
        self.assertEqual(reopened.budget()["categories"]["video"]["reserved_usd"], 0)

    async def test_meshy_request_has_bounded_original_masked_image_and_reserved_cost(self):
        raw = BytesIO()
        mask_image = Image.new("L", (2, 2), 0)
        mask_image.putpixel((0, 0), 255)
        mask_image.save(raw, format="PNG")
        mask = self.store.publish_artifact(raw.getvalue(), kind="mask", mime_type="image/png")
        def respond(request):
            if request.method == "POST":
                self.assertEqual(str(request.url), "https://queue.fal.run/meshy/v7.1/image-to-3d")
                payload = json.loads(request.content)
                self.assertEqual(payload["model_type"], "standard")
                # Official fal queue OpenAPI: standard target range 100..300000;
                # the narrower smart-topology maxima do not apply to this mode.
                self.assertTrue(100 <= payload["target_polycount"] <= 300000)
                self.assertEqual(payload["target_polycount"], 100000)
                self.assertEqual(payload["geometry_resolution"], "2k")
                self.assertEqual(payload["pose_mode"], "")
                self.assertTrue(payload["should_texture"] and payload["enable_pbr"] and payload["enable_safety_checker"])
                self.assertFalse(payload["enable_rigging"] or payload["enable_animation"])
                image = Image.open(BytesIO(base64.b64decode(payload["image_url"].split(",", 1)[1])))
                self.assertEqual(image.size, (2, 2))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getchannel("A").tobytes(), bytes([255, 0, 0, 0]))
                self.assertEqual(self.store.budget()["categories"]["assets_3d"]["reserved_usd"], 2)
                return httpx.Response(200, json={"request_id": "meshy-id", "status_url": "https://queue.fal.run/meshy/v7.1/image-to-3d/requests/meshy-id/status"})
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"status": "COMPLETED"})
            if request.url.host == "queue.fal.run":
                return httpx.Response(200, json={"model_glb": {"url": "https://fal.media/meshy.glb"}})
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=b"glTF-test-payload")
        self.response = respond
        artifact, job = await self.runner.meshy(self.reference["id"], mask["id"])
        self.assertEqual(sum(request.method == "POST" for request in self.calls), 1)
        self.assertEqual(artifact["metadata"]["model"], "meshy/v7.1/image-to-3d")
        self.assertEqual(self.store.get_job(job["id"])["status"], "succeeded")
        self.assertEqual(self.store.budget()["categories"]["assets_3d"]["estimated_usd"], 1.2)
        self.assertEqual(self.store.budget()["categories"]["assets_3d"]["reserved_usd"], 0)

    async def test_meshy_rejects_misaligned_mask_before_job_or_charge(self):
        raw = BytesIO()
        Image.new("L", (3, 2), 255).save(raw, format="PNG")
        mask = self.store.publish_artifact(raw.getvalue(), kind="mask", mime_type="image/png")
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "dimensions"):
            await self.runner.meshy(self.reference["id"], mask["id"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.snapshot(), before)

    async def test_meshy_completed_without_glb_does_not_reconcile_at_sam_price(self):
        raw = BytesIO()
        Image.new("L", (2, 2), 255).save(raw, format="PNG")
        mask = self.store.publish_artifact(raw.getvalue(), kind="mask", mime_type="image/png")
        def respond(request):
            if request.method == "POST":
                return httpx.Response(200, json={"request_id": "meshy-id", "status_url": "https://queue.fal.run/meshy/v7.1/image-to-3d/requests/meshy-id/status"})
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"status": "COMPLETED"})
            return httpx.Response(200, json={})
        self.response = respond
        with self.assertRaises(ProviderError):
            await self.runner.meshy(self.reference["id"], mask["id"])
        # Either the known Meshy estimate or the unresolved $2 reservation is
        # acceptable. The unrelated $0.02 SAM estimate is not.
        self.assertGreaterEqual(self.store.budget()["committed_usd"], 1.2)


if __name__ == "__main__":
    unittest.main()
