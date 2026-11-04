"""Portable reference-workflow lineage, without jobs or executable recovery intents."""
from __future__ import annotations

from ghost_director.references import ReferenceAsset, ReferenceDecision, ReferenceVersion
from ghost_director.reference_reconstruction import ReconstructionReview


def collect_reference_lineage(records, scene, shots, packages, selected_runs):
    reconstruction_ids = {entity.get("state", {}).get("reconstruction_id") for entity in scene["entities"]}
    pinned_reviews = {entity.get("state", {}).get("reconstruction_review_id") for entity in scene["entities"]} - {None}
    all_reviews = {item["id"]: item for item in records.get("reconstruction_reviews", [])}
    if not pinned_reviews.issubset(all_reviews):
        raise ValueError("Selected scene has a missing reconstruction review.")
    reconstruction_ids.update(all_reviews[identifier]["reconstruction_id"] for identifier in pinned_reviews)
    reconstructions = [item for item in records.get("reconstructions", []) if item["id"] in reconstruction_ids or item.get("run_id") in selected_runs]
    selected_mesh_ids = {item["id"] for item in reconstructions}
    reviews = [item for item in records.get("reconstruction_reviews", []) if item["reconstruction_id"] in selected_mesh_ids]
    pins = [entity.get("state", {}).get("reference_pin") for entity in scene["entities"]]
    pins += [pin for item in [*shots, *packages] for pin in item.get("reference_pins", [])]
    pins += [item["reference_pin"] for item in [*reconstructions, *reviews]]
    pins = [pin for pin in pins if pin]
    required_assets = {pin["asset_id"] for pin in pins}
    required_versions = {pin["revision_id"] for pin in pins}
    assets = {item["id"]: item for item in records.get("reference_assets", [])}
    versions = {item["id"]: item for item in records.get("reference_versions", [])}
    if not required_assets.issubset(assets) or not required_versions.issubset(versions):
        raise ValueError("Selected scene/shot references have missing registry identities or versions.")
    # Keep just selected versions and their ancestors. A newer unselected draft
    # must not enter a public bundle merely because an older version was used.
    pending = list(required_versions)
    while pending:
        version = versions[pending.pop()]
        parent = version.get("parent_revision_id")
        if parent and parent not in required_versions:
            if parent not in versions:
                raise ValueError("Reference version history is incomplete.")
            required_versions.add(parent)
            pending.append(parent)
    exported_versions = [value for value in versions.values() if value["id"] in required_versions]
    decisions = [item for item in records.get("reference_decisions", []) if item["revision_id"] in required_versions]
    exported_assets = []
    for asset_id in sorted(required_assets):
        chosen = max((version for version in exported_versions if version["asset_id"] == asset_id), key=lambda value: value["version"])
        approvals = {value["revision_id"]: value for value in decisions if value["asset_id"] == asset_id}
        approved = [value for value in exported_versions if value["asset_id"] == asset_id and approvals.get(value["id"], {}).get("decision") == "approved"]
        head = dict(assets[asset_id])
        head.update(revision_id=chosen["id"], version=chosen["version"], approval_status=approvals.get(chosen["id"], {}).get("decision", "draft"),
                    approved_revision_id=max(approved, key=lambda value: value["version"])["id"] if approved else None)
        from ghost_director.references import ReferenceRegistry
        head["missing_slots"] = ReferenceRegistry._missing(head["category"], chosen["references"])
        exported_assets.append(head)
    package_receipts = {package.get("motion_receipt_id") for package in packages}
    shot_ids = {shot["id"] for shot in shots}
    previews = [item for item in records.get("motion_previews", [])
                if item.get("receipt_id") in package_receipts or item.get("run_id") in selected_runs
                or (item.get("shot_id") in shot_ids and item.get("scene_revision_id") == scene["id"])]
    artifacts = set()
    for version in exported_versions:
        for reference in version["references"]:
            artifacts.update((reference["artifact_id"], reference["original_artifact_id"]))
            provenance = reference.get("provenance", {})
            if provenance.get("prompt_ref"):
                artifacts.add(provenance["prompt_ref"])
            artifacts.update(provenance.get("parent_reference_ids", []))
    for item in previews:
        artifacts.update(item[key] for key in ("video_id", "blend_id", "poster_id", "receipt_id") if item.get(key))
    for item in reconstructions:
        artifacts.update(item[key] for key in ("artifact_id", "receipt_id", "source_image_id", "source_model_id") if item.get(key))
    for package in packages:
        artifacts.update(package[key] for key in ("motion_video_id", "motion_receipt_id") if package.get(key))
    return {"reference_assets": exported_assets, "reference_versions": exported_versions, "reference_decisions": decisions,
            "motion_previews": previews, "reconstructions": reconstructions, "reconstruction_reviews": reviews}, artifacts


