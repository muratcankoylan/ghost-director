"""Connect approved appearance identities to the exact technical scene proxies."""
from __future__ import annotations

from ghost_director.references import ReferencePin


def _key(pin):
    value = ReferencePin.model_validate(pin)
    return value.asset_id, value.revision_id, value.state


def bind_reference_identities(scene, references, receipt):
    """Return explicit prompt bindings and uncovered-set warnings; never guess by name."""
    groups = {}
    for index, reference in enumerate(references, 1):
        key = _key({field: reference[field] for field in ("asset_id", "revision_id", "state")})
        groups.setdefault(key, {"category": reference["category"], "tags": [], "name": reference["name"]})["tags"].append(f"@Image{index}")
    entities = [entity for entity in scene.entities if entity.visible and entity.kind != "light" and not entity.state.get("included_in_entity_id")]
    technical = {row["entity_id"]: row for row in receipt.get("entity_mapping", [])}
    bindings, covered, warnings = [], set(), []
    for entity in entities:
        raw = entity.state.get("reference_pin")
        key = _key(raw) if raw else None
        selected = groups.get(key)
        if entity.kind == "actor" and selected is None:
            raise ValueError(f"Visible character {entity.name} needs its exact bound reference revision and state in the shot")
        if selected is None:
            warnings.append({"entity_id": entity.id, "name": entity.name, "kind": entity.kind,
                             "reason": "No approved appearance reference is selected for this scene entity; the clay proxy specifies only layout and blocking."})
            continue
        required = "character" if entity.kind == "actor" else "location" if entity.kind == "room" else "prop"
        if selected["category"] != required:
            raise ValueError(f"Reference category does not match bound scene entity: {entity.id}")
        row = technical.get(entity.id)
        if entity.kind != "room":
            if row is None or row.get("kind") != entity.kind:
                raise ValueError(f"Technical motion receipt is missing the bound entity: {entity.id}")
            if not row.get("reference_pin") or _key(row["reference_pin"]) != key:
                raise ValueError(f"Technical proxy was rendered with a different identity binding: {entity.id}")
            color = row.get("technical_color")
            if not isinstance(color, list) or len(color) != 4 or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in color):
                raise ValueError(f"Technical proxy color is missing or invalid: {entity.id}")
        covered.add(key)
        bindings.append({"entity_id": entity.id, "name": entity.name, "kind": entity.kind, "asset_id": key[0],
                         "revision_id": key[1], "state": key[2], "reference_tags": selected["tags"],
                         "technical_color": row["technical_color"] if row else None,
                         "appearance_role": "room appearance guidance; geometry remains the authored scene" if entity.kind == "room" else "replace this technical proxy's appearance with the approved references"})
    for key, selected in groups.items():
        if key not in covered:
            role = "room" if selected["category"] == "location" else "visible scene entity"
            raise ValueError(f"Selected reference {selected['name']} is not bound to a {role} at this exact revision and state")
    return bindings, warnings
