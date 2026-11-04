from io import BytesIO
import hashlib
import json
import subprocess
from PIL import Image
import pytest
from ghost_director.api import create_app, public_snapshot
from ghost_director.config import Settings
from ghost_director.domain.models import Camera, Entity, SceneRevision, ShotPlan, MotionPlan
from ghost_director.jobs.store import ProductionStore, Conflict
from ghost_director.references import ReferenceRegistry
from ghost_director.service import ProductionService
from ghost_director.shot_workflow import revise_shot, compile_package, plan_digest, package_review_digest, PackageApproval, validate_package_dispatch
from httpx import AsyncClient, ASGITransport


def fixture(tmp_path):
    store = ProductionStore(tmp_path, opening_entries=())
    scene = SceneRevision(id='scene',entities=[Entity(id='cup',name='Cup',kind='prop')],cameras=[Camera(id='camera',name='Wide',position=(0,-4,2),target=(0,0,1))])
    store.publish_scene(scene.model_dump(mode='json'),expected_revision=None,actor='test')
    motion = MotionPlan(camera_keyframes=[{'time_seconds':t,'position':(0,-4,2),'target':(0,0,1),'focal_length_mm':50} for t in (0,4)],tracks=[{'entity_id':'cup','keyframes':[{'time_seconds':0,'position':(0,0,0)},{'time_seconds':4,'position':(1,0,0)}]}])
    shot = ShotPlan(id='shot',name='Cup',scene_revision_id='scene',camera_id='camera',duration=4,motion=motion)
    store.upsert_record('shots',shot.model_dump(mode='json'))
    raw=BytesIO(); Image.new('RGB',(300,300),'green').save(raw,format='PNG')
    return store,scene,shot,raw.getvalue()


def approved(store,raw):
    registry=ReferenceRegistry(store)
    asset=registry.create_asset({'category':'prop','name':'Cup','description':'One green cup'})
    asset=registry.attach_image(asset['id'],asset['revision_id'],raw,slot='primary',mime_type='image/png')
    registry.decide(asset['id'],asset['revision_id'],'approved',actor='test-evaluator',reason='Synthetic test reference')
    return {'asset_id':asset['id'],'revision_id':asset['revision_id'],'state':'default'}


@pytest.fixture(scope='module')
def motion_clip(tmp_path_factory):
    path = tmp_path_factory.mktemp('reference-motion-compile') / 'motion.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=gray:s=640x360:r=24:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True, timeout=20)
    return path.read_bytes()


async def compiled_fixture(tmp_path, motion_clip):
    store,scene,shot,raw=fixture(tmp_path)
    pin=approved(store,raw)
    scene.entities[0].state['reference_pin']=pin
    scene.id='bound-scene'
    scene.parent_id='scene'
    store.publish_scene(scene.model_dump(mode='json'),expected_revision='scene',actor='test')
    result=revise_shot(store,scene,{'shot_id':shot.id,'motion':shot.motion.model_dump(mode='json'),'reference_pins':[pin]})
    revised=ShotPlan.model_validate(store.get_record('shots',result['shot_id']))
    run=store.create_run()
    video=store.publish_artifact(motion_clip,kind='video',mime_type='video/mp4')
    source={'scene':scene.model_dump(mode='json'),'shot':revised.model_dump(mode='json'),'motion':revised.motion.model_dump(mode='json')}
    source_hash=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    receipt=store.publish_artifact({'source':'test boundary fixture','duration_seconds':4,'fps':24,'frame_count':96,'source_sha256':source_hash,'shot_id':revised.id,'scene_revision_id':scene.id,'video_sha256':video['sha256'],'evaluation':{'passed':True},'entity_mapping':[{'entity_id':'cup','kind':'prop','technical_color':[1,.5,0,1],'reference_pin':pin}]})
    preview={'shot_id':revised.id,'scene_revision_id':scene.id,'plan_sha256':plan_digest(revised),'video_id':video['id'],'receipt_id':receipt['id']}
    package=await compile_package(store,scene,revised,preview,run_id=run['id'])
    return store, scene, revised, preview, package, pin


