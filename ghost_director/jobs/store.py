"""SQLite operational ledger with immutable creative artifacts.

Every write is a short transaction. Provider calls never execute inside these
transactions. One database belongs to one production and owns its accepted-head
reference; JSON files contain the actual immutable creative state.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterator
from uuid import uuid4

from ghost_director.runtime.redaction import Redactor


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    pass


class NotFound(StoreError):
    pass


class InvalidTransition(StoreError):
    pass


class BudgetExceeded(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def microdollars(value: float | str | Decimal) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Cost must be finite and nonnegative.")
    return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


DEFAULT_ALLOCATIONS = {"meta": 150, "video": 60, "assets_3d": 40, "contingency": 50}
OPENING_ENTRIES = (
    {"id": "opening-meta-text", "category": "meta", "amount_usd": "0.000992", "basis": "estimated", "source": "meta-api-test.json"},
    {"id": "opening-meta-media", "category": "meta", "amount_usd": "0.027016", "basis": "estimated", "source": "api-preflight/results.json"},
    {"id": "opening-openrouter", "category": "video", "amount_usd": "0.415481", "basis": "reported", "source": "api-preflight/results.json"},
    {"id": "opening-fal", "category": "assets_3d", "amount_usd": "0.04", "basis": "estimated", "source": "api-preflight/results.json"},
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 type TEXT NOT NULL, recorded_at TEXT NOT NULL, run_id TEXT, task_id TEXT,
 job_id TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
CREATE INDEX IF NOT EXISTS events_type ON events(type,seq);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), task_id TEXT REFERENCES tasks(id),
 root_id TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT,
 data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), category TEXT NOT NULL,
 amount_micro INTEGER NOT NULL CHECK(amount_micro >= 0), state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS charges (
 id TEXT PRIMARY KEY, job_id TEXT UNIQUE REFERENCES jobs(id), category TEXT NOT NULL,
 amount_micro INTEGER NOT NULL CHECK(amount_micro >= 0), basis TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (root_id TEXT PRIMARY KEY, count INTEGER NOT NULL CHECK(count BETWEEN 0 AND 1));
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, path TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scenes (id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), parent_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositions (id TEXT PRIMARY KEY, take_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (collection TEXT NOT NULL, id TEXT NOT NULL, run_id TEXT, data TEXT NOT NULL, PRIMARY KEY(collection,id));
"""