def validate_reference_lineage(creative, artifacts):
    artifact_ids = set(artifacts)
    assets, versions = {}, {}
    for item in creative.get("reference_assets", []):
        record = ReferenceAsset.model_validate(item)
        if record.id in assets:
            raise ValueError("Duplicate reference asset identity.")
        assets[record.id] = record
    for item in creative.get("reference_versions", []):
        record = ReferenceVersion.model_validate(item)
        if record.id in versions or record.asset_id not in assets:
            raise ValueError("Duplicate or orphan reference version.")
        versions[record.id] = record
        for reference in record.references:
            refs = {reference.artifact_id, reference.original_artifact_id, *reference.provenance.parent_reference_ids}
            if reference.provenance.prompt_ref:
                refs.add(reference.provenance.prompt_ref)
            if not refs.issubset(artifact_ids):
                raise ValueError("Reference version is missing an image or provenance artifact.")
            image = artifacts[reference.artifact_id]
            if image["sha256"] != reference.sha256 or image["kind"] != "image" or image["mime_type"] != reference.mime_type:
                raise ValueError("Reference image content hash or media type differs from its immutable identity.")
    for record in assets.values():
        if record.revision_id not in versions or versions[record.revision_id].asset_id != record.id:
            raise ValueError("Reference asset head is missing or belongs to another identity.")
        if record.approved_revision_id and (record.approved_revision_id not in versions or versions[record.approved_revision_id].asset_id != record.id):
            raise ValueError("Approved reference head is missing or mismatched.")
    for record in versions.values():
        parent = record.parent_revision_id
        if parent and (parent not in versions or versions[parent].asset_id != record.asset_id or versions[parent].version >= record.version):
            raise ValueError("Reference version ancestry is missing, cyclic, or mismatched.")
    decisions = []
    for item in creative.get("reference_decisions", []):
        decision = ReferenceDecision.model_validate(item)
        if decision.revision_id not in versions or versions[decision.revision_id].asset_id != decision.asset_id:
            raise ValueError("Reference decision points to a missing or mismatched version.")
        decisions.append(decision)
    if len({item.revision_id for item in decisions}) != len(decisions):
        raise ValueError("A reference version has conflicting decisions.")
    for asset in assets.values():
        if asset.approved_revision_id and not any(item.revision_id == asset.approved_revision_id and item.decision == "approved" for item in decisions):
            raise ValueError("Reference approval lacks an attributed decision.")
    pins = [entity.get("state", {}).get("reference_pin") for entity in creative["scene"]["entities"]]
    pins += [pin for item in [*creative.get("shots", []), *creative.get("packages", [])] for pin in item.get("reference_pins", [])]
    for pin in filter(None, pins):
        if pin["asset_id"] not in assets or pin["revision_id"] not in versions or versions[pin["revision_id"]].asset_id != pin["asset_id"]:
            raise ValueError("Creative reference pin has missing identity lineage.")
    for item in creative.get("motion_previews", []):
        if any(item.get(key) not in artifact_ids for key in ("video_id", "blend_id", "poster_id", "receipt_id")):
            raise ValueError("Motion preview is missing its artifacts.")
    reconstructions = {}
    for item in creative.get("reconstructions", []):
        if item["id"] in reconstructions:
            raise ValueError("Duplicate reconstruction identity.")
        reconstructions[item["id"]] = item
        if any(item.get(key) not in artifact_ids for key in ("artifact_id", "receipt_id", "source_image_id", "source_model_id")):
            raise ValueError("Reconstruction is missing its artifacts.")
        pin = item.get("reference_pin", {})
        version = versions.get(pin.get("revision_id"))
        if version is None or version.asset_id != pin.get("asset_id") or not any(
                decision.revision_id == version.id and decision.decision == "approved" for decision in decisions):
            raise ValueError("Reconstruction lacks its approved reference identity.")
        if not any(reference.artifact_id == item["source_image_id"] and reference.state == pin.get("state", "default") for reference in version.references):
            raise ValueError("Reconstruction source image is outside its approved reference pin.")
    reviews = {}
    for item in creative.get("reconstruction_reviews", []):
        review = ReconstructionReview.model_validate(item)
        if review.id in reviews or review.reconstruction_id not in reconstructions:
            raise ValueError("Duplicate or orphan reconstruction review.")
        reviews[review.id] = review
        reconstruction = reconstructions[review.reconstruction_id]
        model = artifacts.get(review.artifact_id)
        if (review.artifact_id != reconstruction["artifact_id"] or model is None or model["kind"] != "model"
                or review.artifact_sha256 != model["sha256"]
                or review.reference_pin.model_dump(mode="json") != reconstruction["reference_pin"]):
            raise ValueError("Reconstruction review does not match its exact mesh, hash and approved reference pin.")
    for entity in creative["scene"]["entities"]:
        state = entity.get("state", {})
        review_id = state.get("reconstruction_review_id")
        if review_id:
            review = reviews.get(review_id)
            if (review is None or review.decision != "accepted" or review.reconstruction_id != state.get("reconstruction_id")
                    or review.artifact_id != state.get("artifact_id") or review.reference_pin.model_dump(mode="json") != state.get("reference_pin")):
                raise ValueError("Placed entity has a missing or mismatched accepted reconstruction review.")


def import_reference_registry(store, creative, rebind):
    """Restore attributed historical decisions, never synthesize a new approval."""
    from ghost_director.references import ReferenceRegistry
    registry = ReferenceRegistry(store)
    with store._transaction() as db:
        for collection, model in (("reference_versions", ReferenceVersion), ("reference_decisions", ReferenceDecision), ("reference_assets", ReferenceAsset)):
            for original in creative.get(collection, []):
                record = rebind(original)
                if collection == "reference_versions":
                    for reference in record["references"]:
                        artifact = store.artifact(reference["artifact_id"])
                        reference["sha256"] = artifact["sha256"]
                elif collection == "reference_decisions":
                    record["actor"] = ("Imported historical record: " + original["actor"])[:120]
                registry._write(db, collection, model.model_validate(record).model_dump(mode="json"))
        store._event(db, "demo.references_imported", {"asset_count": len(creative.get("reference_assets", [])), "evidence_origin": "imported historical reference decisions", "provider_calls_executed": 0})