@pytest.mark.asyncio
async def test_plan_revision_and_compiler_exact_inputs(tmp_path,motion_clip):
    store,scene,revised,preview,package,pin=await compiled_fixture(tmp_path,motion_clip)
    assert revised.parent_id=='shot' and revised.proposed_patch[0].position==(1,0,0)
    assert store.get_record('shots','shot')['proposed_patch']==[]
    assert package['reference_mapping'][0]['tag']=='@Image1'
    assert package['entity_reference_bindings'][0]['entity_id']=='cup'
    assert package['entity_reference_bindings'][0]['reference_tags']==['@Image1']
    assert package['entity_reference_bindings'][0]['technical_color']==[1,.5,0,1]
    assert package['appearance_coverage_warnings']==[]
    assert package['motion_video_id']==preview['video_id'] and '@Video1' in package['prompt']
    assert package['reference_pins']==[pin] and package['shot_plan']['motion']==revised.motion.model_dump(mode='json')
    assert package['review_sha256']==package_review_digest(package)
    assert package['pricing_estimate']['input_video_seconds']==4
    assert package['pricing_estimate']['reserve_usd']>package['pricing_estimate']['estimated_usd']>0
    assert store.scene()['entities'][0]['position']==[0,0,0]
    with pytest.raises(Conflict):await compile_package(store,scene,revised,{**preview,'plan_sha256':'stale'},run_id=package['run_id'])
    assert store.snapshot()['jobs']==[]


def approval(package, **changes):
    return {'accepted': True, 'review_sha256': package['review_sha256'], 'actor': 'local-operator',
            'note': 'I reviewed the exact references, blocking and cost estimate.', **changes}


@pytest.mark.asyncio
async def test_dispatch_requires_exact_review_and_rechecks_reference_and_receipt_bytes(tmp_path,motion_clip):
    store,scene,_,_,package,_=await compiled_fixture(tmp_path,motion_clip)
    decision=PackageApproval.model_validate(approval(package))
    await validate_package_dispatch(store,scene,package,decision)
    with pytest.raises(Conflict,match='exact reviewed package'):
        await validate_package_dispatch(store,scene,package,PackageApproval.model_validate(approval(package,review_sha256='0'*64)))
    with pytest.raises(Conflict,match='exact reviewed package'):
        await validate_package_dispatch(store,scene,{**package,'prompt':'Changed after review'},decision)
    reference=store.artifact_path(package['reference_ids'][0]); previous=reference.read_bytes()
    reference.write_bytes(b'changed')
    with pytest.raises(Conflict,match='hash'):
        await validate_package_dispatch(store,scene,package,decision)
    reference.write_bytes(previous)
    receipt=store.artifact_path(package['motion_receipt_id']); receipt.write_text('{}')
    with pytest.raises(Conflict,match='receipt'):
        await validate_package_dispatch(store,scene,package,decision)
    assert store.snapshot()['jobs']==[]


@pytest.mark.parametrize('changes',[{'accepted':False},{'accepted':1},{'note':'  '},{'actor':'director'}])
def test_package_approval_requires_explicit_local_operator_decision(changes):
    with pytest.raises(ValueError):
        PackageApproval.model_validate(approval({'review_sha256':'1'*64},**changes))


@pytest.mark.asyncio
async def test_approved_dispatch_records_authorization_before_only_call(tmp_path,motion_clip):
    store,scene,_,_,package,_=await compiled_fixture(tmp_path,motion_clip)
    service=ProductionService(store,Settings('','','',True,tmp_path))
    calls=[]
    async def video(prompt,images,**kwargs):
        events=store.events(0,limit=1000)
        authorization=next(row for row in events if row['type']=='package.generation_authorized')
        assert authorization['payload']['review_sha256']==package['review_sha256']
        assert authorization['payload']['actor']=='local-operator'
        assert prompt==package['prompt'] and images==package['reference_ids']
        assert kwargs['video_reference_ids']==[package['motion_video_id']]
        calls.append(kwargs)
        return {},{}
    async def finish(*args):return {'id':'take'}
    service.runner.video=video; service.finish_take=finish
    try:
        with pytest.raises(ValueError):
            await service.handle({'id':'unapproved','operation':'generate_package','expected_revision':scene.id,'payload':{'package_id':package['id']}})
        assert calls==[]
        result=await service.handle({'id':'approved','operation':'generate_package','expected_revision':scene.id,'payload':{'package_id':package['id'],'approval':approval(package)}})
        assert result['take_id']=='take' and len(calls)==1
    finally:await service.runner.http.close()


