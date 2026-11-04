"""Reference-first shots cannot silently return to rendered-frame conditioning."""
import json
from unittest.mock import AsyncMock

import pytest

from ghost_director.config import Settings
from ghost_director.domain.models import Camera, Entity, MotionPlan, SceneRevision, ShotPlan
from ghost_director.jobs.store import Conflict, ProductionStore
from ghost_director.service import ProductionService


def setup(tmp_path):
    store=ProductionStore(tmp_path,opening_entries=())
    scene=SceneRevision(id='scene',entities=[Entity(id='cup',name='Cup',kind='prop')],
        cameras=[Camera(id='camera',name='Wide',position=(0,-4,2),target=(0,0,1))])
    store.publish_scene(scene.model_dump(mode='json'),expected_revision=None,actor='test')
    return store,scene,ProductionService(store,Settings('','','',False,tmp_path))


def motion(duration):
    return MotionPlan(camera_keyframes=[{'time_seconds':t,'position':(0,-4,2),'target':(0,0,1),'focal_length_mm':50} for t in (0,duration)])


@pytest.mark.asyncio
@pytest.mark.parametrize('reference_only',[False,True])
async def test_legacy_generate_rejects_shared_shots_before_render_or_spend(tmp_path,reference_only):
    store,scene,service=setup(tmp_path)
    shot=ShotPlan(id='shot',name='Shared',scene_revision_id=scene.id,camera_id='camera',duration=4,
        motion=None if reference_only else motion(4),
        reference_pins=[{'asset_id':'PROP-1','revision_id':'refv-1'}] if reference_only else [])
    store.upsert_record('shots',shot.model_dump(mode='json'))
    service.render=AsyncMock(side_effect=AssertionError('Legacy rendering must not start'))
    service.runner.video=AsyncMock(side_effect=AssertionError('Provider must not start'))
    try:
        with pytest.raises(Conflict,match='shared plan'):
            await service.generate(shot.id,scene)
        service.render.assert_not_called();service.runner.video.assert_not_called()
        assert store.snapshot()['jobs']==[] and store.snapshot()['runs']==[]
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_reference_take_repair_does_not_fall_back_to_legacy_generation(tmp_path):
    store,scene,service=setup(tmp_path)
    store.upsert_record('packages',{'id':'package','workflow':'reference-motion-v1'})
    store.upsert_record('takes',{'id':'take','scene_revision_id':scene.id,'package_id':'package'})
    service.generate=AsyncMock(side_effect=AssertionError('Legacy repair must not start'))
    try:
        with pytest.raises(Conflict,match='revised shared plan'):
            await service.handle({'id':'repair','operation':'repair_take','expected_revision':scene.id,'payload':{'take_id':'take'}})
        service.generate.assert_not_called()
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_director_duration_drives_the_same_fifteen_second_motion_plan(tmp_path):
    store,scene,service=setup(tmp_path)
    async def role(name,goal,*args,**kwargs):
        if name=='producer':return '{}','producer-task',{}
        assert 'duration (integer seconds, 4–30)' in goal
        return json.dumps({'name':'Fifteen seconds','camera_id':'camera','duration':15,'action':'Hold the cup',
            'motion':motion(15).model_dump(mode='json'),'proposed_patch':[],'assertions':[]}), 'dp-task', {}
    service.role=role
    try:
        result=await service.direct({'text':'Hold for fifteen seconds'},scene)
        shot=store.get_record('shots',result['shot_id'])
        assert shot['duration']==15 and shot['motion']['camera_keyframes'][-1]['time_seconds']==15
        assert store.snapshot()['jobs']==[]
    finally:await service.runner.http.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('duration',[None,True,3,31,4.5])
async def test_director_invalid_or_missing_duration_never_publishes_shot(tmp_path,duration):
    store,scene,service=setup(tmp_path)
    async def role(name,*args,**kwargs):
        return ('{}' if name=='producer' else json.dumps({'name':'Invalid','camera_id':'camera','duration':duration})), 'task', {}
    service.role=role
    try:
        with pytest.raises(ValueError,match='explicit integer'):
            await service.direct({'text':'Plan one shot'},scene)
        assert store.list_records('shots')==[]
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_save_view_creates_valid_shared_plan_without_changing_legacy_default(tmp_path):
    store,scene,service=setup(tmp_path)
    camera=scene.cameras[0].model_dump(mode='json',exclude={'id','name'})
    try:
        saved=await service.handle({'id':'save','operation':'save_shot','expected_revision':scene.id,
            'payload':{'name':'Fifteen-second hold','camera':camera,'duration':15,'workflow':'reference-motion-v1'}})
        assert saved['duration']==15 and saved['motion']['camera_keyframes'][-1]['time_seconds']==15
        assert saved['motion']['camera_keyframes'][0]['position']==camera['position']
        legacy=await service.handle({'id':'legacy','operation':'save_shot','expected_revision':saved['scene_revision_id'],
            'payload':{'name':'Legacy','camera':camera}})
        assert legacy['motion'] is None and legacy['duration']==8
        assert store.snapshot()['jobs']==[]
    finally:await service.runner.http.close()
