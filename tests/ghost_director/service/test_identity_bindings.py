import pytest

from ghost_director.domain.models import Entity, SceneRevision
from ghost_director.identity_bindings import bind_reference_identities


PIN = {"asset_id": "CHAR-one", "revision_id": "refv-one", "state": "default"}


def setup():
    scene = SceneRevision(id="scene", entities=[Entity(id="actor-one", name="One", kind="actor", state={"reference_pin": PIN})], cameras=[])
    refs = [{**PIN, "category": "character", "name": "One", "slot": slot} for slot in ("front", "back", "face")]
    receipt = {"entity_mapping": [{"entity_id": "actor-one", "kind": "actor", "technical_color": [.9, .6, .1, 1], "reference_pin": PIN}]}
    return scene, refs, receipt


def test_exact_identity_maps_all_images_and_actual_proxy_color():
    scene, refs, receipt = setup()
    bindings, warnings = bind_reference_identities(scene, refs, receipt)
    assert bindings[0]["reference_tags"] == ["@Image1", "@Image2", "@Image3"]
    assert bindings[0]["entity_id"] == "actor-one"
    assert bindings[0]["technical_color"] == [.9, .6, .1, 1]
    assert not warnings


@pytest.mark.parametrize("change,match", [
    (lambda s, r, p: s.entities[0].state.clear(), "Visible character"),
    (lambda s, r, p: s.entities[0].state.update(reference_pin={**PIN, "revision_id": "refv-other"}), "exact bound"),
    (lambda s, r, p: s.entities[0].state.update(reference_pin={**PIN, "state": "costume-change"}), "exact bound"),
    (lambda s, r, p: p.update(entity_mapping=[]), "missing the bound entity"),
    (lambda s, r, p: p["entity_mapping"][0].update(reference_pin={**PIN, "revision_id": "refv-other"}), "different identity"),
    (lambda s, r, p: p["entity_mapping"][0].update(technical_color=None), "color"),
])
def test_no_implicit_name_or_version_matching(change, match):
    scene, refs, receipt = setup()
    change(scene, refs, receipt)
    with pytest.raises(ValueError, match=match):
        bind_reference_identities(scene, refs, receipt)


def test_selected_unbound_prop_is_rejected_instead_of_appearing_in_prompt():
    scene, refs, receipt = setup()
    refs.append({"asset_id": "PROP-cup", "revision_id": "refv-cup", "state": "default", "category": "prop", "name": "Cup"})
    with pytest.raises(ValueError, match="Cup is not bound"):
        bind_reference_identities(scene, refs, receipt)


def test_room_requires_explicit_appearance_binding_and_unbound_props_are_disclosed():
    scene, refs, receipt = setup()
    room_pin = {"asset_id": "LOC-room", "revision_id": "refv-room", "state": "default"}
    scene.entities.append(Entity(id="room", name="Room", kind="room", state={"reference_pin": room_pin}))
    scene.entities.append(Entity(id="chair", name="Chair", kind="prop"))
    refs.append({**room_pin, "category": "location", "name": "Room"})
    bindings, warnings = bind_reference_identities(scene, refs, receipt)
    assert bindings[-1]["entity_id"] == "room"
    assert bindings[-1]["reference_tags"] == ["@Image4"]
    assert bindings[-1]["technical_color"] is None
    assert warnings[0]["entity_id"] == "chair"
    scene.entities[-2].state.clear()
    with pytest.raises(ValueError, match="bound to a room"):
        bind_reference_identities(scene, refs, receipt)