@pytest.mark.asyncio
async def test_compiler_rejects_provider_invalid_crop_before_marking_ready(tmp_path,motion_clip):
    store,scene,shot,preview,package,pin=await compiled_fixture(tmp_path,motion_clip)
    registry=ReferenceRegistry(store)
    raw=BytesIO();Image.new('RGB',(240,240),'green').save(raw,format='PNG')
    asset=registry.attach_image(pin['asset_id'],pin['revision_id'],raw.getvalue(),slot='primary',mime_type='image/png')
    registry.decide(asset['id'],asset['revision_id'],'approved',actor='test',reason='Approved image; provider dimensions differ')
    shot.reference_pins[0].revision_id=asset['revision_id']
    scene.entities[0].state['reference_pin']['revision_id']=asset['revision_id']
    source={'scene':scene.model_dump(mode='json'),'shot':shot.model_dump(mode='json'),'motion':shot.motion.model_dump(mode='json')}
    receipt=json.loads(store.artifact_path(preview['receipt_id']).read_text())
    receipt['source_sha256']=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    receipt['entity_mapping'][0]['reference_pin']['revision_id']=asset['revision_id']
    artifact=store.publish_artifact(receipt)
    preview.update(plan_sha256=plan_digest(shot),receipt_id=artifact['id'])
    with pytest.raises(ValueError,match='dimensions'):
        await compile_package(store,scene,shot,preview,run_id=package['run_id'])
    assert len(store.list_records('packages'))==1 and store.snapshot()['jobs']==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value',[('duration_seconds',5),('fps',30),('frame_count',95)])
async def test_compiler_rejects_different_blender_timeline(tmp_path,motion_clip,field,value):
    store,scene,shot,preview,package,_=await compiled_fixture(tmp_path,motion_clip)
    receipt=json.loads(store.artifact_path(preview['receipt_id']).read_text())
    receipt[field]=value
    artifact=store.publish_artifact(receipt)
    with pytest.raises(Conflict,match='timeline'):
        await compile_package(store,scene,shot,{**preview,'receipt_id':artifact['id']},run_id=package['run_id'])
    assert len(store.list_records('packages'))==1 and store.snapshot()['jobs']==[]


@pytest.mark.asyncio
async def test_compiler_rejects_actual_clip_duration_despite_matching_declared_receipt(tmp_path,motion_clip):
    source=tmp_path/'source.mp4';source.write_bytes(motion_clip)
    target=tmp_path/'short.mp4'
    subprocess.run(['ffmpeg','-v','error','-i',str(source),'-t','2','-c','copy',str(target)],check=True,timeout=20)
    with pytest.raises(Conflict,match='Decoded motion clip duration'):
        await compiled_fixture(tmp_path/'production',target.read_bytes())
    store=ProductionStore(tmp_path/'production',opening_entries=())
    assert store.list_records('packages')==[] and store.snapshot()['jobs']==[]


@pytest.mark.asyncio
async def test_upload_boundary_and_replay(tmp_path):
    store,scene,shot,raw=fixture(tmp_path)
    registry=ReferenceRegistry(store)
    asset=registry.create_asset({'category':'prop','name':'Cup','description':'Green cup'})
    app=create_app(store)
    url=f'/api/productions/{store.production_id}/references/{asset["id"]}/images?expected_revision_id={asset["revision_id"]}&slot=primary'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
        response=await client.post(url,content=raw,headers={'Content-Type':'image/png','X-Ghost-View-Mode':'replay'})
        assert response.status_code==409
        response=await client.post(url,content=raw,headers={'Content-Type':'image/png','Origin':'http://evil.invalid'})
        assert response.status_code==403
        response=await client.post(url,content=b'bad',headers={'Content-Type':'image/png'})
        assert response.status_code==422
        response=await client.post(url,content=raw,headers={'Content-Type':'image/png'})
        assert response.status_code==200 and response.json()['approval_status']=='draft'
        response=await client.post(url,content=raw,headers={'Content-Type':'image/png'})
        assert response.status_code==409
    assert public_snapshot(store.snapshot())['reference_templates']['character']['required_slots']==['front','back','face']


