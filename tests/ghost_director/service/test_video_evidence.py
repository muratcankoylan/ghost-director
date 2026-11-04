"""Decode real local video bytes; review mocks never contact a provider."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import pytest
from PIL import Image
from ghost_director.config import Settings
from ghost_director.domain.models import SceneRevision
from ghost_director.jobs.store import ProductionStore
from ghost_director.media import process
from ghost_director.service import ProductionService


@pytest.mark.asyncio
@pytest.mark.parametrize(('conditioning_mode','colors','reference_roles'),[
    ('references',('red','blue','white'),['reference_scene','reference_eren','reference_leyla']),
    ('first_last_frame',('red','green','blue','white'),['reference_scene','proposed_endpoint','reference_eren','reference_leyla']),
])
async def test_review_compares_sampled_frames_with_exact_submitted_references(tmp_path,conditioning_mode,colors,reference_roles):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required for media integration check')
    store=ProductionStore(tmp_path,opening_entries=())
    service=ProductionService(store,Settings('','','',False,tmp_path))
    try:
        clip=tmp_path/'test.mp4'
        await process('ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=128x72:r=24','-t','2','-c:v','libx264','-pix_fmt','yuv420p',clip)
        video=store.publish_artifact(source_path=clip,kind='video',mime_type='video/mp4')
        refs=[]
        for color in colors:
            stream=BytesIO();Image.new('RGB',(8,8),color).save(stream,format='PNG')
            refs.append(store.publish_artifact(stream.getvalue(),kind='image',mime_type='image/png')['id'])
        run=store.create_run()
        package={'id':'package','run_id':run['id'],'shot_id':'shot','scene_revision_id':'scene','reference_ids':refs[:2] if conditioning_mode=='first_last_frame' else refs,'proposed_patch':[],'assertions':['A','B']}
        if conditioning_mode=='first_last_frame':
            package.update(conditioning_mode=conditioning_mode,planning_reference_ids=refs)
        captured={}
        async def review(role,goal,inputs,run_id,**options):
            captured.update(role=role,inputs=inputs,options=options)
            return json.dumps({'observations':[{'assertion':'A','status':'pass','detail':'Visible at last sample','frame_index':2}]}),'task',{}
        service.role=review
        take=await service.finish_take(video,{'id':'job'},package,SceneRevision(id='scene',entities=[],cameras=[]))
        assert take['status']=='reviewed'
        assert captured['options']['images'][3:]==refs
        assert captured['inputs']['ordered_image_roles']==['output_first','output_middle','output_last',*reference_roles]
        assert len(captured['options']['images'])==len(captured['inputs']['ordered_image_roles'])
        assert captured['inputs']['sample_time_ms']==[0,1000,1850]
        assert captured['inputs']['video_metadata']['width']==128
        observations=store.list_records('observations')
        assert [o['status'] for o in observations]==['pass','unknown']
        assert observations[0]['evidence_ids'][1:]==refs
        assert observations[0]['time_ms']==1850
        assert not store.snapshot()['jobs']
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_corrupt_video_never_becomes_a_playable_take(tmp_path):
    store=ProductionStore(tmp_path,opening_entries=())
    service=ProductionService(store,Settings('','','',False,tmp_path))
    try:
        video=store.publish_artifact(b'not-a-video',kind='video',mime_type='video/mp4')
        with pytest.raises(RuntimeError,match='ffprobe'):
            await service.finish_take(video,{'id':'job'},{},SceneRevision(id='scene',entities=[],cameras=[]))
        assert not store.list_records('takes')
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_reference_motion_review_uses_appearance_references_and_explicit_evidence_limits(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required for media integration check')
    store=ProductionStore(tmp_path,opening_entries=())
    service=ProductionService(store,Settings('','','',False,tmp_path))
    try:
        clip=tmp_path/'test.mp4'
        await process('ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=green:s=128x72:r=24','-t','2','-c:v','libx264','-pix_fmt','yuv420p',clip)
        video=store.publish_artifact(source_path=clip,kind='video',mime_type='video/mp4')
        stream=BytesIO();Image.new('RGB',(8,8),'blue').save(stream,format='PNG')
        reference=store.publish_artifact(stream.getvalue(),kind='image',mime_type='image/png')
        run=store.create_run()
        mapping=[{'tag':'@Image1','asset_id':'PROP-cup','revision_id':'refv-approved','name':'Blue cup','slot':'primary','artifact_id':reference['id']}]
        bindings=[{'entity_id':'cup','asset_id':'PROP-cup','revision_id':'refv-approved','reference_tags':['@Image1']}]
        motion={'duration':2,'fps':24,'tracks':[{'entity_id':'cup','keyframes':[{'time':0,'position':[0,0,1]},{'time':2,'position':[1,0,1]}]}]}
        warnings=[{'entity_id':'room','reason':'No approved appearance reference'}]
        package={'id':'package','workflow':'reference-motion-v1','run_id':run['id'],'shot_id':'shot','scene_revision_id':'scene',
                 'reference_ids':[reference['id']],'reference_mapping':mapping,'entity_reference_bindings':bindings,
                 'shot_plan':{'motion':motion},'appearance_coverage_warnings':warnings,
                 'motion_video_id':'technical-motion-video','proposed_patch':[],'assertions':['Cup is blue','Motion is continuous']}
        captured={}
        async def review(role,goal,inputs,run_id,**options):
            captured.update(role=role,goal=goal,inputs=inputs,options=options)
            return json.dumps({'observations':[{'assertion':'Cup is blue','status':'fail','detail':'Visible output is green','frame_index':2}]}),'task',{}
        service.role=review
        await service.finish_take(video,{'id':'job'},package,SceneRevision(id='scene',entities=[],cameras=[]))
        assert captured['inputs']['ordered_image_roles']==['output_first','output_middle','output_last','approved_identity:Blue cup:primary']
        assert captured['options']['images'][3:]==[reference['id']]
        assert 'technical-motion-video' not in captured['options']['images']
        assert captured['inputs']['reference_mapping']==mapping
        assert captured['inputs']['entity_reference_bindings']==bindings
        assert captured['inputs']['motion_plan']==motion
        assert captured['inputs']['appearance_coverage_warnings']==warnings
        assert 'approved appearance reference images' in captured['goal']
        assert 'technical motion video is not attached' in captured['goal']
        assert 'No Blender appearance frame or proposed endpoint is attached' in captured['goal']
        assert 'planned constraints, not evidence' in captured['goal']
        assert 'against the Blender reference' not in captured['goal']
        observations=store.list_records('observations')
        assert [item['status'] for item in observations]==['fail','unknown']
        assert observations[0]['time_ms']==1850
        assert observations[0]['evidence_ids'][1:]==[reference['id']]
        assert not store.snapshot()['jobs']
    finally:await service.runner.http.close()
