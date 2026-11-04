"""Fault and concurrency tests; no provider sockets or credentials are used."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ghost_director.jobs.store import BudgetExceeded, Conflict, InvalidTransition, NotFound, ProductionStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProductionStore(self.tmp.name, opening_entries=())

    def paid_job(self, job_id="job-1", category="video", amount=2, **extra):
        job = self.store.create_job({"id": job_id, "provider": "fake", **extra})
        self.store.reserve(job["id"], category, amount)
        return job["id"]

    def test_opening_receipts_are_exact_distinct_and_imported_once(self):
        root = Path(self.tmp.name) / "opening"
        store = ProductionStore(root)
        self.assertEqual(store.budget()["committed_usd"], 0.483489)
        self.assertEqual(store.budget()["categories"]["video"]["reported_usd"], 0.415481)
        self.assertEqual(store.budget()["categories"]["meta"]["estimated_usd"], 0.028008)
        second = ProductionStore(root)
        self.assertEqual(second.budget()["committed_usd"], 0.483489)

    def test_recovery_leaves_other_live_worker_owned_jobs_tasks_commands_alone(self):
        script = """
import sys
from ghost_director.jobs.store import ProductionStore
store=ProductionStore(sys.argv[1],opening_entries=())
store.create_run('worker-run')
store.create_task({'id':'worker-task','run_id':'worker-run'})
store.update_task('worker-task','running')
store.create_job({'id':'worker-job','run_id':'worker-run','task_id':'worker-task'})
store.reserve('worker-job','meta',1)
store.begin_submission('worker-job')
store.submit_command({'id':'worker-command','operation':'generate'})
print('ready',flush=True)
sys.stdin.buffer.read(1)
"""
        child = subprocess.Popen([sys.executable, "-c", script, self.tmp.name], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            before = self.store.snapshot()
            recovery = self.store.recover()
            self.assertEqual({row["kind"] for row in recovery["active_owners"]}, {"job", "task", "command"})
            self.assertEqual(recovery["submission_unknown"], [])
            self.assertEqual(recovery["resumable_jobs"], [])
            self.assertEqual(self.store.snapshot(), before)
            _, error = child.communicate("x", timeout=5)
            self.assertEqual(child.returncode, 0, error)
            recovery = self.store.recover()
            self.assertEqual([row["id"] for row in recovery["submission_unknown"]], ["worker-job"])
            self.assertEqual(self.store.get_run("worker-run")["tasks"][0]["status"], "interrupted")
            self.assertEqual(self.store.command_receipt("worker-command")["status"], "interrupted")
            self.assertEqual(self.store.budget()["committed_usd"], 1)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_concurrent_category_reservations_do_not_overspend(self):
        for i in range(8):
            self.store.create_job({"id": f"job-{i}"})
        barrier = threading.Barrier(8)
        def reserve(i):
            barrier.wait()
            try:
                self.store.reserve(f"job-{i}", "video", 10)
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(outcomes), 6)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 60)
        self.assertEqual(sum(e["type"] == "budget.reserved" for e in self.store.events()), 6)

    def test_total_ceiling_and_rounding_are_enforced(self):
        store = ProductionStore(Path(self.tmp.name) / "small", ceiling_usd=1, allocations={"meta": 2}, opening_entries=())
        store.create_job({"id": "a"})
        store.reserve("a", "meta", "0.9999991")
        self.assertEqual(store.budget()["committed_usd"], 1)
        store.create_job({"id": "b"})
        with self.assertRaises(BudgetExceeded):
            store.reserve("b", "meta", "0.000001")
        for invalid in (float("nan"), -1, float("inf")):
            with self.assertRaises(ValueError):
                store.reserve("b", "meta", invalid)

    def test_failed_transaction_rolls_back_job_budget_and_event(self):
        self.store.create_job({"id": "a"})
        original = self.store._event
        before = self.store.snapshot()["last_seq"]
        def fail(db, kind, *args, **kwargs):
            if kind == "budget.reserved":
                raise RuntimeError("injected crash before commit")
            return original(db, kind, *args, **kwargs)
        with patch.object(self.store, "_event", fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.store.reserve("a", "meta", 1)
        self.assertEqual(self.store.get_job("a")["status"], "prepared")
        self.assertEqual(self.store.budget()["committed_usd"], 0)
        self.assertEqual(self.store.snapshot()["last_seq"], before)

    def test_duplicate_command_conflicting_body_and_stale_parent(self):
        command = {"id": "cmd-1", "operation": "generate", "expected_revision": None, "payload": {"shot": "a"}}
        first = self.store.submit_command(command)
        last_seq = self.store.snapshot()["last_seq"]
        again = self.store.submit_command(command)
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["accepted_at"], first["accepted_at"])
        self.assertEqual(self.store.snapshot()["last_seq"], last_seq)
        with self.assertRaises(Conflict):
            self.store.submit_command({**command, "payload": {"shot": "b"}})
        with self.assertRaises(Conflict):
            self.store.submit_command({"id": "new", "expected_revision": "old"})
        with self.assertRaises(Conflict):
            self.store.submit_command({"id": "replay", "mode": "replay"})

    def test_submission_claim_is_exclusive_and_restart_never_reposts(self):
        job_id = self.paid_job()
        self.store.begin_submission(job_id)
        with self.assertRaises(InvalidTransition):
            self.store.begin_submission(job_id)
        restarted = ProductionStore(self.tmp.name, opening_entries=())
        recovered = restarted.recover()
        self.assertEqual(len(recovered["submission_unknown"]), 1)
        self.assertEqual(restarted.get_job(job_id)["status"], "submission_unknown")
        self.assertEqual(restarted.budget()["categories"]["video"]["reserved_usd"], 2)
        with self.assertRaises(InvalidTransition):
            restarted.begin_submission(job_id)

    def test_real_process_exit_after_submit_intent_and_remote_receipt(self):
        for confirmed in (False, True):
            root = str(Path(self.tmp.name) / ("confirmed" if confirmed else "ambiguous"))
            script = "\n".join([
                "import os", "from ghost_director.jobs.store import ProductionStore",
                f"s=ProductionStore({root!r}, opening_entries=())",
                "s.create_job({'id':'job'})", "s.reserve('job','video',2)",
                "s.begin_submission('job')",
                "s.record_submission('job','remote-id')" if confirmed else "pass",
                "os._exit(17)",
            ])
            process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
            self.assertEqual(process.returncode, 17, process.stderr)
            recovered_store = ProductionStore(root, opening_entries=())
            recovery = recovered_store.recover()
            expected = "resumable_jobs" if confirmed else "submission_unknown"
            self.assertEqual(len(recovery[expected]), 1)
            self.assertEqual(recovered_store.budget()["committed_usd"], 2)
            with self.assertRaises(InvalidTransition):
                recovered_store.begin_submission("job")

    def test_confirmed_remote_job_resumes_same_id(self):
        job_id = self.paid_job()
        self.store.begin_submission(job_id)
        self.store.record_submission(job_id, "remote-existing")
        recovered = ProductionStore(self.tmp.name, opening_entries=()).recover()
        self.assertEqual(recovered["resumable_jobs"][0]["remote_id"], "remote-existing")
        self.assertEqual(recovered["submission_unknown"], [])
        with self.assertRaises(InvalidTransition):
            self.store.begin_submission(job_id)

    def test_one_active_video_includes_unknown_submission(self):
        a = self.paid_job("a")
        b = self.paid_job("b")
        self.store.begin_submission(a)
        self.store.mark_submission_unknown(a)
        with self.assertRaises(Conflict):
            self.store.begin_submission(b)

    def test_explicit_unknown_retirement_keeps_hold_and_never_retries(self):
        first = self.paid_job("unknown", amount=2)
        second = self.paid_job("new-experiment", amount=3)
        self.store.begin_submission(first)
        self.store.mark_submission_unknown(first)
        before = self.store.budget()
        retired = self.store.retire_unknown(first, actor="operator", reason="No remote ID after read-only reconciliation; accept potential overlap.")
        self.assertEqual(retired["status"], "submission_unknown")
        self.assertTrue(retired["dispatch_retired"])
        self.assertEqual(retired["dispatch_retirement"]["held_usd"], 2)
        self.assertTrue(retired["dispatch_retirement"]["potential_remote_overlap"])
        self.assertEqual(self.store.budget(), before)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 5)
        with self.assertRaises(InvalidTransition):
            self.store.begin_submission(first)
        restarted = ProductionStore(self.tmp.name, opening_entries=())
        recovered = restarted.recover()
        self.assertEqual(recovered["submission_unknown"][0]["id"], first)
        self.assertTrue(recovered["submission_unknown"][0]["dispatch_retired"])
        self.assertEqual(recovered["resumable_jobs"], [])
        restarted.begin_submission(second)
        self.assertEqual(restarted.get_job(second)["status"], "submitting")
        events = [event for event in self.store.events() if event["type"] == "job.dispatch_retired"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["dispatch_retirement"]["actor"], "operator")

    def test_retirement_requires_unknown_state_and_nonempty_actor_reason(self):
        job = self.paid_job()
        for state in ("reserved", "submitting", "queued", "succeeded"):
            with self.assertRaises(InvalidTransition):
                self.store.retire_unknown(job, actor="operator", reason="reviewed")
            if state == "reserved":
                self.store.begin_submission(job)
            elif state == "submitting":
                self.store.record_submission(job, "remote")
            elif state == "queued":
                self.store.finish_job(job, cost_usd=1)
        for actor, reason in (("", "reason"), (" ", "reason"), ("actor", ""), ("actor", " \n"), (None, "reason")):
            with self.assertRaises(ValueError):
                self.store.retire_unknown(job, actor=actor, reason=reason)
        with self.assertRaises(ValueError):
            self.store.create_job({"id": "forged", "dispatch_retired": True})

    def test_unknown_retirement_is_idempotent_and_decision_cannot_be_rewritten(self):
        job = self.paid_job()
        self.store.begin_submission(job)
        self.store.mark_submission_unknown(job)
        self.store.retire_unknown(job, actor="operator", reason="reviewed")
        before = self.store.snapshot()
        self.store.retire_unknown(job, actor="operator", reason="reviewed")
        self.assertEqual(self.store.snapshot(), before)
        with self.assertRaises(Conflict):
            self.store.retire_unknown(job, actor="someone-else", reason="different")

    def test_late_remote_receipt_reactivates_retired_unknown_for_concurrency(self):
        job = self.paid_job()
        next_job = self.paid_job("next")
        self.store.begin_submission(job)
        self.store.mark_submission_unknown(job)
        self.store.retire_unknown(job, actor="operator", reason="reviewed")
        recovered = self.store.record_submission(job, "late-remote-id")
        self.assertFalse(recovered["dispatch_retired"])
        self.assertEqual(recovered["dispatch_retirement"]["actor"], "operator")
        self.assertEqual(recovered["status"], "queued")
        with self.assertRaises(Conflict):
            self.store.begin_submission(next_job)
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 4)

    def test_retirement_event_failure_rolls_back_exemption(self):
        job = self.paid_job()
        self.store.begin_submission(job)
        self.store.mark_submission_unknown(job)
        before = self.store.snapshot()
        original = self.store._event
        def fail(db, kind, *args, **kwargs):
            if kind == "job.dispatch_retired":
                raise RuntimeError("retirement audit failure")
            return original(db, kind, *args, **kwargs)
        with patch.object(self.store, "_event", fail):
            with self.assertRaises(RuntimeError):
                self.store.retire_unknown(job, actor="operator", reason="reviewed")
        self.assertEqual(self.store.snapshot(), before)

    def test_synchronous_result_is_terminal_and_never_pollable(self):
        job_id = self.paid_job(category="meta")
        self.store.begin_submission(job_id)
        output = self.store.publish_artifact({"answer": 42})
        result = self.store.finish_job(job_id, result_refs=[output["id"]], cost_usd="0.004", receipt={"response_id": "completion-not-a-job"})
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["remote_id"])
        self.assertEqual(self.store.recover()["resumable_jobs"], [])
        self.assertEqual(self.store.budget()["categories"]["meta"]["reserved_usd"], 0)
        self.assertEqual(self.store.budget()["categories"]["meta"]["reported_usd"], 0.004)

    def test_unknown_billing_retains_hold_until_later_receipt(self):
        job_id = self.paid_job()
        self.store.begin_submission(job_id)
        self.store.finish_job(job_id, "failed", error="provider failed without billing receipt")
        self.assertEqual(self.store.budget()["categories"]["video"]["reserved_usd"], 2)
        self.store.reconcile(job_id, "0.5", basis="estimated")
        self.assertEqual(self.store.budget()["categories"]["video"]["estimated_usd"], 0.5)
        self.store.reconcile(job_id, "0.7", basis="reported")
        self.assertEqual(self.store.budget()["categories"]["video"]["estimated_usd"], 0)
        self.assertEqual(self.store.budget()["categories"]["video"]["reported_usd"], 0.7)
        with self.assertRaises(Conflict):
            self.store.reconcile(job_id, "0.2", basis="estimated")

    def test_reported_overage_is_recorded_and_blocks_new_dispatch_budget(self):
        job_id = self.paid_job()
        self.store.begin_submission(job_id)
        self.store.finish_job(job_id, cost_usd=61)
        self.assertTrue(self.store.budget()["over_budget"])
        self.store.create_job({"id": "new"})
        with self.assertRaises(BudgetExceeded):
            self.store.reserve("new", "meta", 1)

    def test_cancellation_before_submit_releases_but_remote_request_does_not(self):
        a = self.paid_job("a")
        self.store.request_cancel(a)
        self.assertEqual(self.store.get_job(a)["status"], "cancelled")
        self.assertEqual(self.store.budget()["committed_usd"], 0)
        b = self.paid_job("b")
        self.store.begin_submission(b)
        self.store.record_submission(b, "remote-b")
        self.store.request_cancel(b)
        self.assertEqual(self.store.get_job(b)["status"], "cancel_requested")
        self.assertEqual(self.store.budget()["committed_usd"], 2)

    def test_root_repair_limit_persists_and_child_cannot_reset_root(self):
        self.store.create_job({"id": "root", "root_id": "shot-root"})
        child = self.store.create_job({"id": "child", "parent_job_id": "root"}, automatic_repair=True)
        self.assertEqual(child["root_id"], "shot-root")
        restarted = ProductionStore(self.tmp.name, opening_entries=())
        with self.assertRaises(Conflict):
            restarted.create_job({"id": "grandchild", "parent_job_id": "child"}, automatic_repair=True)
        with self.assertRaises(Conflict):
            restarted.create_job({"id": "fake-root", "parent_job_id": "child", "root_id": "invented"}, automatic_repair=True)

    def test_scene_head_atomic_stale_rejection_and_reject_preserves_scene(self):
        self.store.publish_scene({"id": "scene-1", "entities": []}, expected_revision=None, actor="engineering-test")
        before = self.store.scene()
        self.store.reject_take("take-1", actor="engineering-test", reason="Wrong prop", expected_revision="scene-1")
        self.assertEqual(self.store.scene(), before)
        with self.assertRaises(Conflict):
            self.store.publish_scene({"id": "scene-2", "entities": []}, expected_revision=None, actor="engineering-test")
        self.assertEqual(self.store.snapshot()["production"]["revision_id"], "scene-1")
        self.assertFalse((Path(self.tmp.name) / "current.json").exists())
        original = self.store._event
        def crash(db, kind, *args, **kwargs):
            if kind == "revision.accepted":
                raise RuntimeError("crash before scene commit")
            return original(db, kind, *args, **kwargs)
        with patch.object(self.store, "_event", crash):
            with self.assertRaises(RuntimeError):
                self.store.publish_scene({"id": "scene-3", "parent_revision_id": "scene-1"}, expected_revision="scene-1", actor="engineering-test")
        self.assertEqual(self.store.scene(), before)
        with self.assertRaises(NotFound):
            self.store.scene("scene-3")

    def test_artifact_publication_hash_and_orphan_before_db_commit(self):
        artifact = self.store.publish_artifact(b"original", kind="test", mime_type="application/octet-stream")
        self.assertEqual(artifact["sha256"], hashlib.sha256(b"original").hexdigest())
        self.assertEqual(self.store.artifact_path(artifact["id"]).read_bytes(), b"original")
        before = len(self.store.snapshot()["artifacts"])
        original = self.store._event
        def crash(db, kind, *args, **kwargs):
            if kind == "artifact.published":
                raise RuntimeError("after rename before metadata commit")
            return original(db, kind, *args, **kwargs)
        with patch.object(self.store, "_event", crash):
            with self.assertRaises(RuntimeError):
                self.store.publish_artifact("orphan")
        self.assertEqual(len(self.store.snapshot()["artifacts"]), before)
        self.assertTrue((self.store.artifact_dir / hashlib.sha256(b"orphan").hexdigest()).exists())

    def test_path_escape_and_missing_files_are_not_served(self):
        artifact = self.store.publish_artifact("present", mime_type="text/plain")
        self.store.artifact_path(artifact["id"]).unlink()
        with self.assertRaises(NotFound):
            self.store.artifact_path(artifact["id"])
        with sqlite3.connect(self.store.db_path) as db:
            record = dict(artifact, path="../../outside")
            db.execute("UPDATE artifacts SET path=?,data=? WHERE id=?", (record["path"], json.dumps(record), artifact["id"]))
        with self.assertRaises(NotFound):
            self.store.artifact_path(artifact["id"])

    def test_snapshot_watermark_is_consistent_during_concurrent_write(self):
        original = self.store._budget
        wrote = False
        def concurrent_write(db):
            nonlocal wrote
            if not wrote:
                wrote = True
                # WAL allows an independent writer while this read transaction
                # retains the snapshot already established by accepted_head.
                self.store.create_run("concurrent")
            return original(db)
        with patch.object(self.store, "_budget", concurrent_write):
            snapshot = self.store.snapshot()
        self.assertEqual(snapshot["runs"], [])
        self.assertEqual(snapshot["last_seq"], 0)
        backlog = self.store.events(snapshot["last_seq"])
        self.assertEqual(len(backlog), 1)
        self.assertEqual(backlog[0]["type"], "run.created")

    def test_event_cursor_backlog_reconnect_and_scope_validation(self):
        for i in range(7):
            self.store.append_event("test.event", {"i": i})
        first = self.store.events(0, limit=3)
        second = self.store.events(first[-1]["seq"], limit=3)
        tail = self.store.events(second[-1]["seq"])
        self.assertEqual([e["payload"]["i"] for e in first + second + tail], list(range(7)))
        self.assertEqual(len({e["id"] for e in first + second + tail}), 7)
        for invalid in (-1, "2", True):
            with self.assertRaises(ValueError):
                self.store.events(invalid)
        with self.assertRaises(Conflict):
            self.store.events(999)

    def test_read_only_replay_preserves_jobs_budget_events_and_scene(self):
        self.store.create_run("run", mode="fixture")
        self.store.create_task({"id": "task", "run_id": "run", "role_id": "role.test"})
        self.store.update_task("task", "running")
        job_id = self.paid_job(run_id="run", task_id="task")
        self.store.begin_submission(job_id)
        before = self.store.snapshot()
        replay = self.store.replay("run")
        self.assertEqual(replay["mode"], "replay")
        self.assertTrue(replay["events"])
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.get_job(job_id)["status"], "submitting")

    def test_pause_blocks_new_work_but_does_not_prevent_receipts(self):
        self.store.create_run("run")
        self.store.create_task({"id": "a", "run_id": "run"})
        self.store.create_task({"id": "b", "run_id": "run", "dependency_ids": ["a"]})
        with self.assertRaises(Conflict):
            self.store.update_task("b", "running")
        job_id = self.paid_job()
        self.store.begin_submission(job_id)
        self.store.set_paused(True)
        with self.assertRaises(Conflict):
            self.store.update_task("a", "running")
        self.store.finish_job(job_id, cost_usd=1)
        self.assertEqual(self.store.get_job(job_id)["status"], "succeeded")
        self.store.set_paused(False)
        self.store.update_task("a", "running")
        self.store.update_task("a", "completed")
        self.store.update_task("b", "running")

    def test_generic_records_emit_atomic_events_and_prompts_are_immutable(self):
        data = {"id": "p1", "text": "Original prompt"}
        self.store.upsert_record("prompts", data)
        seq = self.store.snapshot()["last_seq"]
        self.store.upsert_record("prompts", data)
        self.assertEqual(self.store.snapshot()["last_seq"], seq)
        with self.assertRaises(Conflict):
            self.store.upsert_record("prompts", {**data, "text": "Changed while rendering"})
        self.store.upsert_record("prompts", {"id": "p2", "parent_id": "p1", "text": "New draft"})
        self.assertEqual(len(self.store.list_records("prompts")), 2)
        with self.assertRaises(ValueError):
            self.store.upsert_record("settings", {"id": "bypass"})

    def test_secrets_and_signed_urls_redacted_under_misleading_keys(self):
        secret = "credential-that-must-never-appear"
        store = ProductionStore(Path(self.tmp.name) / "redaction", secrets=(secret,), opening_entries=())
        artifact = store.publish_artifact({"model": secret, "note": f"Bearer {secret}", "url": "https://host.test/output?signature=private#fragment", "authorization": "even-short"})
        contents = store.artifact_path(artifact["id"]).read_text()
        self.assertNotIn(secret, contents)
        self.assertNotIn("signature", contents)
        self.assertNotIn("even-short", contents)
        store.append_event("error", {"message": secret, "model": secret})
        self.assertNotIn(secret, json.dumps(store.snapshot()))


if __name__ == "__main__":
    unittest.main()