@pytest.mark.asyncio
async def test_sheet_generation_is_single_call_and_not_approval(tmp_path):
    store,scene,shot,raw=fixture(tmp_path)
    service=ProductionService(store,Settings('','','',False,tmp_path))
    asset=ReferenceRegistry(store).create_asset({'category':'character','name':'Fictional adult','description':'Original adult character'})
    count=[]
    async def image(prompt,**kwargs):
        count.append(prompt)
        buffer=BytesIO();Image.new('RGB',(900,600),'blue').save(buffer,format='PNG')
        return store.publish_artifact(buffer.getvalue(),kind='image',mime_type='image/png'),{}
    service.runner.image=image
    try:
        result=await service.handle({'id':'command','operation':'generate_reference','expected_revision':scene.id,'payload':{'asset_id':asset['id'],'expected_revision_id':asset['revision_id'],'prompt':'Blue shirt, neutral standing reference'}})
        assert len(count)==1 and result['missing_slots']==[]
        assert result['approved_revision_id'] is None and result['approval_status']=='draft'
        references=ReferenceRegistry(store).version(result['revision_id'])['references']
        assert {r['slot'] for r in references}=={'front','back','face'}
        assert all(r['original_artifact_id']==result['sheet_id'] for r in references)
        assert store.scene()['id']==scene.id
    finally:await service.runner.http.close()

@pytest.mark.asyncio
async def test_reference_crop_creates_unapproved_revision_and_preserves_original(tmp_path):
    store,scene,shot,raw=fixture(tmp_path)
    pin=approved(store,raw)
    registry=ReferenceRegistry(store);previous=registry.version(pin['revision_id'])['references'][0]
    service=ProductionService(store,Settings('','','',False,tmp_path))
    try:
        payload={'asset_id':pin['asset_id'],'expected_revision_id':pin['revision_id'],'slot':'primary','box':[.1,.1,.9,.9]}
        updated=await service.handle({'id':'crop','operation':'crop_reference','expected_revision':scene.id,'payload':payload})
        assert updated['approval_status']=='draft' and updated['approved_revision_id']==pin['revision_id']
        cropped=registry.version(updated['revision_id'])['references'][0]
        assert cropped['width']==240 and cropped['height']==240
        assert cropped['original_artifact_id']==previous['original_artifact_id']
        with pytest.raises(Conflict):
            await service.handle({'id':'crop-again','operation':'crop_reference','expected_revision':scene.id,'payload':payload})
        with pytest.raises(ValueError):
            await service.handle({'id':'invalid','operation':'crop_reference','expected_revision':scene.id,'payload':{**payload,'box':[0,0,2,1]}})
        assert not store.snapshot()['jobs']
    finally: await service.runner.http.close()


@pytest.mark.asyncio
async def test_existing_identity_cannot_be_silently_regenerated_from_text(tmp_path):
    store, scene, shot, raw = fixture(tmp_path)
    pin = approved(store, raw)
    service = ProductionService(store, Settings('', '', '', False, tmp_path))
    async def no_image(*args, **kwargs):
        pytest.fail("Existing identity must be blocked before any provider call")
    service.runner.image = no_image
    before = store.snapshot()
    try:
        with pytest.raises(Conflict, match="does not yet support conditioning"):
            await service.handle({'id': 'regenerate', 'operation': 'generate_reference', 'expected_revision': scene.id, 'payload': {'asset_id': pin['asset_id'], 'expected_revision_id': pin['revision_id'], 'prompt': 'A different cup'}})
        assert store.snapshot()['reference_versions'] == before['reference_versions']
        assert store.snapshot()['jobs'] == before['jobs']
        assert store.snapshot()['runs'] == before['runs']
    finally:
        await service.runner.http.close()


@pytest.mark.asyncio
async def test_new_location_generation_creates_one_master_not_invented_opposite(tmp_path):
    store, scene, shot, raw = fixture(tmp_path)
    registry = ReferenceRegistry(store)
    asset = registry.create_asset({'category': 'location', 'name': 'Dining room'})
    service = ProductionService(store, Settings('', '', '', False, tmp_path))
    prompts = []
    async def image(prompt, **kwargs):
        prompts.append(prompt)
        return store.publish_artifact(raw, kind='image', mime_type='image/png'), {}
    service.runner.image = image
    try:
        result = await service.handle({'id': 'master', 'operation': 'generate_reference', 'expected_revision': scene.id, 'payload': {'asset_id': asset['id'], 'expected_revision_id': asset['revision_id'], 'prompt': 'Oak dining room with north windows'}})
        refs = registry.version(result['revision_id'])['references']
        assert [ref['slot'] for ref in refs] == ['wide']
        assert refs[0]['width'] == 300 and refs[0]['height'] == 300
        assert len(prompts) == 1 and 'Master photographic location reference' in prompts[0]
        assert result['approval_status'] == 'draft' and result['missing_slots'] == []
    finally:
        await service.runner.http.close()
