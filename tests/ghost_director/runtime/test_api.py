"""HTTP/SSE boundary tests with a bounded local command handler."""

import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from ghost_director.api import create_app, event_cursor, event_stream
from ghost_director.jobs.store import ProductionStore


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProductionStore(self.tmp.name, opening_entries=())
        self.calls = []
        async def handler(command):
            self.calls.append(command)
            return {"performed": command["operation"]}
        self.client = TestClient(create_app(self.store, handler))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.base = "/api/productions/ghost-director-hackathon"

    def wait_command(self, command_id):
        for _ in range(100):
            response = self.client.get(f"{self.base}/commands/{command_id}")
            if response.json().get("status") in {"completed", "failed"}:
                return response.json()
            time.sleep(0.01)
        self.fail("Command did not settle.")

    def test_snapshot_has_public_contract_and_artifact_links(self):
        artifact = self.store.publish_artifact({"value": 3})
        self.store.publish_scene({"id": "scene-1", "parent_id": None, "entities": [], "cameras": []}, expected_revision=None, actor="engineering-test")
        response = self.client.get(f"{self.base}/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        self.assertEqual(snapshot["production"]["revision_id"], "scene-1")
        self.assertEqual(snapshot["scene"]["id"], "scene-1")
        self.assertEqual(snapshot["artifacts"][0]["id"], artifact["id"])
        self.assertNotIn("path", snapshot["artifacts"][0])
        self.assertEqual(self.client.get(snapshot["artifacts"][0]["url"]).json(), {"value": 3})

    def test_shutdown_closes_resources_after_background_settles(self):
        order = []
        async def recover(_state):
            await asyncio.sleep(.01)
            order.append("recovery-finished")
        async def close():
            order.append("client-closed")
        with TestClient(create_app(self.store, recovery_handler=recover, shutdown_handler=close)):
            pass
        self.assertEqual(order, ["recovery-finished", "client-closed"])

    def test_duplicate_command_never_reinvokes_handler(self):
        command = {"id": "cmd-1", "operation": "direction", "payload": {"text": "Close-up"}}
        first = self.client.post(f"{self.base}/commands", json=command)
        self.assertEqual(first.status_code, 202, first.text)
        self.wait_command("cmd-1")
        again = self.client.post(f"{self.base}/commands", json=command)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(len(self.calls), 1)
        conflict = self.client.post(f"{self.base}/commands", json={**command, "payload": {"text": "Wide"}})
        self.assertEqual(conflict.status_code, 409)

    def test_inspection_and_replay_never_dispatch(self):
        self.store.create_run("run-1", mode="fixture")
        self.store.publish_artifact("output", kind="prompt", mime_type="text/plain", run_id="run-1")
        before = self.store.snapshot()
        self.assertEqual(self.client.get(f"{self.base}/snapshot").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/runs/run-1").status_code, 200)
        replay = self.client.get(f"{self.base}/runs/run-1/replay")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["mode"], "replay")
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.calls, [])
        rejected = self.client.post(f"{self.base}/commands", json={"id": "replay-command", "operation": "generate"}, headers={"X-Ghost-View-Mode": "replay"})
        self.assertEqual(rejected.status_code, 409)

    def test_unknown_operation_external_origin_and_host_are_rejected(self):
        invalid = self.client.post(f"{self.base}/commands", json={"id": "a", "operation": "shell", "payload": {"command": "ls"}})
        self.assertEqual(invalid.status_code, 422)
        external = self.client.post(f"{self.base}/commands", json={"id": "b", "operation": "generate"}, headers={"Origin": "https://attacker.test"})
        self.assertEqual(external.status_code, 403)
        host = self.client.get("/health", headers={"Host": "attacker.test"})
        self.assertEqual(host.status_code, 400)
        form = self.client.post(f"{self.base}/commands", content="{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(form.status_code, 415)
        self.assertEqual(self.calls, [])

    def test_invalid_cursor_rejected_before_sse_headers_and_scope_isolated(self):
        self.assertEqual(self.client.get(f"{self.base}/events?after=999").status_code, 409)
        self.assertEqual(self.client.get(f"{self.base}/events?after=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/productions/other/snapshot").status_code, 404)

    def test_artifact_active_content_is_not_executable_and_range_works(self):
        html = self.store.publish_artifact("<script>alert(1)</script>", mime_type="text/html")
        response = self.client.get(html["url"])
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        video = self.store.publish_artifact(b"0123456789", mime_type="video/mp4")
        partial = self.client.get(video["url"], headers={"Range": "bytes=2-5"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"2345")

    def test_handler_failure_is_durable_and_redacted(self):
        secret = "fake-private-value"
        store = ProductionStore(Path(self.tmp.name) / "failed", secrets=(secret,), opening_entries=())
        async def failing(_command):
            raise RuntimeError(secret)
        with TestClient(create_app(store, failing)) as client:
            response = client.post(f"{self.base}/commands", json={"id": "fail", "operation": "direction"})
            self.assertEqual(response.status_code, 202)
            for _ in range(100):
                receipt = client.get(f"{self.base}/commands/fail").json()
                if receipt["status"] == "failed":
                    break
                time.sleep(0.01)
            self.assertEqual(receipt["status"], "failed")
            self.assertNotIn(secret, json.dumps(receipt))
            self.assertNotIn(secret, json.dumps(store.snapshot()))

    def test_unwired_generation_is_unavailable_without_recorded_fake_work(self):
        with TestClient(create_app(self.store)) as client:
            response = client.post(f"{self.base}/commands", json={"id": "absent", "operation": "generate"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.store.events(), [])

    def test_startup_recovery_hook_receives_only_known_remote_jobs_for_polling(self):
        store = ProductionStore(Path(self.tmp.name) / "restart", opening_entries=())
        store.submit_command({"id": "interrupted-command", "operation": "generate"})
        for job_id in ("sync", "async", "unknown"):
            store.create_job({"id": job_id})
            store.reserve(job_id, "meta", 1)
            store.begin_submission(job_id)
        store.finish_job("sync", cost_usd=0.1, receipt={"response_id": "not-pollable"})
        store.record_submission("async", "pollable-id")
        recovered = []
        async def recover(state):
            recovered.append(state)
        with TestClient(create_app(store, recovery_handler=recover)) as client:
            for _ in range(100):
                if recovered:
                    break
                time.sleep(0.01)
            self.assertEqual([j["id"] for j in recovered[0]["resumable_jobs"]], ["async"])
            self.assertEqual([j["id"] for j in recovered[0]["submission_unknown"]], ["unknown"])
            self.assertEqual(store.command_receipt("interrupted-command")["status"], "interrupted")
            self.assertEqual(client.get(f"{self.base}/jobs/sync").json()["status"], "succeeded")
            self.assertEqual(client.get(f"{self.base}/budget").status_code, 200)
            self.assertEqual(client.get(f"{self.base}/scenes/missing").status_code, 404)


class SseTests(unittest.IsolatedAsyncioTestCase):
    def test_reconnect_header_supersedes_initial_query(self):
        self.assertEqual(event_cursor("0", "18"), 18)
        self.assertEqual(event_cursor("4", None), 4)
        for bad in ("-1", "1.2", "abc", "1\n2"):
            with self.assertRaises(ValueError):
                event_cursor("0", bad)

    async def test_stream_backlog_then_new_commit_no_duplicate_or_race(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProductionStore(root, opening_entries=())
            store.append_event("first", {"n": 1})
            store.append_event("second", {"n": 2})
            closed = False
            async def disconnected():
                return closed
            stream = event_stream(store, 1, disconnected, poll_seconds=0.001)
            self.assertEqual(await asyncio.wait_for(anext(stream), timeout=.1), ": connected\n\n")
            first = await asyncio.wait_for(anext(stream), timeout=1)
            self.assertIn("id: 2\n", first)
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.01)
            store.append_event("third", {"n": 3})
            second = await asyncio.wait_for(waiting, timeout=1)
            self.assertIn("id: 3\n", second)
            self.assertNotIn("id: 2\n", second)
            closed = True
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)

    async def test_idle_stream_connects_without_waiting_for_heartbeat(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProductionStore(root, opening_entries=())
            async def disconnected():
                return False
            stream = event_stream(store, 0, disconnected, heartbeat_seconds=15)
            self.assertEqual(await asyncio.wait_for(anext(stream), timeout=.1), ": connected\n\n")
            self.assertEqual(store.snapshot()["last_seq"], 0)
            await stream.aclose()


if __name__ == "__main__":
    unittest.main()
