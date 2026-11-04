"""Historical snapshots must never borrow facts from the current projection."""

from pathlib import Path
from io import BytesIO
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from ghost_director.api import create_app
from ghost_director.jobs.store import ProductionStore
from ghost_director.references import ReferenceRegistry
from ghost_director.runtime.replay import RECORD_COLLECTIONS


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProductionStore(Path(self.tmp.name), opening_entries=())

    def test_creative_attribution_survives_activity_tail_rollover(self):
        self.store.append_event('take.accepted',{'take_id':'original','actor':'build-system','reason':'Reviewed actual output'})
        self.store.append_event('demo.disposition_imported',{'take_id':'imported','actor':'original-reviewer','reason':'Historical decision','evidence_origin':'imported'})
        for index in range(250):self.store.append_event('test.activity',{'index':index})
        events=self.store.snapshot()['events']
        self.assertEqual(len(events),202)
        self.assertEqual([event['type'] for event in events[:2]],['take.accepted','demo.disposition_imported'])
        self.assertEqual(events[0]['payload']['actor'],'build-system')
        self.assertEqual(events[1]['payload']['reason'],'Historical decision')
        self.assertEqual(len({event['seq'] for event in events}),len(events))
        self.assertEqual([event['seq'] for event in events],sorted(event['seq'] for event in events))

    def scene(self, scene_id, parent, run_id=None):
        return self.store.publish_scene({"id": scene_id, "parent_id": parent, "entities": [], "cameras": []}, expected_revision=parent, actor="test", run_id=run_id)

    def job(self, job_id, run_id):
        self.store.create_job({"id": job_id, "run_id": run_id})
        self.store.reserve(job_id, "meta", 1)
        self.store.begin_submission(job_id)

    def test_recorded_endpoint_excludes_future_revisions_records_and_charges(self):
        self.scene("initial", None)
        self.store.create_run("run-old")
        self.store.create_task({"id": "task", "run_id": "run-old"})
        self.store.update_task("task", "running")
        self.store.update_task("task", "completed", result={"value": "old"})
        self.job("old-job", "run-old")
        self.store.finish_job("old-job", cost_usd=.5)
        self.store.upsert_record("models", {"id": "model", "version": "old"}, event_type="capability.verified", run_id="run-old")
        take = {"id": "take-old", "shot_id": "shot", "scene_revision_id": "initial", "package_id": "package", "job_id": "old-job", "artifact_id": "media", "run_id": "run-old", "status": "unreviewed"}
        self.store.upsert_record("takes", take, event_type="take.created", run_id="run-old")
        self.scene("recorded", "initial", "run-old")
        self.store.finish_run("run-old")
        expected = self.store.run_snapshot("run-old")
        self.assertEqual(expected["budget"]["committed_usd"], .5)

        self.store.create_run("run-new")
        self.job("new-job", "run-new")
        self.store.finish_job("new-job", cost_usd=.7)
        self.store.upsert_record("models", {"id": "model", "version": "new"}, event_type="capability.verified", run_id="run-new")
        self.store.upsert_record("takes", {**take, "status": "accepted"}, run_id="run-new")
        self.store.upsert_record("takes", {**take, "id": "take-future"}, run_id="run-new")
        future = self.store.publish_artifact("future", mime_type="text/plain", run_id="run-new")
        self.scene("future", "recorded", "run-new")
        self.store.set_paused(True)
        self.store.finish_run("run-new")
        before_read = self.store.snapshot()

        result = self.store.run_snapshot("run-old")
        self.assertEqual(result, expected)
        self.assertEqual(result["scene"]["id"], "recorded")
        self.assertEqual(result["models"], [{"id": "model", "version": "old"}])
        self.assertEqual(result["takes"], [take])
        self.assertEqual(result["tasks"][0]["status"], "completed")
        self.assertEqual(result["jobs"][0]["cost_usd"], .5)
        self.assertNotIn(future["id"], {row["id"] for row in result["artifacts"]})
        self.assertEqual(self.store.snapshot(), before_read)
        replay = self.store.replay("run-old")
        self.assertNotIn(future["id"], {row["id"] for row in replay["artifacts"]})
        self.assertTrue(all(event["seq"] <= expected["last_seq"] for event in replay["events"]))

    def test_incomplete_run_freezes_at_last_known_fact_and_marks_budget_unknown(self):
        self.scene("initial", None)
        self.store.create_run("partial")
        self.store.create_task({"id": "task", "run_id": "partial"})
        self.store.update_task("task", "running")
        self.store.create_run("later")
        self.scene("later-scene", "initial", "later")
        snapshot = self.store.run_snapshot("partial")
        self.assertEqual(snapshot["scene"]["id"], "initial")
        self.assertEqual(snapshot["tasks"][0]["status"], "running")
        self.assertFalse(snapshot["production"]["replay_complete"])
        self.assertEqual(snapshot["budget"]["status"], "historical_unavailable")
        self.assertNotIn("committed_usd", snapshot["budget"])

    def test_reference_motion_and_reconstruction_records_freeze_at_run_endpoint(self):
        self.assertEqual(RECORD_COLLECTIONS, self.store.RECORD_COLLECTIONS)
        self.scene('initial', None)
        self.store.create_run('reference-run')
        registry = ReferenceRegistry(self.store)
        asset = registry.create_asset({'category':'prop','name':'Original cup'}, run_id='reference-run')
        original_revision = asset['revision_id']
        stream = BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(stream, format='PNG')
        asset = registry.attach_image(asset['id'], asset['revision_id'], stream.getvalue(), slot='primary', mime_type='image/png', run_id='reference-run')
        asset = registry.decide(asset['id'], asset['revision_id'], 'approved', actor='local-operator', reason='Reviewed original cup', run_id='reference-run')
        preview = {'id':'preview-original','shot_id':'shot-original','scene_revision_id':'initial','video_id':'motion-original'}
        reconstruction = {'id':'geometry-original','reference_pin':{'asset_id':asset['id'],'revision_id':asset['revision_id'],'state':'default'},'artifact_id':'model-original'}
        self.store.upsert_record('motion_previews', preview, run_id='reference-run')
        self.store.upsert_record('reconstructions', reconstruction, run_id='reference-run')
        review = {'id':'review-original','reconstruction_id':'geometry-original','decision':'accepted','notes':'Historical operator review'}
        self.store.upsert_record('reconstruction_reviews', review, run_id='reference-run')
        self.store.finish_run('reference-run')
        expected = self.store.run_snapshot('reference-run')
        self.assertEqual(expected['reference_assets'], [asset])
        self.assertEqual({item['id'] for item in expected['reference_versions']}, {original_revision, asset['revision_id']})
        self.assertEqual(expected['reference_decisions'][0]['revision_id'], asset['revision_id'])
        self.assertEqual(expected['motion_previews'], [preview])
        self.assertEqual(expected['reconstructions'], [reconstruction])
        self.assertEqual(expected['reconstruction_reviews'], [review])

        self.store.create_run('future-run')
        stream = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(stream, format='PNG')
        changed = registry.attach_image(asset['id'], asset['revision_id'], stream.getvalue(), slot='primary', mime_type='image/png', run_id='future-run')
        registry.decide(changed['id'], changed['revision_id'], 'rejected', actor='local-operator', reason='Wrong color', run_id='future-run')
        self.store.upsert_record('motion_previews', {**preview,'id':'preview-future'}, run_id='future-run')
        self.store.upsert_record('reconstructions', {**reconstruction,'id':'geometry-future'}, run_id='future-run')
        self.store.upsert_record('reconstruction_reviews', {**review,'id':'review-future','decision':'changes_requested'}, run_id='future-run')
        self.store.finish_run('future-run')
        before = self.store.snapshot()
        self.assertEqual(self.store.run_snapshot('reference-run'), expected)
        with TestClient(create_app(self.store)) as client:
            response = client.get('/api/productions/ghost-director-hackathon/runs/reference-run/snapshot')
            self.assertEqual(response.status_code, 200, response.text)
            for collection in ('reference_assets','reference_versions','reference_decisions','motion_previews','reconstructions','reconstruction_reviews'):
                self.assertEqual(response.json()[collection], expected[collection])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(before['jobs'], [])

    def test_snapshot_http_contract_and_missing_run(self):
        self.scene("initial", None)
        self.store.create_run("historical")
        self.store.finish_run("historical")
        self.scene("later", "initial")
        calls = []
        async def handler(command):
            calls.append(command)
        with TestClient(create_app(self.store, handler)) as client:
            response = client.get("/api/productions/ghost-director-hackathon/runs/historical/snapshot")
            self.assertEqual(response.status_code, 200, response.text)
            snapshot = response.json()
            self.assertEqual(snapshot["scene"]["id"], "initial")
            self.assertEqual(snapshot["production"]["mode"], "replay")
            self.assertTrue(snapshot["budget"]["historical"])
            self.assertEqual(calls, [])
            self.assertEqual(client.get("/api/productions/ghost-director-hackathon/runs/missing/snapshot").status_code, 404)

    def test_cancel_requested_job_can_keep_polling_without_losing_intent(self):
        self.store.create_run("run")
        self.job("job", "run")
        self.store.record_submission("job", "remote")
        self.store.request_cancel("job")
        self.assertEqual(self.store.set_job_running("job")["status"], "cancel_requested")
        self.assertEqual(self.store.budget()["committed_usd"], 1)
        self.store.finish_job("job", "cancelled", cost_usd=.1)
        self.assertEqual(self.store.budget()["committed_usd"], .1)

    def test_accepted_head_take_status_and_disposition_commit_or_rollback_together(self):
        self.scene("initial", None)
        self.store.upsert_record("takes", {"id": "take", "status": "unreviewed"})
        original = self.store._event
        def fail(db, kind, *args, **kwargs):
            if kind == "revision.accepted":
                raise RuntimeError("crash before commit")
            return original(db, kind, *args, **kwargs)
        with patch.object(self.store, "_event", fail), self.assertRaises(RuntimeError):
            self.store.publish_scene({"id": "next", "parent_id": "initial"}, expected_revision="initial", actor="test", take_id="take")
        self.assertEqual(self.store.scene()["id"], "initial")
        self.assertEqual(self.store.get_record("takes", "take")["status"], "unreviewed")
        self.assertEqual(self.store.snapshot()["dispositions"], [])
        self.store.publish_scene({"id": "next", "parent_id": "initial"}, expected_revision="initial", actor="test", take_id="take")
        self.assertEqual(self.store.scene()["id"], "next")
        self.assertEqual(self.store.get_record("takes", "take")["status"], "accepted")
        self.assertEqual(len(self.store.snapshot()["dispositions"]), 1)

    def test_rejection_status_and_disposition_are_atomic(self):
        self.scene("initial", None)
        self.store.upsert_record("takes", {"id": "take", "status": "reviewed"})
        self.store.reject_take("take", actor="test", reason="Visible drift", expected_revision="initial")
        self.assertEqual(self.store.get_record("takes", "take")["status"], "rejected")
        self.assertEqual(self.store.scene()["id"], "initial")

    def test_completed_video_without_take_is_materializable_without_new_charge(self):
        self.store.create_run("run")
        self.store.create_job({"id": "video-job", "provider": "openrouter", "run_id": "run"})
        self.store.reserve("video-job", "video", 2)
        self.store.begin_submission("video-job")
        self.store.record_submission("video-job", "remote-video")
        artifact = self.store.publish_artifact(b"registered-video-bytes", kind="video", mime_type="video/mp4", run_id="run")
        self.store.finish_job("video-job", result_refs=[artifact["id"]], cost_usd=1)
        before = self.store.snapshot()
        recovered = ProductionStore(self.tmp.name, opening_entries=()).recover()
        self.assertEqual([row["id"] for row in recovered["materializable_jobs"]], ["video-job"])
        self.assertEqual(recovered["materializable_jobs"][0]["result_refs"], [artifact["id"]])
        self.assertEqual(recovered["resumable_jobs"], [])
        self.assertEqual(self.store.snapshot(), before)
        # Even an unfinished review counts as a materialized take: restarting
        # must not pay for another judge call with an ambiguous prior outcome.
        self.store.upsert_record("takes", {"id": "take", "job_id": "video-job", "status": "unreviewed"})
        self.assertEqual(self.store.recover()["materializable_jobs"], [])


if __name__ == "__main__":
    unittest.main()