class ProductionStore:
    def __init__(
        self, root: str | Path, production_id: str = "ghost-director-hackathon", *,
        ceiling_usd: float = 300, allocations: dict | None = None,
        opening_entries: tuple[dict, ...] = OPENING_ENTRIES, secrets: tuple[str, ...] = (),
    ):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = self.root / "artifacts"
        self.artifact_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "runtime.sqlite3"
        self.production_id = production_id
        self.redactor = Redactor(secrets)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        with self._transaction() as db:
            existing = self._setting(db, "production_id")
            if existing is not None and existing != production_id:
                raise Conflict("Database belongs to a different production.")
            for key, value in {
                "production_id": production_id,
                "ceiling_micro": microdollars(ceiling_usd),
                "allocations_micro": {k: microdollars(v) for k, v in (allocations or DEFAULT_ALLOCATIONS).items()},
                "accepted_head": None,
                "paused": False,
            }.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, encode(value)))
            for entry in opening_entries:
                db.execute(
                    "INSERT OR IGNORE INTO charges VALUES (?,NULL,?,?,?,?)",
                    (entry["id"], entry["category"], microdollars(entry["amount_usd"]), entry["basis"], entry["source"]),
                )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _setting(db, key):
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _record(db, table, record_id):
        # table is always an internal literal, never caller-controlled SQL.
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"{table} record not found: {record_id}")
        return row

    def _event(self, db, event_type: str, payload: dict, *, run_id=None, task_id=None, job_id=None, record_collection=None):
        event = {
            "schema_version": "1.0", "event_id": str(uuid4()), "production_id": self.production_id,
            "type": event_type, "recorded_at": utc_now(), "run_id": run_id,
            "task_id": task_id, "job_id": job_id, "payload": self.redactor.value(payload),
        }
        event["id"] = event["event_id"]
        event["artifact_refs"] = payload.get("artifact_refs", [])
        if record_collection:
            event["record_collection"] = record_collection
        cursor = db.execute(
            "INSERT INTO events(event_id,type,recorded_at,run_id,task_id,job_id,payload) VALUES (?,?,?,?,?,?,?)",
            (event["event_id"], event_type, event["recorded_at"], run_id, task_id, job_id, encode(event)),
        )
        event["seq"] = cursor.lastrowid
        return event

    def append_event(self, event_type: str, payload: dict, *, run_id=None, task_id=None, job_id=None) -> dict:
        """Record actual handoffs/observations; state changes use dedicated methods."""
        with self._transaction() as db:
            return self._event(db, event_type, payload, run_id=run_id, task_id=task_id, job_id=job_id)

    def create_run(self, run_id: str | None = None, *, mode="live", metadata: dict | None = None) -> dict:
        if mode not in {"live", "fixture"}:
            raise ValueError("Run mode must be live or fixture; replay is a read mode.")
        data = self.redactor.value({**(metadata or {}), "id": run_id or str(uuid4()), "status": "created", "mode": mode, "created_at": utc_now()})
        with self._transaction() as db:
            data["initial_scene_revision"] = self._setting(db, "accepted_head")
            data["initial_seq"] = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            db.execute("INSERT INTO runs VALUES (?,?,?)", (data["id"], data["status"], encode(data)))
            self._event(db, "run.created", data, run_id=data["id"])
        return data

    def create_task(self, task: dict) -> dict:
        data = self.redactor.value(dict(task))
        data.setdefault("id", str(uuid4()))
        data.setdefault("status", "pending")
        data.setdefault("attempt", 1)
        data.setdefault("created_at", utc_now())
        data["owner_pid"] = os.getpid()
        with self._transaction() as db:
            self._record(db, "runs", data["run_id"])
            for dependency in data.get("dependency_ids", []):
                parent = self._record(db, "tasks", dependency)
                if parent["run_id"] != data["run_id"]:
                    raise Conflict("Task dependency belongs to another run.")
            db.execute("INSERT INTO tasks VALUES (?,?,?,?)", (data["id"], data["run_id"], data["status"], encode(data)))
            self._event(db, "task.created", data, run_id=data["run_id"], task_id=data["id"])
        return data

    def update_task(self, task_id: str, status: str, *, result: dict | None = None) -> dict:
        transitions = {
            "pending": {"running", "blocked", "cancelled"}, "queued": {"running", "blocked", "cancelled"}, "blocked": {"pending", "cancelled"},
            "running": {"completed", "failed", "cancelled", "interrupted"},
            "interrupted": {"failed", "completed"},
        }
        with self._transaction() as db:
            row = self._record(db, "tasks", task_id)
            if status not in transitions.get(row["status"], set()):
                raise InvalidTransition(f"Task {row['status']} -> {status} is forbidden.")
            data = json.loads(row["data"])
            if status == "running":
                if self._setting(db, "paused"):
                    raise Conflict("Production dispatch is paused.")
                for dependency in data.get("dependency_ids", []):
                    if self._record(db, "tasks", dependency)["status"] != "completed":
                        raise Conflict("Task dependency has not completed.")
            data.update(status=status, updated_at=utc_now())
            if result is not None:
                data["result"] = self.redactor.value(result)
            db.execute("UPDATE tasks SET status=?,data=? WHERE id=?", (status, encode(data), task_id))
            self._event(db, "task." + status, data, run_id=row["run_id"], task_id=task_id)
            return data

    def finish_run(self, run_id: str, status: str = "completed") -> dict:
        if status not in {"completed", "failed", "awaiting_acceptance", "cancelled"}:
            raise ValueError("Invalid run status.")
        with self._transaction() as db:
            row = self._record(db, "runs", run_id)
            data = json.loads(row["data"])
            data.update(status=status, updated_at=utc_now())
            db.execute("UPDATE runs SET status=?,data=? WHERE id=?", (status, encode(data), run_id))
            self._event(db, "run." + status, {**data, "budget_snapshot": self._budget(db)}, run_id=run_id)
            return data

    def submit_command(self, command: dict) -> dict:
        """Claim a command exactly once. Caller dispatches only a new receipt."""
        command_id = command.get("id") or command.get("command_id")
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("A nonempty command UUID is required.")
        if command.get("production_id", self.production_id) != self.production_id:
            raise Conflict("Wrong production.")
        if command.get("mode") == "replay":
            raise Conflict("Replay cannot submit commands.")
        fingerprint = hashlib.sha256(encode(command).encode()).hexdigest()
        with self._transaction() as db:
            prior = db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise Conflict("Command ID was already used with different content.")
                return {**json.loads(prior["receipt"]), "duplicate": True}
            head = self._setting(db, "accepted_head")
            if "expected_revision" in command and command["expected_revision"] != head:
                raise Conflict("The accepted scene revision changed.")
            receipt = {"command_id": command_id, "status": "accepted", "accepted_at": utc_now(), "operation": command.get("operation"), "expected_revision": head, "owner_pid": os.getpid()}
            db.execute("INSERT INTO commands VALUES (?,?,?)", (command_id, fingerprint, encode(receipt)))
            self._event(db, "command.accepted", {"command": command, "receipt": receipt})
            return {**receipt, "duplicate": False}

    def create_job(self, job: dict, *, automatic_repair: bool = False) -> dict:
        if any(key in job for key in ("dispatch_retired", "dispatch_retirement")):
            raise ValueError("Dispatch retirement requires an explicit decision after an unknown submission.")
        data = self.redactor.value(dict(job))
        data.setdefault("id", str(uuid4()))
        data.setdefault("root_id", data["id"])
        data.update(status="prepared", remote_id=None, created_at=utc_now(), owner_pid=os.getpid())
        with self._transaction() as db:
            if data.get("parent_job_id"):
                parent = self._record(db, "jobs", data["parent_job_id"])
                if "root_id" in job and data["root_id"] != parent["root_id"]:
                    raise Conflict("Repair child cannot change its originating request.")
                data["root_id"] = parent["root_id"]
            if automatic_repair:
                count = db.execute("SELECT count FROM repairs WHERE root_id=?", (data["root_id"],)).fetchone()
                if count and count[0] >= 1:
                    raise Conflict("This originating shot/request already used its automatic repair.")
                db.execute("INSERT INTO repairs VALUES (?,1) ON CONFLICT(root_id) DO UPDATE SET count=1", (data["root_id"],))
                data["automatic_repair"] = True
            if data.get("task_id"):
                task = self._record(db, "tasks", data["task_id"])
                if data.get("run_id") != task["run_id"]:
                    raise Conflict("Job/task run mismatch.")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (data["id"], data.get("run_id"), data.get("task_id"), data["root_id"], data["status"], None, encode(data)))
            self._event(db, "job.created", data, run_id=data.get("run_id"), task_id=data.get("task_id"), job_id=data["id"])
            return data

    def complete_command(self, command_id: str, *, result: dict | None = None, error: str | None = None) -> dict:
        with self._transaction() as db:
            row = self._record(db, "commands", command_id)
            receipt = json.loads(row["receipt"])
            receipt.update(status="failed" if error else "completed", completed_at=utc_now())
            if result is not None:
                receipt["result"] = self.redactor.value(result)
            if error:
                receipt["error"] = self.redactor.text(error)
            db.execute("UPDATE commands SET receipt=? WHERE id=?", (encode(receipt), command_id))
            self._event(db, "command." + receipt["status"], receipt)
            return receipt

    def command_receipt(self, command_id: str) -> dict:
        with self._transaction(write=False) as db:
            return json.loads(self._record(db, "commands", command_id)["receipt"])

    def _budget(self, db) -> dict:
        allocations = self._setting(db, "allocations_micro")
        categories = {}
        for category, cap in allocations.items():
            charges = db.execute("SELECT basis,COALESCE(SUM(amount_micro),0) amount FROM charges WHERE category=? GROUP BY basis", (category,)).fetchall()
            by_basis = {row["basis"]: row["amount"] for row in charges}
            held = db.execute("SELECT COALESCE(SUM(amount_micro),0) FROM reservations WHERE category=? AND state='held'", (category,)).fetchone()[0]
            spent = sum(by_basis.values())
            categories[category] = {"ceiling_usd": cap / 1e6, "reported_usd": by_basis.get("reported", 0) / 1e6, "estimated_usd": by_basis.get("estimated", 0) / 1e6, "reserved_usd": held / 1e6, "committed_micro": spent + held, "remaining_usd": (cap - spent - held) / 1e6}
        committed = sum(row["committed_micro"] for row in categories.values())
        cap = self._setting(db, "ceiling_micro")
        return {"currency": "USD", "ceiling_usd": cap / 1e6, "committed_usd": committed / 1e6, "remaining_usd": (cap - committed) / 1e6, "categories": categories, "over_budget": committed > cap or any(row["remaining_usd"] < 0 for row in categories.values())}

    def budget(self) -> dict:
        with self._transaction(write=False) as db:
            return self._budget(db)

    def reserve(self, job_id: str, category: str, amount_usd) -> dict:
        amount = microdollars(amount_usd)
        if amount <= 0:
            raise ValueError("Paid jobs require a positive conservative reservation.")
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            prior = db.execute("SELECT * FROM reservations WHERE job_id=?", (job_id,)).fetchone()
            if prior:
                if prior["category"] != category or prior["amount_micro"] != amount:
                    raise Conflict("Reservation is immutable once created.")
                return dict(prior)
            if row["status"] != "prepared":
                raise InvalidTransition("Only prepared jobs can reserve funds.")
            budget = self._budget(db)
            caps = self._setting(db, "allocations_micro")
            if category not in caps:
                raise ValueError("Unknown budget category.")
            total = sum(x["committed_micro"] for x in budget["categories"].values())
            if budget["over_budget"] or total + amount > self._setting(db, "ceiling_micro") or budget["categories"][category]["committed_micro"] + amount > caps[category]:
                raise BudgetExceeded("Reservation would exceed the total or category allocation.")
            db.execute("INSERT INTO reservations VALUES (?,?,?,'held')", (job_id, category, amount))
            data = json.loads(row["data"])
            data.update(status="reserved", category=category, reserved_usd=amount / 1e6)
            self._update_job(db, data)
            self._event(db, "budget.reserved", {"job_id": job_id, "category": category, "amount_usd": amount / 1e6}, run_id=row["run_id"], job_id=job_id)
            return {"job_id": job_id, "category": category, "amount_micro": amount, "state": "held"}

    def _update_job(self, db, data: dict):
        data = self.redactor.value(data)
        data["updated_at"] = utc_now()
        db.execute("UPDATE jobs SET status=?,remote_id=?,data=? WHERE id=?", (data["status"], data.get("remote_id"), encode(data), data["id"]))
        self._event(db, "job.status_changed", data, run_id=data.get("run_id"), task_id=data.get("task_id"), job_id=data["id"])

    def begin_submission(self, job_id: str) -> dict:
        """Claim one paid POST. Repeated calls, including after a crash, fail."""
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] != "reserved":
                raise InvalidTransition("Only a reserved, never-submitted job may start submission.")
            if self._setting(db, "paused"):
                raise Conflict("Production dispatch is paused.")
            data = json.loads(row["data"])
            if data.get("category") == "video":
                active = db.execute("SELECT data FROM jobs WHERE id<>? AND status IN ('submitting','queued','running','submission_unknown','cancel_requested')", (job_id,)).fetchall()
                active_data = [json.loads(x["data"]) for x in active]
                if any(x.get("category") == "video" and not (x["status"] == "submission_unknown" and x.get("dispatch_retired") is True) for x in active_data):
                    raise Conflict("A video job is already active or has an uncertain outcome.")
            data.update(status="submitting", intent_id=str(uuid4()), submitted_at=utc_now())
            self._update_job(db, data)
            return data

    def record_submission(self, job_id: str, remote_id: str, *, polling_locator: str | None = None) -> dict:
        if not remote_id:
            raise ValueError("Remote job ID is required.")
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            data = json.loads(row["data"])
            if row["status"] not in {"submitting", "submission_unknown"}:
                if row["remote_id"] == remote_id and row["status"] in {"queued", "running"}:
                    return data
                raise InvalidTransition("Job is not awaiting a submission receipt.")
            data.update(status="queued", remote_id=remote_id)
            if data.get("dispatch_retired"):
                # A recovered receipt makes this a known active job again. The
                # original retirement decision remains in the audit history.
                data["dispatch_retired"] = False
            # The provider reconstructs authenticated polling from its durable
            # ID; signed URLs must not become replay/export content.
            if polling_locator:
                data["polling_locator"] = self.redactor.text(polling_locator)
            self._update_job(db, data)
            return data

    def mark_submission_unknown(self, job_id: str, *, error: str = "Remote outcome is unknown.") -> dict:
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] not in {"submitting", "submission_unknown"}:
                raise InvalidTransition("Only an interrupted submission can become unknown.")
            data = json.loads(row["data"])
            data.update(status="submission_unknown", error=self.redactor.text(error))
            self._update_job(db, data)
            return data

    def retire_unknown(self, job_id: str, *, actor: str, reason: str) -> dict:
        """Explicitly exempt an unknown job from dispatch concurrency, retaining its full hold.

        This is an operator adjudication, not evidence of provider failure or
        cancellation. It cannot retry the job, release funds, or run during
        automatic recovery. A later independent submission can overlap remotely.
        """
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("A nonempty decision actor and reason are required.")
        actor, reason = self.redactor.text(actor.strip()), self.redactor.text(reason.strip())
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] != "submission_unknown":
                raise InvalidTransition("Only an unknown submission can be retired from dispatch concurrency.")
            data = json.loads(row["data"])
            previous = data.get("dispatch_retirement")
            if previous:
                if previous["actor"] != actor or previous["reason"] != reason:
                    raise Conflict("Dispatch retirement decision is immutable.")
                return data
            reservation = db.execute("SELECT * FROM reservations WHERE job_id=?", (job_id,)).fetchone()
            if not reservation or reservation["state"] != "held":
                raise InvalidTransition("An unknown submission must retain its reservation.")
            data.update(dispatch_retired=True, dispatch_retirement={
                "actor": actor, "reason": reason, "recorded_at": utc_now(),
                "held_usd": reservation["amount_micro"] / 1e6,
                "potential_remote_overlap": True,
            })
            self._update_job(db, data)
            self._event(db, "job.dispatch_retired", data, run_id=data.get("run_id"), task_id=data.get("task_id"), job_id=job_id)
            return data

    def set_job_running(self, job_id: str) -> dict:
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] not in {"queued", "running", "cancel_requested"}:
                raise InvalidTransition("Job has no confirmed queued submission.")
            data = json.loads(row["data"])
            if row["status"] == "cancel_requested":
                # A cancel request is not a provider receipt. Continue polling
                # without clearing operator intent or releasing the budget hold.
                return data
            if row["status"] != "running":
                data["status"] = "running"
                self._update_job(db, data)
            return data

    def request_cancel(self, job_id: str) -> dict:
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            data = json.loads(row["data"])
            if row["status"] in {"prepared", "reserved"}:
                data["status"] = "cancelled"
                db.execute("UPDATE reservations SET state='released' WHERE job_id=?", (job_id,))
            elif row["status"] in {"queued", "running", "submitting", "submission_unknown"}:
                data["status_before_cancel"] = row["status"]
                data["status"] = "cancel_requested"
            elif row["status"] != "cancel_requested":
                raise InvalidTransition("Terminal job cannot be cancelled.")
            self._update_job(db, data)
            return data

    def finish_job(self, job_id: str, status: str = "succeeded", *, result_refs: list[str] | None = None, cost_usd=None, cost_basis: str = "reported", receipt: dict | None = None, error: str | None = None) -> dict:
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("Invalid terminal job state.")
        if cost_basis not in {"reported", "estimated"}:
            raise ValueError("Cost basis must be reported or estimated.")
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] not in {"submitting", "queued", "running", "submission_unknown", "cancel_requested"}:
                raise InvalidTransition("Job is not awaiting a remote outcome.")
            data = json.loads(row["data"])
            for artifact_id in result_refs or []:
                self._record(db, "artifacts", artifact_id)
            data.update(status=status, result_refs=result_refs or [], completed_at=utc_now())
            if receipt is not None:
                data["receipt"] = self.redactor.value(receipt)
            if error:
                data["error"] = self.redactor.text(error)
            # Unknown billing never releases a hold. A later authoritative
            # receipt can reconcile the charge without changing creative state.
            if cost_usd is not None:
                self._reconcile(db, job_id, cost_usd, cost_basis)
                data.update(cost_usd=microdollars(cost_usd) / 1e6, cost_basis=cost_basis)
            else:
                data["cost_basis"] = "unknown"
            self._update_job(db, data)
            return data

    def _reconcile(self, db, job_id: str, cost_usd, basis: str):
        reservation = db.execute("SELECT * FROM reservations WHERE job_id=?", (job_id,)).fetchone()
        if not reservation:
            raise Conflict("Cannot reconcile an unreserved paid job.")
        old = db.execute("SELECT basis FROM charges WHERE job_id=?", (job_id,)).fetchone()
        if old and old[0] == "reported" and basis != "reported":
            raise Conflict("An estimate cannot replace a reported charge.")
        amount = microdollars(cost_usd)
        db.execute("INSERT INTO charges VALUES (?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET amount_micro=excluded.amount_micro,basis=excluded.basis,source=excluded.source", ("charge-" + job_id, job_id, reservation["category"], amount, basis, "provider-receipt"))
        db.execute("UPDATE reservations SET state='reconciled' WHERE job_id=?", (job_id,))
        self._event(db, "budget.reconciled", {"job_id": job_id, "amount_usd": amount / 1e6, "basis": basis}, job_id=job_id)

    def reconcile(self, job_id: str, cost_usd, *, basis="reported") -> dict:
        if basis not in {"reported", "estimated"}:
            raise ValueError("Invalid cost basis.")
        with self._transaction() as db:
            row = self._record(db, "jobs", job_id)
            if row["status"] not in {"succeeded", "failed", "cancelled"}:
                raise InvalidTransition("Reconcile only a known terminal outcome.")
            self._reconcile(db, job_id, cost_usd, basis)
            data = json.loads(row["data"])
            data.update(cost_usd=microdollars(cost_usd) / 1e6, cost_basis=basis)
            self._update_job(db, data)
            return data

    def recover(self) -> dict:
        """Run once at process startup, never as a browser read side effect."""
        unknown, resumable, materializable, interrupted_commands, active_owners = [], [], [], [], []

        def owned_elsewhere(data, kind, record_id):
            pid = data.get("owner_pid")
            if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
                return False
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                # A process we cannot signal still exists. A recycled PID can
                # delay recovery, but must never cause an extra paid submission.
                pass
            active_owners.append({"kind": kind, "id": record_id, "owner_pid": pid})
            return True

        with self._transaction() as db:
            for row in db.execute("SELECT * FROM jobs WHERE status IN ('submitting','queued','running','submission_unknown','cancel_requested')").fetchall():
                data = json.loads(row["data"])
                if owned_elsewhere(data, "job", row["id"]):
                    continue
                if row["status"] == "submitting" or (row["status"] == "cancel_requested" and not row["remote_id"]):
                    data.update(status="submission_unknown", error="Process ended before remote outcome was durably confirmed.")
                    self._update_job(db, data)
                    unknown.append(data)
                elif row["status"] == "submission_unknown":
                    unknown.append(data)
                elif row["remote_id"]:
                    resumable.append(data)
            take_jobs = {json.loads(row[0]).get("job_id") for row in db.execute("SELECT data FROM records WHERE collection='takes'")}
            for row in db.execute("SELECT * FROM jobs WHERE status='succeeded'").fetchall():
                data = json.loads(row["data"])
                if data.get("category") == "video" and data.get("result_refs") and data["id"] not in take_jobs:
                    if not owned_elsewhere(data, "job", row["id"]):
                        # The paid job is complete. Rebuild a missing local take
                        # from its registered artifact, never submit it again.
                        materializable.append(data)
            for row in db.execute("SELECT * FROM tasks WHERE status='running'").fetchall():
                data = json.loads(row["data"])
                if owned_elsewhere(data, "task", row["id"]):
                    continue
                data.update(status="interrupted", updated_at=utc_now())
                db.execute("UPDATE tasks SET status='interrupted',data=? WHERE id=?", (encode(data), row["id"]))
                self._event(db, "task.interrupted", data, run_id=row["run_id"], task_id=row["id"])
            for row in db.execute("SELECT * FROM commands").fetchall():
                receipt = json.loads(row["receipt"])
                if receipt["status"] == "accepted":
                    if owned_elsewhere(receipt, "command", row["id"]):
                        continue
                    receipt.update(status="interrupted", error="Application restarted before command completion. Inspect its persisted jobs; the command was not replayed.")
                    db.execute("UPDATE commands SET receipt=? WHERE id=?", (encode(receipt), row["id"]))
                    self._event(db, "command.interrupted", receipt)
                    interrupted_commands.append(receipt)
        return {"submission_unknown": unknown, "resumable_jobs": resumable, "materializable_jobs": materializable, "interrupted_commands": interrupted_commands, "active_owners": active_owners}

    def set_paused(self, paused: bool) -> dict:
        with self._transaction() as db:
            db.execute("UPDATE settings SET value=? WHERE key='paused'", (encode(bool(paused)),))
            return self._event(db, "dispatch.paused" if paused else "dispatch.resumed", {"paused": bool(paused)})

    def publish_artifact(self, data: bytes | str | dict | list | None = None, *, source_path: str | Path | None = None, kind="document", mime_type="application/json", metadata: dict | None = None, run_id=None, task_id=None) -> dict:
        if (data is None) == (source_path is None):
            raise ValueError("Provide exactly one data value or source_path.")
        if source_path is not None:
            content = Path(source_path).read_bytes()
        elif isinstance(data, bytes):
            content = data
        elif isinstance(data, str):
            content = self.redactor.text(data).encode("utf-8")
        else:
            content = encode(self.redactor.value(data)).encode("utf-8")
        if mime_type.startswith("text/") or mime_type == "application/json":
            content = self.redactor.text(content.decode("utf-8")).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        final = self.artifact_dir / digest
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.artifact_dir)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            # Content-addressed path is immutable; identical concurrent writes
            # share bytes, while metadata remains separately attributable.
            if final.exists():
                if hashlib.sha256(final.read_bytes()).hexdigest() != digest:
                    raise Conflict("Existing content-addressed artifact is corrupt.")
                os.unlink(temporary)
            else:
                os.replace(temporary, final)
            artifact_id = "art-" + str(uuid4())
            relative_path = str(final.relative_to(self.root))
            record = self.redactor.value({"id": artifact_id, "kind": kind, "mime_type": mime_type, "sha256": digest, "byte_size": len(content), "bytes": len(content), "name": (metadata or {}).get("name", artifact_id), "path": relative_path, "relative_path": relative_path, "created_at": utc_now(), "run_id": run_id, "task_id": task_id, "metadata": metadata or {}, "url": f"/api/productions/{self.production_id}/artifacts/{artifact_id}/content"})
            with self._transaction() as db:
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (record["id"], digest, record["path"], encode(record)))
                self._event(db, "artifact.published", record, run_id=run_id, task_id=task_id)
            return record
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def artifact(self, artifact_id: str) -> dict:
        with self._transaction(write=False) as db:
            return json.loads(self._record(db, "artifacts", artifact_id)["data"])

    def artifact_path(self, artifact_id: str) -> Path:
        record = self.artifact(artifact_id)
        path = (self.root / record["path"]).resolve()
        if not path.is_relative_to(self.artifact_dir.resolve()) or not path.is_file():
            raise NotFound("Artifact file is missing or outside its production.")
        return path

    def publish_scene(self, scene: dict, *, expected_revision: str | None, actor: str, run_id=None, take_id: str | None = None, decision_reason: str = "Accepted bounded scene patch") -> dict:
        if not actor.strip():
            raise ValueError("An attributed operator is required.")
        revision_id = scene.get("id") or scene.get("revision_id")
        if not revision_id:
            raise ValueError("Scene revision needs an ID.")
        if scene.get("parent_revision_id", scene.get("parent_id", expected_revision)) != expected_revision:
            raise Conflict("Scene parent does not match expected revision.")
        artifact = self.publish_artifact(scene, kind="scene_revision", run_id=run_id)
        with self._transaction() as db:
            if self._setting(db, "accepted_head") != expected_revision:
                raise Conflict("The accepted scene revision changed; artifact remains an unaccepted proposal.")
            db.execute("INSERT INTO scenes VALUES (?,?,?,?)", (revision_id, artifact["id"], expected_revision, utc_now()))
            db.execute("UPDATE settings SET value=? WHERE key='accepted_head'", (encode(revision_id),))
            payload = {"revision_id": revision_id, "parent_revision_id": expected_revision, "artifact_id": artifact["id"], "actor": actor, "take_id": take_id}
            if take_id:
                self._disposition(db, take_id, "accepted", actor, decision_reason, run_id=run_id, scene_revision_id=revision_id)
            self._event(db, "revision.accepted", payload, run_id=run_id)
            return payload

    def _disposition(self, db, take_id, disposition, actor, reason, *, run_id=None, scene_revision_id=None):
        existing = db.execute("SELECT data,run_id FROM records WHERE collection='takes' AND id=?", (take_id,)).fetchone()
        if existing:
            take = json.loads(existing["data"])
            if take.get("status") in {"accepted", "rejected"}:
                raise Conflict("Take already has a creative decision.")
            take["status"] = disposition
            db.execute("UPDATE records SET data=? WHERE collection='takes' AND id=?", (encode(take), take_id))
            self._event(db, "takes.updated", take, run_id=run_id or existing["run_id"], record_collection="takes")
        data = {"id": str(uuid4()), "take_id": take_id, "disposition": disposition, "actor": actor, "reason": reason, "created_at": utc_now(), "scene_revision_id": scene_revision_id}
        data = self.redactor.value(data)
        db.execute("INSERT INTO dispositions VALUES (?,?,?)", (data["id"], take_id, encode(data)))
        self._event(db, "take." + disposition, data, run_id=run_id)
        return data

    def reject_take(self, take_id: str, *, actor: str, reason: str, expected_revision: str | None, run_id=None) -> dict:
        if not actor.strip() or not reason.strip():
            raise ValueError("Rejection requires operator attribution and reason.")
        with self._transaction() as db:
            if self._setting(db, "accepted_head") != expected_revision:
                raise Conflict("The accepted scene revision changed.")
            return self._disposition(db, take_id, "rejected", actor, reason, run_id=run_id, scene_revision_id=expected_revision)

    def scene(self, revision_id: str | None = None) -> dict | None:
        with self._transaction(write=False) as db:
            target = revision_id or self._setting(db, "accepted_head")
            if target is None:
                return None
            row = self._record(db, "scenes", target)
            artifact_id = row["artifact_id"]
        return json.loads(self.artifact_path(artifact_id).read_text(encoding="utf-8"))

    def get_job(self, job_id: str) -> dict:
        with self._transaction(write=False) as db:
            return json.loads(self._record(db, "jobs", job_id)["data"])

    def get_run(self, run_id: str) -> dict:
        with self._transaction(write=False) as db:
            return self._run(db, run_id)

    def _run(self, db, run_id: str) -> dict:
        run = json.loads(self._record(db, "runs", run_id)["data"])
        return {**run, "tasks": [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks WHERE run_id=?", (run_id,))], "jobs": [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs WHERE run_id=?", (run_id,))]}

    RECORD_COLLECTIONS = frozenset({"shots", "takes", "observations", "handoffs", "calls", "models", "prompts", "packages", "reference_assets", "reference_versions", "reference_decisions", "motion_previews", "reconstructions", "reconstruction_reviews"})

    def upsert_record(self, collection: str, record: dict, *, event_type: str | None = None, run_id=None, task_id=None, job_id=None) -> dict:
        """Typed service validates contents; storage validates collection/identity.

        Changes and their observable event are committed together. Immutable
        prompt/media bytes belong in artifacts, not these operational projections.
        """
        if collection not in self.RECORD_COLLECTIONS:
            raise ValueError("Unknown record collection.")
        if collection in {"reference_assets", "reference_versions", "reference_decisions"}:
            raise ValueError("Reference records must be written through the versioned registry.")
        data = self.redactor.value(dict(record))
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("Record requires a nonempty ID.")
        run_id = run_id or data.get("run_id")
        with self._transaction() as db:
            if run_id:
                self._record(db, "runs", run_id)
            previous = db.execute("SELECT data FROM records WHERE collection=? AND id=?", (collection, data["id"])).fetchone()
            if previous and json.loads(previous[0]) == data:
                return data
            # Submitted prompt/package records are immutable. New drafts and
            # repairs need new IDs; their parent links preserve the lineage.
            if previous and collection in {"prompts", "packages", "motion_previews", "reconstructions", "reconstruction_reviews"}:
                raise Conflict("Prompt/package revisions are immutable; create a new ID.")
            db.execute("INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(collection,id) DO UPDATE SET run_id=excluded.run_id,data=excluded.data", (collection, data["id"], run_id, encode(data)))
            self._event(db, event_type or f"{collection}.updated", data, run_id=run_id, task_id=task_id or data.get("task_id"), job_id=job_id or data.get("job_id"), record_collection=collection)
            return data

    def get_record(self, collection: str, record_id: str) -> dict:
        if collection not in self.RECORD_COLLECTIONS:
            raise ValueError("Unknown record collection.")
        with self._transaction(write=False) as db:
            row = db.execute("SELECT data FROM records WHERE collection=? AND id=?", (collection, record_id)).fetchone()
            if row is None:
                raise NotFound(f"{collection} record not found: {record_id}")
            return json.loads(row[0])

    def list_records(self, collection: str, *, run_id: str | None = None) -> list[dict]:
        if collection not in self.RECORD_COLLECTIONS:
            raise ValueError("Unknown record collection.")
        with self._transaction(write=False) as db:
            return self._records(db, collection, run_id=run_id)

    @staticmethod
    def _records(db, collection, *, run_id=None):
        if run_id:
            rows = db.execute("SELECT data FROM records WHERE collection=? AND run_id=? ORDER BY rowid", (collection, run_id))
        else:
            rows = db.execute("SELECT data FROM records WHERE collection=? ORDER BY rowid", (collection,))
        return [json.loads(row[0]) for row in rows]

    def _snapshot(self, db) -> dict:
        head = self._setting(db, "accepted_head")
        scene_ref = dict(self._record(db, "scenes", head)) if head else None
        scene = None
        if scene_ref:
            ref = self._record(db, "artifacts", scene_ref["artifact_id"])
            path = (self.root / ref["path"]).resolve()
            if not path.is_relative_to(self.artifact_dir.resolve()) or not path.is_file():
                raise NotFound("Accepted scene artifact is missing.")
            scene = json.loads(path.read_text(encoding="utf-8"))
        # Decisions are durable creative provenance, not transient activity.
        # Keep their original events beside the bounded tail so attribution does
        # not disappear after 200 unrelated events, including imported evidence.
        recent_events = [{**json.loads(row["payload"]), "seq": row["seq"]} for row in db.execute("""
            SELECT seq,payload FROM events WHERE seq IN
                (SELECT seq FROM events ORDER BY seq DESC LIMIT 200)
            UNION
            SELECT seq,payload FROM events WHERE type IN
                ('take.accepted','take.rejected','demo.disposition_imported')
            ORDER BY seq
        """)]
        undo_source = scene
        seen = set()
        while undo_source and undo_source.get("restored_from_id"):
            restored_id = undo_source["restored_from_id"]
            if restored_id in seen:
                raise Conflict("Scene restoration lineage contains a cycle.")
            seen.add(restored_id)
            restored_ref = self._record(db, "scenes", restored_id)
            artifact_ref = self._record(db, "artifacts", restored_ref["artifact_id"])
            undo_source = json.loads((self.root / artifact_ref["path"]).read_text())
        return {
            "schema_version": "1.0", "production_id": self.production_id,
            "production": {"id": self.production_id, "name": self.production_id.replace("-", " ").title(), "revision_id": head, "paused": self._setting(db, "paused")},
            "scene": scene,
            "undo_target_revision_id": undo_source.get("parent_id") if undo_source else None,
            "commands": [json.loads(row[0]) for row in db.execute("""
                SELECT receipt FROM commands WHERE rowid IN
                    (SELECT rowid FROM commands ORDER BY rowid DESC LIMIT 128)
                    OR json_extract(receipt, '$.status') IN ('accepted', 'running')
                ORDER BY rowid
            """)],
            "scene_revision_id": head, "scene_ref": scene_ref,
            "paused": self._setting(db, "paused"), "budget": self._budget(db),
            "runs": [json.loads(row[0]) for row in db.execute("SELECT data FROM runs")],
            "tasks": [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks")],
            "jobs": [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs")],
            "artifacts": [json.loads(row[0]) for row in db.execute("SELECT data FROM artifacts")],
            "dispositions": [json.loads(row[0]) for row in db.execute("SELECT data FROM dispositions")],
            **{name: self._records(db, name) for name in self.RECORD_COLLECTIONS},
            "events": recent_events,
            "last_seq": db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0],
        }

    def snapshot(self) -> dict:
        with self._transaction(write=False) as db:
            return self._snapshot(db)

    def events(self, after: int = 0, *, limit: int = 200, run_id: str | None = None) -> list[dict]:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("Event cursor must be a nonnegative integer.")
        if not 1 <= limit <= 1000:
            raise ValueError("Event page limit must be between 1 and 1000.")
        with self._transaction(write=False) as db:
            maximum = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            if after > maximum:
                raise Conflict("Event cursor is beyond this production; request a fresh snapshot.")
            if run_id:
                rows = db.execute("SELECT seq,payload FROM events WHERE seq>? AND run_id=? ORDER BY seq LIMIT ?", (after, run_id, limit)).fetchall()
            else:
                rows = db.execute("SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)).fetchall()
            return [{**json.loads(row["payload"]), "seq": row["seq"]} for row in rows]

    def replay(self, run_id: str) -> dict:
        """Pure read: never resumes jobs, invokes providers, or publishes state."""
        with self._transaction(write=False) as db:
            snapshot = self._run_snapshot(db, run_id)
            run = next(row for row in snapshot["runs"] if row["id"] == run_id)
            run = {**run, "tasks": [row for row in snapshot["tasks"] if row.get("run_id") == run_id], "jobs": [row for row in snapshot["jobs"] if row.get("run_id") == run_id]}
            events = [{**json.loads(row["payload"]), "seq": row["seq"]} for row in db.execute("SELECT seq,payload FROM events WHERE run_id=? AND seq<=? ORDER BY seq", (run_id, snapshot["last_seq"]))]
            return {"mode": "replay", "production_id": self.production_id, "run": run, "initial_scene_revision": run.get("initial_scene_revision"), "events": events, "artifacts": snapshot["artifacts"]}

    def _run_snapshot(self, db, run_id: str) -> dict:
        from ghost_director.runtime.replay import project_snapshot

        self._record(db, "runs", run_id)
        run_events = [{**json.loads(row["payload"]), "seq": row["seq"]} for row in db.execute("SELECT seq,payload FROM events WHERE run_id=? ORDER BY seq", (run_id,))]
        terminal = [row for row in run_events if row["type"] in {"run.completed", "run.failed", "run.cancelled", "run.awaiting_acceptance"}]
        endpoint = (terminal or run_events)[-1]
        rows = [{**json.loads(row["payload"]), "seq": row["seq"]} for row in db.execute("SELECT seq,payload FROM events WHERE seq<=? ORDER BY seq", (endpoint["seq"],))]
        snapshot = project_snapshot(self.production_id, run_id, rows, endpoint, completed=bool(terminal))
        head = snapshot["production"]["revision_id"]
        if head is not None:
            scene_ref = self._record(db, "scenes", head)
            artifact = self._record(db, "artifacts", scene_ref["artifact_id"])
            path = (self.root / artifact["path"]).resolve()
            if not path.is_relative_to(self.artifact_dir.resolve()) or not path.is_file():
                raise NotFound("Recorded scene artifact is missing.")
            snapshot["scene"] = json.loads(path.read_text(encoding="utf-8"))
        return snapshot

    def run_snapshot(self, run_id: str) -> dict:
        """Rebuild the recorded endpoint from events, never from live rows."""
        with self._transaction(write=False) as db:
            return self._run_snapshot(db, run_id)
