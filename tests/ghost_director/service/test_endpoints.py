"""A proposed endpoint can condition video without becoming creative truth."""
from io import BytesIO
from PIL import Image
import pytest
from ghost_director.config import Settings
from ghost_director.domain.models import Entity, EntityPatch, SceneRevision, ShotPlan
from ghost_director.jobs.store import ProductionStore, NotFound
from ghost_director.service import ProductionService

@pytest.mark.asyncio
@pytest.mark.parametrize('moves_cup',[True,False])
async def test_two_rendered_endpoints_preserve_head_and_bind_exact_patch(tmp_path,moves_cup):
    store=ProductionStore(tmp_path,opening_entries=())
    scene=SceneRevision(id='initial',entities=[Entity(id='cup',name='Cup',kind='prop')],cameras=[])
    store.publish_scene(scene.model_dump(mode='json'),expected_revision=None,actor='test')
    shot=ShotPlan(id='shot',name='Move cup',scene_revision_id=scene.id,camera_id='camera',duration=8,proposed_patch=[EntityPatch(entity_id='cup',position=(2,0,0))] if moves_cup else [])
    store.upsert_record('shots',shot.model_dump(mode='json'))
    service=ProductionService(store,Settings('','','',False,tmp_path,video_model='fal-ai/veo3.1/first-last-frame-to-video'))
    rendered=[];submitted=[]
    async def render(state,shot,run):
        rendered.append(state)
        raw=BytesIO();Image.new('RGB',(8,8),'red' if len(rendered)==1 else 'blue').save(raw,format='PNG')
        return store.publish_artifact(raw.getvalue(),kind='image',mime_type='image/png')
    async def role(*args,**kwargs):
        return 'Move from first frame to second','task',store.publish_artifact('prompt',kind='prompt',mime_type='text/plain')
    async def video(prompt,refs,**kwargs):
        submitted.extend(refs)
        assert store.scene()['id']=='initial'
        return {},{}
    async def finish(video,job,package,state,task):return {'id':'take'}
    service.render=render;service.role=role;service.runner.video=video;service.finish_take=finish
    try:
        await service.generate('shot',scene)
        package=store.list_records('packages')[0]
        assert len(rendered)==(2 if moves_cup else 1) and rendered[0].entities[0].position==(0,0,0)
        if moves_cup:assert rendered[1].entities[0].position==(2,0,0)
        assert store.scene()['entities'][0]['position']==[0,0,0]
        with pytest.raises(NotFound):store.scene(package['proposed_end_state']['id'])
        assert package['reference_ids']==submitted and len(submitted)==2
        assert package['conditioning_mode']=='first_last_frame'
        assert package['proposed_end_state']['entities'][0]['position']==([2,0,0] if moves_cup else [0,0,0])
        if not moves_cup:assert store.artifact(submitted[0])['sha256']==store.artifact(submitted[1])['sha256']
        assert not store.snapshot()['jobs']
    finally:await service.runner.http.close()
