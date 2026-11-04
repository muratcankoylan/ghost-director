"""Reference-video boundary and billing checks with real media and mocked transport."""
from io import BytesIO
import json
import subprocess

import httpx
from PIL import Image
import pytest

from ghost_director.config import Settings
from ghost_director.jobs.store import ProductionStore
from ghost_director.providers.http import ProviderError, ProviderHTTP
from ghost_director.providers.reference_video import REFERENCE_VIDEO_MODEL, estimate_reference_cost
from ghost_director.providers.runner import ProviderRunner


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("reference-video") / "motion.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=24:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=20)
    return path.read_bytes()


def setup(tmp_path, clip):
    settings = Settings("test-meta", "test-router", "test-fal", True, tmp_path)
    store = ProductionStore(tmp_path, opening_entries=())
    raw = BytesIO()
    Image.new("RGB", (640, 360), "orange").save(raw, format="PNG")
    image = store.publish_artifact(raw.getvalue(), kind="image", mime_type="image/png")
    video = store.publish_artifact(clip, kind="video", mime_type="video/mp4")
    calls = []
    locator = "https://queue.fal.run/bytedance/seedance-2.5/requests/remote/status"
    def respond(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "remote", "status_url": locator})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.host == "queue.fal.run":
            return httpx.Response(200, json={"video": {"url": "https://fal.media/result.mp4"}})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=clip)
    http = ProviderHTTP(settings, httpx.MockTransport(respond))
    return settings, store, ProviderRunner(store, settings, http), image, video, calls


@pytest.mark.asyncio
async def test_actual_media_combines_appearance_and_motion_and_reconciles_its_own_rate(tmp_path, clip):
    settings, store, runner, image, video, calls = setup(tmp_path, clip)
    try:
        artifact, original = await runner.video("Appearance from @Image1. Follow @Video1 motion.", [image["id"]],
            duration=4, resolution="480p", model_override=REFERENCE_VIDEO_MODEL, video_reference_ids=[video["id"]])
        assert sum(request.method == "POST" for request in calls) == 1
        request = next(request for request in calls if request.method == "POST")
        assert str(request.url) == "https://queue.fal.run/"+REFERENCE_VIDEO_MODEL
        payload = json.loads(request.content)
        assert payload["image_urls"][0].startswith("data:image/png;base64,")
        assert payload["video_urls"][0].startswith("data:video/mp4;base64,")
        assert payload["task"] == "reference"
        assert payload["duration"] == "4"
        assert payload["codec"] == "H264"
        assert payload["generate_audio"] is False
        assert "input_references" not in payload and "frame_images" not in payload
        job = store.get_job(original["id"])
        assert job["status"] == "succeeded"
        assert job["pricing_estimate"]["input_video_seconds"] == 4
        assert job["reserved_usd"] == pytest.approx(estimate_reference_cost(864, 496, 4, 4, resolution="480p")*1.2, abs=1e-6)
        assert job["cost_usd"] == pytest.approx(estimate_reference_cost(640, 360, 4, 4, resolution="480p"), abs=1e-6)
        assert job["cost_basis"] == "estimated"
        assert job["cost_usd"] != 1.6
        assert artifact["metadata"]["model"] == REFERENCE_VIDEO_MODEL
        assert artifact["metadata"]["duration"] == 4
        assert store.budget()["categories"]["video"]["reserved_usd"] == 0
        assert store.budget()["categories"]["video"]["reported_usd"] == 0
    finally:
        await runner.http.close()


