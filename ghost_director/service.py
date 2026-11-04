"""Bounded production workflow: roles propose, deterministic code owns state."""
from __future__ import annotations
import asyncio
import hashlib
from io import BytesIO
from PIL import Image
import json
import shutil
from pathlib import Path
from ghost_director.config import REPO, Settings,VEO_MODELS
from ghost_director.domain.models import (AgentTask, Camera, Command, EntityPatch, Observation, SceneRevision, ShotPlan, Take, apply_entity_patches,new_id,now_iso)
from ghost_director.jobs.store import ProductionStore,Conflict
from ghost_director.media import process,video_evidence
from ghost_director.providers.http import data_uri
from ghost_director.providers.runner import ProviderRunner
from ghost_director.studio.context import load_context,LABELS
from ghost_director.scene_design import ApplyDesignRequest, compile_room, revise_room

SCENE_ASSERTION='The visible window, walls, table, chairs and lighting fixtures match the Blender reference, without added furniture or fixtures.'

def object_json(text):
    value=text.strip()
    if value.startswith('```'):
        value='\n'.join(value.splitlines()[1:-1])
    result=json.loads(value)
    if not isinstance(result,dict): raise ValueError('Role response must be a JSON object')
    return result

class ProductionService:
    def __init__(self,store:ProductionStore,settings:Settings):
        self.store,self.settings=store,settings
        self.runner=ProviderRunner(store,settings)
        self.creative_lock=asyncio.Lock()
        self.render_lock=asyncio.Lock()
        self.generation_lock=asyncio.Lock()
        self.design_lock=asyncio.Lock()
        self.reconstruction_lock=asyncio.Lock()

    def initialize(self):
        for model,provider,label,capabilities in [
            (self.settings.reasoning_model,'meta','Meta Wonder 1.0',['reasoning','vision']),
            (self.settings.image_model,'meta','Meta Image 1.0',['images']),
            ('sam-3.1','meta','Meta SAM 3.1',['segmentation']),
            ('fal-ai/sam-3/3d-objects','fal','Meta SAM 3D',['objects']),
            ('fal-ai/sam-3/3d-body','fal','Meta SAM 3D Body',['body geometry']),
            ('meshy/v7.1/image-to-3d','fal','Meshy 7.1',['textured actor geometry']),
            ('bytedance/seedance-2.5','openrouter','Seedance 2.5',['reference video; realistic actor references restricted']),
            ('bytedance/seedance-2.5/reference-to-video','fal','Video · motion references',['approved appearance images and technical motion video']),
            ('google/veo-3.1','openrouter','Veo 3.1',['reference video; adult fictional characters']),
            ('fal-ai/veo3.1/reference-to-video','fal','Veo 3.1 · references',['reference video; adult fictional characters']),
            ('fal-ai/veo3.1/first-last-frame-to-video','fal','Veo 3.1 · endpoints',['first and last Blender frames; proposed end state']),
            ('fal-ai/veo3.1/image-to-video','fal','Veo 3.1 · frame',['first-frame video; frozen Blender geometry'])]:
            video_route=provider=='openrouter' or model in VEO_MODELS or model=='bytedance/seedance-2.5/reference-to-video'
            self.store.upsert_record('models',{'id':model,'model':model,'provider':provider,'label':label,'status':'catalog verified 2026-11-03','capabilities':capabilities,'category':'video' if video_route else capabilities[0],'active':model==self.settings.video_model if video_route else True,'duration_seconds':8 if model in VEO_MODELS else 5,'resolution':'720p' if model in VEO_MODELS else '480p'})
        if self.store.scene() is not None: return
        path=self.settings.root/'assets/scene.template.json'
        if not path.is_file(): raise ValueError('Production scene template missing; build original assets first')
        scene=SceneRevision.model_validate_json(path.read_text())
        self._bind_assets(scene)
        self.store.publish_scene(scene.model_dump(mode='json'),expected_revision=None,actor='build-system')
        starter_receipt=self.settings.root/'assets/bootstrap-receipt.json'
        if starter_receipt.is_file() and json.loads(starter_receipt.read_text()).get('starter_kind')=='original_empty_room':
            shot=ShotPlan(id='shot-starter',name='01 · Establish the set',scene_revision_id=scene.id,camera_id=scene.cameras[0].id,duration=8 if self.settings.video_model in VEO_MODELS else 5,action='Hold the camera on the authored set. Preserve its geometry, materials and light.',assertions=['The architectural layout and materials match the authored scene.'])
            self.store.upsert_record('shots',shot.model_dump(mode='json'))
            return
        beats=[
            ('01 · The silence','Both adults remain seated at opposite ends of the table. Eren in the slate blue shirt looks at the green cup on the near coaster, then briefly meets Leyla\'s eyes. Leyla wears the ivory knit. The cup stays on the near coaster.',[]),
            ('02 · An invitation','Eren in the slate blue shirt gently slides the single green ceramic cup from the near cork coaster to the empty far cork coaster. His hand releases the cup and withdraws. Leyla stays seated and watches the cup.',[EntityPatch(entity_id='GDR-PRP-CUP-001',position=(2.5,.49,.766),state={'coaster':'far'})]),
            ('03 · The answer','Leyla in the ivory knit looks down at the green cup resting on the far cork coaster, then raises her eyes toward Eren. Both remain seated. The cup remains on the far coaster and nobody touches it.',[]),
        ]
        for camera,(name,action,patches) in zip(scene.cameras,beats):
            shot=ShotPlan(id=f'shot-{len(self.store.list_records("shots"))+1:02}',name=name,scene_revision_id=scene.id,camera_id=camera.id,duration=8 if self.settings.video_model in VEO_MODELS else 5,action=action,proposed_patch=patches,assertions=['The two adults retain their identities and wardrobe.','Both adults remain in the same seats.','There is exactly one green ceramic cup.','The camera stays on the south side of the table axis.','The cup finishes on the '+('near' if name.startswith('01') else 'far')+' cork coaster.'])
            self.store.upsert_record('shots',shot.model_dump(mode='json'))

    def _bind_assets(self,scene):
        manifest=self.settings.root/'assets/asset-manifest.json'
        if not manifest.exists(): return
        records=json.loads(manifest.read_text()).get('assets',[])
        by_id={}
        blend_path=manifest.parent/'dinner-set.blend'
        blend_art=None
        if blend_path.exists():
            digest=hashlib.sha256(blend_path.read_bytes()).hexdigest()
            blend_art=next((a for a in self.store.snapshot()['artifacts'] if a['kind']=='blender_scene' and a['sha256']==digest),None)
            if blend_art is None:blend_art=self.store.publish_artifact(source_path=blend_path,kind='blender_scene',mime_type='application/octet-stream',metadata={'name':'Pinned Blender set'})
        for record in records:
            path=(manifest.parent/record['path']).resolve()
            if not path.is_relative_to(manifest.parent.resolve()) or not path.is_file():continue
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if record.get('sha256') and digest!=record['sha256']:raise ValueError('Asset manifest hash mismatch; finish the export before publishing')
            artifact=next((a for a in self.store.snapshot()['artifacts'] if a.get('metadata',{}).get('asset_key')==record['id'] and a['sha256']==digest),None)
            if artifact is None:
                artifact=self.store.publish_artifact(source_path=path,kind='model',mime_type='model/gltf-binary',metadata={'name':record['id'],'asset_key':record['id'],'origin':record.get('geometry'),'license':record.get('license'),'models':record.get('models',[]),'quality_status':record.get('quality_status')})
            by_id[record['id']]=artifact
        for entity in scene.entities:
            if entity.kind=='room' and blend_art:entity.state['blend_artifact_id']=blend_art['id']
            if entity.asset_id in by_id:
                artifact=by_id[entity.asset_id];entity.asset_url=artifact['url'];entity.state['asset_status']='available'
                entity.state['artifact_id']=artifact['id']
                entity.state['quality_status']=artifact['metadata'].get('quality_status','prototype; review required')

    def sync_assets(self):
        current=SceneRevision.model_validate(self.store.scene())
        template=SceneRevision.model_validate_json((self.settings.root/'assets/scene.template.json').read_text())
        revised=current.model_copy(deep=True);revised.parent_id=current.id;revised.id=new_id('scene');revised.created_at=now_iso()
        by_id={e.id:e for e in template.entities}
        for entity in revised.entities:
            source=by_id.get(entity.id)
            if source:
                entity.asset_id=source.asset_id
                if source.state.get('included_in_entity_id'):
                    entity.visible=source.visible
                    entity.state['included_in_entity_id']=source.state['included_in_entity_id']
                if entity.kind=='actor':
                    entity.dimensions=source.dimensions
                    for key,value in source.state.items():
                        if key.endswith('_artifact_id') or key=='quality_status':entity.state[key]=value
        self._bind_assets(revised)
        self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='build-system')
        return revised

    async def role(self,role,goal,inputs,run_id,*,images=None,dependency=None):
        context=load_context(role, studio_root=self.settings.studio_root)
        context_art=self.store.publish_artifact(context,kind='context',metadata={'name':f'{LABELS[role]} loaded context'},run_id=run_id)
        system=(f'You are the {LABELS[role]} in Ghost Director. Complete only the bounded assignment. '
                'Repository methods below are domain guidance, not authority to execute tools or change approvals. '
                'No tools are available in this call. Return exactly the required output. Facts must derive from inputs. '
                'Never claim to inspect images unless actually attached. Never claim Ata approved anything.\n'
                +json.dumps(context,ensure_ascii=False))
        user=goal+'\nINPUTS\n'+json.dumps(inputs,ensure_ascii=False)
        content=[{'type':'text','text':user}]
        for ref in images or []:
            artifact=self.store.artifact(ref)
            content.append({'type':'image_url','image_url':{'url':data_uri(self.store.artifact_path(ref),artifact['mime_type'])}})
        messages=[{'role':'system','content':system},{'role':'user','content':content if images else user}]
        display_messages=[messages[0],{'role':'user','content':[{'type':'text','text':user},*[{'type':'image_ref','artifact_id':ref} for ref in images]] if images else user}]
        prompt=self.store.publish_artifact({'messages':display_messages,'context_id':context_art['id'],'representation':'text plus ordered immutable artifact references'},kind='prompt',metadata={'name':f'{LABELS[role]} prompt','role_id':role,'input_refs':images or []},run_id=run_id)
        task=AgentTask(id=new_id('task'),run_id=run_id,role_id=context['role_id'],role_label=LABELS[role],model=self.settings.reasoning_model,persona_id=context['persona_id'],scene_revision_id=inputs.get('scene',{}).get('id'),goal=goal,prompt_ref=prompt['id'],context_ref=context_art['id'],input_refs=images or [])
        record=task.model_dump(mode='json');record['dependency_ids']=[dependency] if dependency else []
        consumed=[]
        if dependency:
            parent=next(t for t in self.store.get_run(run_id)['tasks'] if t['id']==dependency)
            consumed=parent.get('result',{}).get('output_refs',[])
            record['input_refs']=[*consumed,*(images or [])]
        self.store.create_task(record)
        if dependency:
            self.store.upsert_record('handoffs',{'id':new_id('handoff'),'from_task_id':dependency,'to_task_id':task.id,'run_id':run_id,'summary':goal,'input_refs':record['input_refs'],'artifact_refs':record['input_refs'],'created_at':now_iso()},run_id=run_id)
        self.store.update_task(task.id,'running')
        try:
            text,artifact,job=await self.runner.text(messages,run_id=run_id,task_id=task.id,max_tokens=2400)
            self.store.update_task(task.id,'completed',result={'output_refs':[artifact['id']],'job_id':job['id']})
            return text,task.id,artifact
        except BaseException:
            self.store.update_task(task.id,'failed',result={'error':'Model call did not complete; inspect its job receipt.'})
            raise

    async def handle(self,raw):
        command=Command.model_validate(raw)
        op,payload=command.operation,command.payload
        if op in ('pause','resume'):return self.store.set_paused(op=='pause')
        if op=='cancel_job':return self.store.request_cancel(payload['job_id'])
        if op=='retire_unknown':return self.store.retire_unknown(payload['job_id'],actor='local-operator',reason=str(payload.get('reason','')))
        if op in ('create_reference_asset','generate_reference','approve_reference','import_reference','crop_reference'):
            from ghost_director.reference_workflow import handle_reference_command
            return await handle_reference_command(self,command)
        if op in ('update_shot_plan','render_motion','compile_shot','generate_package'):
            from ghost_director.shot_workflow import handle_shot_command
            current=SceneRevision.model_validate(self.store.scene())
            if command.expected_revision!=current.id:raise Conflict('Scene changed; inspect the current revision')
            return await handle_shot_command(self,command,current)
        if op in ('reconstruct_reference','place_reference','bind_reference','review_reconstruction'):
            from ghost_director.reference_reconstruction import reconstruct_reference,place_reference,bind_reference,review_reconstruction
            current=SceneRevision.model_validate(self.store.scene())
            if command.expected_revision!=current.id:raise Conflict('Scene changed; inspect the current revision')
            if op=='review_reconstruction':
                async with self.creative_lock:
                    return review_reconstruction(self.store,payload)
            if op=='reconstruct_reference':
                async with self.reconstruction_lock:
                    return await reconstruct_reference(self,payload)
            async with self.creative_lock:
                revised=(bind_reference if op=='bind_reference' else place_reference)(self.store,current,payload)
                return self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='local-operator')
        if op in ('apply_design','render_preview'):
            current=SceneRevision.model_validate(self.store.scene())
            if command.expected_revision!=current.id:raise Conflict('Scene changed since this command was submitted; inspect the current revision')
            if op=='apply_design':
                request=ApplyDesignRequest.model_validate(payload)
                revised=revise_room(current,request.room)
                if self.design_lock.locked():raise Conflict('A room compilation is already running')
                async with self.design_lock:
                    run=self.store.create_run(metadata={'name':'Room design','operation':'apply_design','scene_revision_id':current.id})
                    try:
                        self.store.append_event('design.compiling',{'source_revision_id':current.id,'proposal_revision_id':revised.id},run_id=run['id'])
                        # Local Blender I/O cannot lock normal editing. The store's
                        # CAS rejects a completed build if its captured head moved.
                        async with self.render_lock:
                            revised=await compile_room(self.store,self.settings,revised,run['id'])
                        async with self.creative_lock:
                            result=self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='local-operator',run_id=run['id'])
                        self.store.finish_run(run['id'])
                        return {**result,'run_id':run['id']}
                    except BaseException:
                        self.store.finish_run(run['id'],'failed');raise
            if set(payload)-{'camera_id','quality'}:raise ValueError('Unknown preview settings')
            camera_id=payload.get('camera_id')
            quality=payload.get('quality','draft')
            if quality not in ('draft','final'):raise ValueError('Preview quality must be draft or final')
            if camera_id not in {camera.id for camera in current.cameras}:raise ValueError('Preview camera does not exist in this scene')
            run=self.store.create_run(metadata={'name':'Local Blender preview','operation':'render_preview','scene_revision_id':current.id,'camera_id':camera_id,'quality':quality})
            try:
                self.store.append_event('preview.rendering',{'scene_revision_id':current.id,'camera_id':camera_id,'quality':quality},run_id=run['id'])
                shot=ShotPlan(id='preview',name='Scene preview',scene_revision_id=current.id,camera_id=camera_id)
                image,receipt=await self._render_local(current,shot,run['id'],quality=quality,render_kind='preview',require_cast=False)
                result={'artifact_id':image['id'],'receipt_artifact_id':receipt['id'],'scene_revision_id':current.id,'camera_id':camera_id,'quality':quality,'run_id':run['id']}
                self.store.append_event('preview.completed',{**result,'artifact_refs':[image['id'],receipt['id']]},run_id=run['id'])
                self.store.finish_run(run['id'])
                return result
            except BaseException:
                self.store.finish_run(run['id'],'failed');raise
        if op=='generate_image':
            prompt=str(payload.get('prompt','')).strip()
            if not 1<=len(prompt)<=12000:raise ValueError('Image prompt must contain 1–12000 characters')
            run=self.store.create_run(metadata={'name':'Original image','direction':prompt})
            try:
                artifact,_=await self.runner.image(prompt,run_id=run['id'])
                self.store.finish_run(run['id']);return {'artifact_id':artifact['id']}
            except BaseException:self.store.finish_run(run['id'],'failed');raise
        if op in ('direction','generate','repair_take'):
            # Freeze the creative input before slow I/O; editing remains live.
            # Acceptance still compares the exact source revision with the head.
            current=SceneRevision.model_validate(self.store.scene())
            if command.expected_revision!=current.id:raise Conflict('Scene changed since this command was submitted; inspect the current revision')
            if op=='direction':return await self.direct(payload,current)
            if self.generation_lock.locked():raise Conflict('A generation workflow is already running')
            async with self.generation_lock:
                if op=='generate':return await self.generate(payload['shot_id'],current,prompt_override=payload.get('prompt_override'))
                take=self.store.get_record('takes',payload['take_id'])
                if take['scene_revision_id']!=current.id:raise Conflict('Repair source scene is historical; restore or create a new shot')
                package=self.store.get_record('packages',take['package_id'])
                if package.get('workflow')=='reference-motion-v1':
                    raise Conflict('Reference-first takes require a revised shared plan, new Blender motion and a reviewed submission; legacy render-based repair is unavailable for this take')
                observations=[x for x in self.store.list_records('observations') if x['take_id']==take['id'] and x['status']!='pass']
                if not observations:raise ValueError('No recorded failure or uncertainty to repair')
                return await self.generate(take['shot_id'],current,repair=take,repair_notes=observations)
        async with self.creative_lock:
            current=SceneRevision.model_validate(self.store.scene())
            if command.expected_revision!=current.id:raise Conflict('Scene changed since this command was submitted; inspect the current revision')
            if op=='apply_scene':
                patches=[EntityPatch.model_validate(item) for item in payload.get('patches',[])]
                if not patches:raise ValueError('No scene change supplied')
                revised=apply_entity_patches(current,patches,revision_id=new_id('scene'))
                return self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='local-operator')
            if op=='update_camera':
                if set(payload)-{'camera_id','camera'}:raise ValueError('Unknown camera update fields')
                camera=next((item for item in current.cameras if item.id==payload.get('camera_id')),None)
                if camera is None:raise ValueError('Camera does not exist in this scene')
                changes=payload.get('camera')
                if not isinstance(changes,dict) or not changes or set(changes)&{'id','name'}:raise ValueError('Supply camera geometry without replacing its identity')
                updated=Camera.model_validate({**camera.model_dump(mode='json'),**changes})
                revised=current.model_copy(deep=True);revised.id=new_id('scene');revised.parent_id=current.id;revised.restored_from_id=None;revised.created_at=now_iso()
                revised.cameras=[updated if item.id==camera.id else item for item in revised.cameras]
                result=self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='local-operator')
                return {**result,'camera_id':camera.id}
            if op=='save_shot':
                if payload.get('workflow') not in (None,'reference-motion-v1'):
                    raise ValueError('Unknown shot workflow')
                camera=Camera(id=new_id('camera'),name=str(payload.get('name','Saved camera')),**payload['camera'])
                revised=current.model_copy(deep=True);revised.parent_id=current.id;revised.id=new_id('scene');revised.created_at=now_iso();revised.cameras.append(camera)
                revised.restored_from_id=None
                shot=ShotPlan(id=new_id('shot'),name=str(payload.get('name','Saved shot')),scene_revision_id=revised.id,camera_id=camera.id,duration=payload.get('duration',8 if self.settings.video_model in VEO_MODELS else 5),action=payload.get('action','Hold with natural performance.'))
                if payload.get('workflow')=='reference-motion-v1':
                    duration=payload.get('duration',8)
                    if not isinstance(duration,int) or isinstance(duration,bool) or not 4<=duration<=30:
                        raise ValueError('Shared shot duration must be an integer between 4 and 30 seconds')
                    shot.duration=duration
                    from ghost_director.domain.models import MotionPlan
                    shot.motion=MotionPlan(camera_keyframes=[{'time_seconds':second,'position':camera.position,'target':camera.target,'focal_length_mm':camera.focal_length_mm} for second in (0,duration)])
                    from ghost_director.motion import validate_motion
                    validate_motion(revised,shot)
                self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor='local-operator')
                return self.store.upsert_record('shots',shot.model_dump(mode='json'))
            if op in ('undo','restore_revision'):
                target_id=payload.get('revision_id') if op=='restore_revision' else self.store.snapshot()['undo_target_revision_id']
                if not target_id:raise ValueError('No previous scene revision' if op=='undo' else 'A source revision is required')
                previous=SceneRevision.model_validate(self.store.scene(target_id))
                previous.id=new_id('scene');previous.parent_id=current.id;previous.restored_from_id=target_id;previous.created_at=now_iso()
                result=self.store.publish_scene(previous.model_dump(mode='json'),expected_revision=current.id,actor='local-operator')
                self.store.append_event('revision.restored',{'revision_id':previous.id,'source_revision_id':target_id,'operation':op,'actor':'local-operator'})
                return result
            if op in ('accept_take','reject_take'):
                return self.decide_take(op,payload,current)
        raise ValueError('Unsupported command')

    def decide_take(self,op,payload,current,*,actor='local-operator'):
        take=self.store.get_record('takes',payload['take_id'])
        if take['status'] in ('accepted','rejected'):raise Conflict('Take already has a creative decision')
        package=self.store.get_record('packages',take['package_id'])
        if payload.get('package_id',package['id'])!=package['id']:raise Conflict('Take/package mismatch')
        if op=='reject_take':
            result=self.store.reject_take(take['id'],actor=actor,reason=str(payload.get('reason','')),expected_revision=current.id)
            take['status']='rejected'
        else:
            if package['scene_revision_id']!=current.id:raise Conflict('Take was generated from another scene revision; cannot apply its patch to this head')
            patches=[EntityPatch.model_validate(item) for item in package['proposed_patch']]
            revised=apply_entity_patches(current,patches,revision_id=new_id('scene'))
            result=self.store.publish_scene(revised.model_dump(mode='json'),expected_revision=current.id,actor=actor,take_id=take['id'],decision_reason=str(payload.get('reason','Accepted bounded scene patch')))
            take['status']='accepted'
        self.store.upsert_record('takes',take)
        if take['status']=='accepted':
            shot=self.store.get_record('shots',take['shot_id'])
            if shot:
                shot['status']='accepted';self.store.upsert_record('shots',shot)
        return result

    async def direct(self,payload,scene):
        direction=str(payload.get('text','')).strip()
        if not 1<=len(direction)<=12000:raise ValueError('Direction must contain 1–12000 characters')
        run=self.store.create_run(metadata={'name':'Direction','direction':direction})
        try:
            producer,producer_task,_=await self.role('producer','Interpret this director instruction as a bounded next shot. Return JSON with intent, action, constraints. Preserve the supplied cast, asset identities and room. Distinguish requested motion from unspecified creative choices.',{'direction':direction,'scene':scene.model_dump(mode='json')},run['id'])
            interpreted=object_json(producer)
            output,_,_=await self.role('dp','Propose one shot using an existing camera ID. Return JSON with name, camera_id, duration (integer seconds, 4–30), action, assertions (list of strings), proposed_patch (list of EntityPatch: entity_id and optional position/rotation/scale/material/state), motion (object). Patches describe the end of the action only. Motion format: {version:1,interpolation:linear,camera_keyframes:[{time_seconds:0,position:[x,y,z],target:[x,y,z],focal_length_mm:50},{time_seconds:DURATION,position:[x,y,z],target:[x,y,z],focal_length_mm:50}],tracks:[{entity_id:ID,keyframes:[{time_seconds:0,position:[x,y,z],rotation:[x,y,z]},{time_seconds:DURATION,position:[x,y,z],rotation:[x,y,z]}]}]}. Use quoted JSON keys and string values. DURATION is the explicitly requested duration, or 8 seconds when unspecified; return it as duration and use the same number throughout the motion timeline. Every first keyframe MUST exactly match the saved camera or entity transform, and every last keyframe must end at DURATION. Root translation/rotation only; do not invent limb rigging or physical simulation. Express articulated acting in action text and note it is generation-only. Respect explicit camera movement and action order; hold the camera if no movement requested. Keep one simple shot. Do not alter identity or room geography.',{'scene':scene.model_dump(mode='json'),'direction':direction,'producer':interpreted},run['id'],dependency=producer_task)
            proposal=object_json(output)
            duration=proposal.get('duration')
            if not isinstance(duration,int) or isinstance(duration,bool) or not 4<=duration<=30:
                raise ValueError('DP must return an explicit integer shot duration between 4 and 30 seconds')
            shot=ShotPlan(id=new_id('shot'),scene_revision_id=scene.id,duration=duration,reference_pins=payload.get('reference_pins',[]),**{k:proposal[k] for k in ('name','camera_id','action','assertions','proposed_patch','motion') if k in proposal})
            if shot.camera_id not in {x.id for x in scene.cameras}:raise ValueError('DP selected an unknown camera')
            if shot.motion:
                from ghost_director.motion import validate_motion
                validate_motion(scene,shot)
                shot.proposed_patch=[EntityPatch(entity_id=track.entity_id,position=track.keyframes[-1].position,rotation=track.keyframes[-1].rotation) for track in shot.motion.tracks]
            apply_entity_patches(scene,shot.proposed_patch,revision_id=new_id('validation'))
            self.store.upsert_record('shots',shot.model_dump(mode='json'),run_id=run['id'])
            self.store.finish_run(run['id']);return {'shot_id':shot.id,'run_id':run['id']}
        except BaseException:self.store.finish_run(run['id'],'failed');raise

    async def render(self,scene,shot,run_id):
        artifact,_=await self._render_local(scene,shot,run_id,quality='final',render_kind='reference',require_cast=True)
        return artifact

    async def _render_local(self,scene,shot,run_id,*,quality,render_kind,require_cast):
        actors=[x for x in scene.entities if x.kind=='actor']
        if require_cast and any(x.visible and (not x.asset_url or x.state.get('asset_status')!='available') for x in actors):
            raise ValueError('Visible character assets must pass scene integration before generating a production take')
        if not self.settings.blender:raise ValueError('Configure GHOST_BLENDER to render local previews')
        if shot.camera_id not in {camera.id for camera in scene.cameras}:raise ValueError('Render camera does not exist in this scene')
        samples,resolution=(16,640) if quality=='draft' else (48,1280)
        policy_files=('render_scene.py','lighting.py','build_set.py')
        render_policy={'version':1,'source_sha256':{name:hashlib.sha256((REPO/'ghost_director/blender'/name).read_bytes()).hexdigest() for name in policy_files}}
        blender_path=Path(self.settings.blender)
        if blender_path.is_file():
            stat=blender_path.stat();render_policy['blender_binary']={'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns}
        cache_key=hashlib.sha256(json.dumps({'scene':scene.model_dump(mode='json'),'camera_id':shot.camera_id,'quality':quality,'render_kind':render_kind,'samples':samples,'resolution':resolution,'render_policy':render_policy},sort_keys=True).encode()).hexdigest()[:24]
        directory=self.settings.root/'renders'/scene.id/cache_key
        target=directory/f'{shot.camera_id}.png'
        receipt_path=directory/'render-receipt.json'
        if not target.exists() or not receipt_path.exists():
            async with self.render_lock:
                if not target.exists() or not receipt_path.exists():
                    directory.mkdir(parents=True,exist_ok=True)
                    source=directory/'scene.json';source.write_text(scene.model_dump_json(indent=2))
                    frozen=directory/'assets';frozen.mkdir(exist_ok=True)
                    room=next(e for e in scene.entities if e.kind=='room')
                    blend_id=room.state.get('blend_artifact_id')
                    if not blend_id:raise ValueError('Scene lacks a pinned Blender source; sync assets first')
                    shutil.copyfile(self.store.artifact_path(blend_id),frozen/'dinner-set.blend')
                    mapping={}
                    geometry=[x for x in scene.entities if x.kind not in ('room','light') and (x.kind=='actor' or x.state.get('artifact_id'))]
                    for actor in geometry:
                        artifact_id=actor.state.get('artifact_id')
                        if not artifact_id:
                            if not actor.visible:continue
                            raise ValueError('Visible actor lacks immutable source geometry')
                        filename=actor.id+'.glb'
                        shutil.copyfile(self.store.artifact_path(artifact_id),frozen/filename)
                        mapping[actor.asset_id]=filename
                    (frozen/'asset-map.json').write_text(json.dumps(mapping))
                    await process(self.settings.blender,'--disable-autoexec','-b','--python',REPO/'ghost_director/blender/render_scene.py','--','--scene',source,'--output',directory,'--camera-id',shot.camera_id,'--asset-root',frozen,'--samples',samples,'--resolution',resolution,timeout=240)
        receipt_data=json.loads(receipt_path.read_text())
        rendered=next((item for item in receipt_data.get('renders',[]) if item.get('camera_id')==shot.camera_id),None)
        if not rendered or rendered.get('sha256')!=hashlib.sha256(target.read_bytes()).hexdigest():raise ValueError('Rendered image does not match its Blender receipt')
        metadata={'name':shot.name+' · Blender '+render_kind,'provider':'blender','model':'Blender '+receipt_data.get('blender_version','unknown'),'scene_revision_id':scene.id,'camera_id':shot.camera_id,'render_kind':render_kind,'quality':quality,'samples':samples,'width':rendered.get('width'),'height':rendered.get('height'),'cache_key':cache_key,'render_policy':render_policy}
        receipt=self.store.publish_artifact({**receipt_data,'render_policy':render_policy,'render_kind':render_kind,'requested_quality':quality},kind='render_receipt',metadata={'name':'Blender render receipt',**{key:metadata[key] for key in ('scene_revision_id','camera_id','quality','render_kind')}},run_id=run_id)
        artifact=self.store.publish_artifact(source_path=target,kind='image',mime_type='image/png',metadata={**metadata,'receipt_artifact_id':receipt['id']},run_id=run_id)
        return artifact,receipt

    def compact_references(self, references, run_id):
        prepared=[]
        for source_id in references:
            with Image.open(self.store.artifact_path(source_id)) as original:
                image=original.convert('RGB')
                image.thumbnail((1536,1536),Image.Resampling.LANCZOS)
                output=BytesIO();image.save(output,format='JPEG',quality=92,optimize=True)
            artifact=self.store.publish_artifact(output.getvalue(),kind='image',mime_type='image/jpeg',metadata={'name':'Submission reference','source_artifact_id':source_id,'transform':'JPEG quality92; longest edge <=1536px','width':image.width,'height':image.height},run_id=run_id)
            prepared.append(artifact['id'])
        return prepared

    async def generate(self,shot_id,scene,*,prompt_override=None,repair=None,repair_notes=None):
        shot=ShotPlan.model_validate(self.store.get_record('shots',shot_id))
        if shot.motion or shot.reference_pins:
            raise Conflict('This reference-first shot must use its shared plan: render Blender motion, prepare the submission and approve its exact photographic references before generation')
        if SCENE_ASSERTION not in shot.assertions:shot.assertions.append(SCENE_ASSERTION)
        if self.settings.video_model in VEO_MODELS and shot.duration!=8:
            raise ValueError('Veo3.1 reference shots must be authored as 8 seconds')
        shot.scene_revision_id=scene.id
        run=self.store.create_run(metadata={'name':shot.name,'shot_id':shot.id,'source_scene_revision_id':scene.id})
        try:
            reference=await self.render(scene,shot,run['id'])
            refs=[reference['id']]
            endpoints=self.settings.video_model=='fal-ai/veo3.1/first-last-frame-to-video'
            proposed_end_state=None
            if endpoints:
                # A renderable proposal is not a published/accepted scene revision.
                target=apply_entity_patches(scene,shot.proposed_patch,revision_id=new_id('proposal'))
                proposed_end_state=target.model_dump(mode='json')
                # An unchanged endpoint reuses the exact first image instead of
                # repeating an identical Cycles render and geometry copy.
                last_frame=await self.render(target,shot,run['id']) if shot.proposed_patch else reference
                refs.append(last_frame['id'])
            # The approved scene render is always first. Actor originals may
            # supplement identity but never replace camera/geometry evidence.
            for actor in (x for x in scene.entities if x.kind=='actor'):
                ref=actor.state.get('reference_artifact_id')
                if ref:refs.append(ref)
            refs=self.compact_references(refs,run['id'])
            anchored=self.settings.video_model=='fal-ai/veo3.1/image-to-video'
            guidance=('Only the first attached image will be supplied to the video model as its exact first frame. The later original actor images inform your descriptions only. Describe the characters by screen position and wardrobe; do not mention absent additional reference images. Animate the supplied composition without redesigning the room, furniture, lighting, characters or materials.' if anchored else 'Reference assets are @Image1, @Image2 etc in exactly attached order. @Image1 defines exact room, camera, actor positions, and prop placement; later images only define the named actor identity. The output should be photographic live-action cinema while preserving reference geography.')
            if endpoints:
                guidance='The first TWO attached images are the exact START and END Blender frames sent to the video model. Later actor images inform descriptions only and are not video attachments. Describe a physically plausible single action connecting these endpoint compositions. The second frame is a proposed end state, not an observed result. Keep the camera and all unchanged geometry fixed; preserve object counts and exact landing/contact positions. Do not mention absent additional reference images.'
            prompt,task_id,prompt_art=await self.role('planner',f'Write only a complete {self.settings.video_model} video prompt for the single shot. '+guidance+' Separate camera from subject action. Preserve all asserted continuity. Use a locked camera unless the shot explicitly requests a move. No unexplained additions or output parameter syntax.',{'scene':scene.model_dump(mode='json'),'shot':shot.model_dump(mode='json'),'ordered_reference_ids':refs,'operator_prompt_override':prompt_override,'repair_notes':repair_notes},run['id'],images=refs)
            if prompt_override:
                prompt=str(prompt_override)
                prompt_art=self.store.publish_artifact(prompt,kind='prompt',mime_type='text/plain',metadata={'name':'Operator revised generation prompt','source':'local-operator'},run_id=run['id'])
            if not 1<=len(prompt)<=16000:raise ValueError('Generation prompt outside bounded length')
            submitted_refs=refs[:2] if endpoints else refs[:1] if anchored else refs
            package={'id':new_id('package'),'run_id':run['id'],'shot_id':shot.id,'scene_revision_id':scene.id,'camera_id':shot.camera_id,'prompt_ref':prompt_art['id'],'prompt':prompt,'reference_ids':submitted_refs,'planning_reference_ids':refs,'conditioning_mode':'first_last_frame' if endpoints else 'first_frame' if anchored else 'references','proposed_patch':[x.model_dump(mode='json',exclude_none=True) for x in shot.proposed_patch],'assertions':shot.assertions,'duration':shot.duration,'model':self.settings.video_model,'created_at':now_iso()}
            if proposed_end_state is not None:package['proposed_end_state']=proposed_end_state
            self.store.upsert_record('packages',package,run_id=run['id'])
            self.store.upsert_record('shots',shot.model_dump(mode='json'),run_id=run['id'])
            video,job=await self.runner.video(prompt,submitted_refs,duration=shot.duration,run_id=run['id'],root_id=self.store.get_job(repair['job_id'])['root_id'] if repair else None,automatic_repair=bool(repair))
            take=await self.finish_take(video,job,package,scene,task_id)
            self.store.finish_run(run['id']);return {'take_id':take['id'],'run_id':run['id']}
        except BaseException:self.store.finish_run(run['id'],'failed');raise

    async def finish_take(self,video,job,package,scene,planner_task=None):
        metadata,frames=await video_evidence(self.store.artifact_path(video['id']),self.settings.root/'evidence'/job['id'])
        evidence=[]
        for frame in frames:
            artifact=self.store.publish_artifact(source_path=frame['path'],kind='frame',mime_type='image/jpeg',metadata={'name':f"Video frame · {frame['time_ms']} ms",'time_ms':frame['time_ms'],'video_id':video['id']},run_id=package['run_id'])
            evidence.append(artifact)
        take=Take(id=new_id('take'),shot_id=package['shot_id'],scene_revision_id=package['scene_revision_id'],package_id=package['id'],job_id=job['id'],artifact_id=video['id'],poster_id=evidence[0]['id'],run_id=package['run_id'],proposed_patch=package['proposed_patch'])
        self.store.upsert_record('takes',take.model_dump(mode='json'),run_id=package['run_id'])
        comparison_refs=package.get('planning_reference_ids',package['reference_ids'])
        review_refs=[x['id'] for x in evidence]+comparison_refs
        reference_roles=['reference_scene','proposed_endpoint'] if package.get('conditioning_mode')=='first_last_frame' else ['reference_scene']
        reference_roles+=['reference_eren','reference_leyla']
        review_inputs={'scene':scene.model_dump(mode='json'),'assertions':package['assertions'],'video_metadata':metadata,'sample_time_ms':[f['time_ms'] for f in frames]}
        review_guidance='A proposed_endpoint image is the desired landing state, not evidence that the video achieved it; compare the actual last output frame against it. Compare visible identity against original character references, wardrobe appearance under the actual lighting against the Blender reference, and geography/props against the Blender scene.'
        if package.get('workflow')=='reference-motion-v1':
            reference_roles=[f"approved_identity:{entry['name']}:{entry['slot']}" for entry in package['reference_mapping']]
            review_inputs.update(reference_mapping=package['reference_mapping'],
                                 entity_reference_bindings=package.get('entity_reference_bindings',[]),
                                 motion_plan=package.get('shot_plan',{}).get('motion'),
                                 appearance_coverage_warnings=package.get('appearance_coverage_warnings',[]))
            review_guidance=('Later attached images are approved appearance reference images, mapped to scene entities by reference_mapping and entity_reference_bindings. '
                             'Compare visible identity, wardrobe, architecture and props only against their corresponding approved references, allowing for actual shot lighting and viewpoint. '
                             'No Blender appearance frame or proposed endpoint is attached. The technical motion video is not attached to this review. '
                             'The supplied scene, entity bindings and motion_plan describe planned constraints, not evidence of generated pixels. '
                             'Use them to understand the intended subject, blocking and camera action; do not claim to have watched the technical motion video or verified continuous motion from these still samples. '
                             'Appearance coverage warnings identify authored entities without approved appearance images; their reference fidelity is UNKNOWN. '
                             'Root motion constraints do not prove articulated acting, physical contact or temporal continuity.')
        image_roles=['output_first','output_middle','output_last']+reference_roles[:len(comparison_refs)]
        review_inputs['ordered_image_roles']=image_roles
        review_goal=('The first three attached images are actual video frames in chronological order. Later image roles are explicitly listed in ordered_image_roles. '
                     +review_guidance+' Do not merely compare output frames against each other. Assess each assertion independently. Offscreen people or objects are UNKNOWN, never FAIL merely because they are not visible. Return JSON {"observations":[{"assertion":"exact supplied assertion","status":"pass|fail|unknown","detail":"visible evidence and limits","frame_index":0}]}. frame_index indexes only the three output frames, starting at zero. A few still frames cannot prove temporal continuity or every frame; mark unobservable properties unknown. Never invent measurements or use scene coordinates as proof of generated pixels.')
        output,_,_=await self.role('supervisor',review_goal,review_inputs,package['run_id'],images=review_refs,dependency=planner_task)
        result=object_json(output)
        proposed=result.get('observations',[])
        for assertion in package['assertions']:
            item=next((x for x in proposed if isinstance(x,dict) and x.get('assertion')==assertion),None)
            index=item.get('frame_index',0) if item else 0
            if not isinstance(index,int) or not 0<=index<len(evidence):index=0
            observation=Observation(id=new_id('obs'),take_id=take.id,assertion=assertion,status=item.get('status','unknown') if item else 'unknown',detail=item.get('detail','Model did not return this assertion') if item else 'Model did not return this assertion',time_ms=frames[index]['time_ms'],evidence_ids=[evidence[index]['id'],*comparison_refs],method='meta-vision-sampled-frames-and-references')
            self.store.upsert_record('observations',observation.model_dump(mode='json'),run_id=package['run_id'])
        take.status='reviewed';record=take.model_dump(mode='json');self.store.upsert_record('takes',record,run_id=package['run_id'])
        return record

    async def resume(self,recovery):
        for job in recovery.get('materializable_jobs',[]):
            try:
                package=next((p for p in self.store.list_records('packages') if p['run_id']==job['run_id']),None)
                if package:
                    video=self.store.artifact(job['result_refs'][0])
                    await self.finish_take(video,job,package,SceneRevision.model_validate(self.store.scene(package['scene_revision_id'])))
                    self.store.finish_run(job['run_id'])
            except Exception as error:
                self.store.append_event('recovery.failed',{'job_id':job['id'],'error':str(error)},job_id=job['id'])
        for job in recovery['resumable_jobs']:
            try:
                if job.get('run_id') and self.store.get_run(job['run_id']).get('operation')=='reference_reconstruction':continue
                if job.get('category')=='video':
                    video,completed=await (self.runner.poll_fal_video(job) if job['provider']=='fal' else self.runner.poll_video(job))
                    package=next((p for p in self.store.list_records('packages') if p['run_id']==job['run_id']),None)
                    if package and not any(t['job_id']==job['id'] for t in self.store.list_records('takes')):
                        await self.finish_take(video,completed,package,SceneRevision.model_validate(self.store.scene(package['scene_revision_id'])))
                elif job['provider']=='fal':await self.runner.poll_fal(job)
                if job.get('run_id'): self.store.finish_run(job['run_id'])
                self.store.append_event('recovery.completed',{'job_id':job['id']},job_id=job['id'])
            except Exception as error:
                self.store.append_event('recovery.failed',{'job_id':job['id'],'error':str(error)},job_id=job['id'])
        from ghost_director.reference_reconstruction import recover_reconstructions
        async with self.reconstruction_lock:
            await recover_reconstructions(self)
