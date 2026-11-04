"""Read-only projection of durable facts at a run's recorded endpoint."""

from copy import deepcopy


COLLECTIONS = (
    "runs", "tasks", "jobs", "artifacts", "shots", "takes", "observations",
    "handoffs", "models", "prompts", "packages", "calls", "reference_assets",
    "reference_versions", "reference_decisions", "motion_previews", "reconstructions", "reconstruction_reviews",
)
RECORD_COLLECTIONS = frozenset(COLLECTIONS[4:])


def project_snapshot(production_id: str, run_id: str, events: list[dict], endpoint: dict, *, completed: bool) -> dict:
    rows = {name: {} for name in COLLECTIONS}
    head = None
    paused = False
    for event in events:
        kind, payload = event["type"], deepcopy(event["payload"])
        collection = event.get("record_collection")
        if collection is None:
            prefix = kind.partition(".")[0]
            if prefix in {"run", "task", "job"}:
                collection = prefix + "s"
            elif kind == "artifact.published":
                collection = "artifacts"
            elif kind.endswith(".updated") and prefix in RECORD_COLLECTIONS:
                # Original ledgers used the default collection event names.
                collection = prefix
        if collection in rows and payload.get("id"):
            payload.pop("budget_snapshot", None)
            rows[collection][payload["id"]] = payload
        if kind == "revision.accepted":
            head = payload["revision_id"]
        elif kind in {"dispatch.paused", "dispatch.resumed"}:
            paused = payload["paused"]
        elif kind in {"take.accepted", "take.rejected"} and payload.get("take_id") in rows["takes"]:
            rows["takes"][payload["take_id"]]["status"] = payload["disposition"]

    budget = deepcopy(endpoint["payload"].get("budget_snapshot"))
    if budget is None:
        budget = {"currency": "USD", "status": "historical_unavailable"}
    budget.update(historical=True, as_of_seq=endpoint["seq"])
    return {
        "production": {"id": production_id, "name": production_id.replace("-", " ").title(), "revision_id": head, "paused": paused,
                       "mode": "replay", "replay_run_id": run_id, "replay_complete": completed},
        "scene": None,
        **{name: list(records.values()) for name, records in rows.items()},
        "events": events[-200:], "budget": budget, "last_seq": endpoint["seq"],
    }