@pytest.mark.parametrize("options,match", [
    ({"model_override": "arbitrary/model"}, "Unvalidated"),
    ({"model_override": None}, "does not support"),
    ({"duration": 31}, "integer seconds"),
    ({"duration": 4.5}, "integer seconds"),
    ({"prompt": "Use @Image2 with @Video1"}, "absent"),
    ({"prompt": "Use @Audio1"}, "Audio"),
    ({"video_reference_ids": []}, "1–10 motion videos"),
    ({"resolution": "4K"}, "supported resolution"),
])
@pytest.mark.asyncio
async def test_invalid_contract_has_no_remote_request_or_reservation(tmp_path, clip, options, match):
    _, store, runner, image, video, calls = setup(tmp_path, clip)
    try:
        args = {"prompt": "Use @Image1 and @Video1", "reference_ids": [image["id"]], "duration": 4,
                "model_override": REFERENCE_VIDEO_MODEL, "video_reference_ids": [video["id"]]}
        args.update(options)
        before = store.snapshot()
        with pytest.raises(ValueError, match=match):
            await runner.video(**args)
        assert calls == []
        assert store.snapshot() == before
    finally:
        await runner.http.close()


@pytest.mark.asyncio
async def test_corrupt_video_hash_and_invalid_image_dimensions_fail_before_spending(tmp_path, clip):
    _, store, runner, image, video, calls = setup(tmp_path, clip)
    try:
        store.artifact_path(video["id"]).write_bytes(b"tampered")
        with pytest.raises(ValueError, match="recorded hash"):
            await runner.video("Use references", [image["id"]], model_override=REFERENCE_VIDEO_MODEL, video_reference_ids=[video["id"]])
        store.artifact_path(video["id"]).write_bytes(clip)
        tiny = BytesIO(); Image.new("RGB", (10, 10)).save(tiny, format="PNG")
        too_small = store.publish_artifact(tiny.getvalue(), kind="image", mime_type="image/png")
        with pytest.raises(ValueError, match="dimensions"):
            await runner.video("Use references", [too_small["id"]], model_override=REFERENCE_VIDEO_MODEL, video_reference_ids=[video["id"]])
        assert calls == [] and store.snapshot()["jobs"] == []
    finally:
        await runner.http.close()


@pytest.mark.asyncio
async def test_unknown_submit_is_not_retried_and_retains_full_input_output_hold(tmp_path, clip):
    settings, store, runner, image, video, calls = setup(tmp_path, clip)
    await runner.http.close()
    def interrupted(request):
        calls.append(request)
        raise httpx.ReadError("Synthetic interrupted response", request=request)
    runner = ProviderRunner(store, settings, ProviderHTTP(settings, httpx.MockTransport(interrupted)))
    try:
        with pytest.raises(ProviderError):
            await runner.video("Use @Image1 and @Video1", [image["id"]], duration=4, model_override=REFERENCE_VIDEO_MODEL, video_reference_ids=[video["id"]])
        assert len(calls) == 1
        job = store.snapshot()["jobs"][0]
        assert job["status"] == "submission_unknown"
        expected = estimate_reference_cost(864, 496, 4, 4, resolution="480p")*1.2
        assert store.budget()["categories"]["video"]["reserved_usd"] == pytest.approx(expected, abs=1e-6)
        assert store.recover()["resumable_jobs"] == []
    finally:
        await runner.http.close()


@pytest.mark.asyncio
async def test_recovered_reference_job_uses_persisted_pricing_and_get_only(tmp_path, clip):
    settings, store, runner, image, video, calls = setup(tmp_path, clip)
    try:
        estimate = {"input_video_seconds": 4, "resolution": "480p"}
        original = runner.prepare("fal", REFERENCE_VIDEO_MODEL, {"prompt": "test"}, "video", 2, pricing_estimate=estimate)
        store.record_submission(original["id"], "remote", polling_locator="https://queue.fal.run/bytedance/seedance-2.5/requests/remote/status")
        reopened = ProductionStore(tmp_path, opening_entries=())
        recovered = reopened.recover()["resumable_jobs"]
        artifact, _ = await ProviderRunner(reopened, settings, runner.http).poll_fal_video(recovered[0])
        assert calls and all(request.method == "GET" for request in calls)
        assert artifact["metadata"]["fps"] == 24
        assert reopened.get_job(original["id"])["cost_usd"] == pytest.approx(estimate_reference_cost(640, 360, 4, 4, resolution="480p"), abs=1e-6)
    finally:
        await runner.http.close()
