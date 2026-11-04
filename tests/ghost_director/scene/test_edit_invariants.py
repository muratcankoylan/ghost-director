"""Creative edits cannot rebind geometry or split a fused actor/chair."""
import pytest
from ghost_director.domain.models import Entity, EntityPatch, SceneRevision, apply_entity_patches


def test_asset_provenance_is_not_an_editable_state_field():
    scene = SceneRevision(id='scene', cameras=[], entities=[Entity(id='actor', name='Actor', kind='actor', state={'artifact_id':'pinned'})])
    for key in ('artifact_id', 'reference_artifact_id', 'blend_artifact_id', 'included_in_entity_id'):
        with pytest.raises(ValueError, match='provenance'):
            apply_entity_patches(scene, [EntityPatch(entity_id='actor', state={key:'replacement'})], revision_id='next')
    assert scene.entities[0].state == {'artifact_id':'pinned'}


def test_fused_chair_moves_with_actor_and_cannot_be_edited_independently():
    scene = SceneRevision(id='scene', cameras=[], entities=[
        Entity(id='actor', name='Actor and chair', kind='actor'),
        Entity(id='chair', name='Included chair', kind='set', visible=False, state={'included_in_entity_id':'actor'}),
    ])
    with pytest.raises(ValueError, match='part of actor'):
        apply_entity_patches(scene, [EntityPatch(entity_id='chair', position=(3,0,0))], revision_id='next')
    revised = apply_entity_patches(scene, [EntityPatch(entity_id='actor', position=(3,0,0))], revision_id='next')
    assert revised.entities[0].position == (3,0,0)
    assert scene.entities[0].position == (0,0,0)


def test_observed_prop_state_remains_editable():
    scene = SceneRevision(id='scene', cameras=[], entities=[Entity(id='cup', name='Cup', kind='prop', state={'artifact_id':'mesh','coaster':'near'})])
    revised = apply_entity_patches(scene, [EntityPatch(entity_id='cup', state={'coaster':'far'})], revision_id='next')
    assert revised.entities[0].state == {'artifact_id':'mesh','coaster':'far'}
