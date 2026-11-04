"""Every billable attempt is reserved and durably claimed before its only POST."""
from __future__ import annotations
import asyncio
import base64
from io import BytesIO
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from ghost_director.config import Settings,VEO_MODELS
from ghost_director.domain.models import new_id
from ghost_director.jobs.store import ProductionStore
from ghost_director.providers.http import ProviderHTTP, ProviderError, data_uri
from ghost_director.providers.reference_video import REFERENCE_VIDEO_MODEL, compile_reference_video, estimate_reference_cost, probe_video


class ProviderRunner:
    def __init__(self, store: ProductionStore, settings: Settings, http=None):
        self.store, self.settings = store, settings
        self.http = http or ProviderHTTP(settings)
        self.meta_slots = asyncio.Semaphore(2)

    def artifact(self, data, *, kind, mime, provider, model, name, run_id=None, task_id=None, **metadata):
        return self.store.publish_artifact(data,kind=kind,mime_type=mime,metadata={'provider':provider,'model':model,'name':name,**metadata},run_id=run_id,task_id=task_id)

    def prepare(self, provider, model, payload, category, reserve, *, run_id=None,task_id=None,root_id=None,automatic_repair=False,pricing_estimate=None):
        if not self.settings.allow_live:
            raise ValueError('Live calls are disabled')
        request_artifact=self.artifact(payload,kind='request',mime='application/json',provider=provider,model=model,name=f'{model} request',run_id=run_id,task_id=task_id)
        job={'id':new_id('job'),'provider':provider,'model':model,'run_id':run_id,'task_id':task_id,'request_ref':request_artifact['id']}
        if pricing_estimate is not None: job['pricing_estimate']=pricing_estimate
        if root_id: job['root_id']=root_id
        job=self.store.create_job(job,automatic_repair=automatic_repair)
        self.store.reserve(job['id'],category,reserve)
        try:
            self.store.begin_submission(job['id'])
        except Exception:
            self.store.request_cancel(job['id'])
            raise
        return job

    async def post(self, job, url, payload, *, sse=False):
        start=time.monotonic()
        try:
            result=await self.http.request(job['provider'],'POST',url,payload,sse=sse)
        except ProviderError as error:
            if error.uncertain:
                self.store.mark_submission_unknown(job['id'],error=str(error))
            else:
                self.store.finish_job(job['id'],'failed',cost_usd=0,cost_basis='estimated',error=str(error))
            raise
        except BaseException:
            self.store.mark_submission_unknown(job['id'],error='Submission interrupted; no automatic retry')
            raise
        self.store.append_event('provider.response',{'provider':job['provider'],'model':job['model'],'elapsed_seconds':round(time.monotonic()-start,3)},job_id=job['id'],run_id=job.get('run_id'),task_id=job.get('task_id'))
        return result

    async def text(self, messages, *, run_id=None,task_id=None,max_tokens=2200):
        model=self.settings.reasoning_model
        payload={'model':model,'messages':messages,'max_completion_tokens':max_tokens,'reasoning_effort':'minimal'}
        # Text length bounds input; images conservatively reserve additional tokens.
        encoded=json.dumps(messages)
        import re
        image_count=encoded.count('data:image/')
        text_only=re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+','[image]',encoded)
        reserve=(len(text_only)/2*4+max_tokens*20+image_count*65536*4)/1e6 + .1
        async with self.meta_slots:
            job=self.prepare('meta',model,payload,'meta',reserve,run_id=run_id,task_id=task_id)
            data=await self.post(job,'https://api.meta.ai/v1/chat/completions',payload)
            try:
                content=data['choices'][0]['message']['content']
                if not isinstance(content,str): raise ValueError('Missing assistant text')
                usage=data.get('usage',{})
                cost=(usage.get('prompt_tokens',0)*4+usage.get('completion_tokens',0)*20)/1e6 if usage else reserve
                artifact=self.artifact(content,kind='text',mime='text/plain',provider='meta',model=model,name='Meta response',run_id=run_id,task_id=task_id,usage=usage)
                self.store.finish_job(job['id'],result_refs=[artifact['id']],cost_usd=cost,cost_basis='estimated',receipt={'id':data.get('id'),'usage':usage})
                return content,artifact,job
            except Exception:
                self.store.finish_job(job['id'],'failed',error='Malformed Meta response; inspect request',cost_usd=None)
                raise

    async def image(self, prompt, *, run_id=None,task_id=None,name='Original image'):
        model=self.settings.image_model
        payload={'model':model,'prompt':prompt,'n':1,'size':'1536x1024','response_format':'b64_json','output_format':'png','tool_enablement':{'enable_image_search':False,'enable_web_search':False,'enable_shell':False}}
        async with self.meta_slots:
            job=self.prepare('meta',model,payload,'meta',.02,run_id=run_id,task_id=task_id)
            data=await self.post(job,'https://api.meta.ai/v1/images/generations',payload)
            try:
                raw=base64.b64decode(data['data'][0]['b64_json'],validate=True)
                with Image.open(BytesIO(raw)) as image: image.verify()
                artifact=self.artifact(raw,kind='image',mime='image/png',provider='meta',model=model,name=name,run_id=run_id,task_id=task_id)
                self.store.finish_job(job['id'],result_refs=[artifact['id']],cost_usd=.01,cost_basis='estimated',receipt={'created':data.get('created')})
                return artifact,job
            except Exception:
                self.store.finish_job(job['id'],'failed',error='Malformed image result',cost_usd=.01,cost_basis='estimated')
                raise

    async def segment(self, image_id, *, run_id=None,task_id=None):
        from ghost_director.providers.segmentation import decode_person_mask
        path=self.store.artifact_path(image_id)
        model=self.settings.segmentation_model
        payload={'model':model,'input':[{'type':'message','role':'user','content':[{'type':'input_text','text':'person'},{'type':'input_image','image_url':data_uri(path)}]}],'stream':True,'metadata':{'mask_encoding':'one_bit'}}
        job=self.prepare('meta',model,payload,'meta',.005,run_id=run_id,task_id=task_id)
        events=await self.post(job,'https://api.meta.ai/v1/responses',payload,sse=True)
        if not any(e.get('type')=='response.completed' for e in events):
            self.store.finish_job(job['id'],'failed',error='SAM stream incomplete',cost_usd=None)
            raise ProviderError('SAM stream incomplete')
        text=''.join(e.get('delta','') for e in events if e.get('type')=='response.output_text.delta')
        response=self.artifact(text,kind='segmentation',mime='text/plain',provider='meta',model=model,name='SAM segmentation output',run_id=run_id,task_id=task_id,input_refs=[image_id])
        try:
            with Image.open(path) as source: mask=decode_person_mask(text,source.size)
            artifact=self.artifact(mask,kind='mask',mime='image/png',provider='meta',model=model,name='Registered person mask',run_id=run_id,task_id=task_id,input_refs=[image_id,response['id']])
            self.store.finish_job(job['id'],result_refs=[response['id'],artifact['id']],cost_usd=.0025,cost_basis='estimated')
            return artifact,job
        except Exception:
            self.store.finish_job(job['id'],'failed',result_refs=[response['id']],cost_usd=.0025,cost_basis='estimated',error='SAM mask registration failed')
            raise

    async def reconstruct(self, image_id,mask_id=None,*,run_id=None,task_id=None,body=False,prompt='person'):
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=200:
            raise ValueError('Reconstruction requires a bounded foreground description')
        model='fal-ai/sam-3/3d-body' if body else 'fal-ai/sam-3/3d-objects'
        payload={'image_url':data_uri(self.store.artifact_path(image_id))}
        if body:
            payload.update(export_meshes=True,include_3d_keypoints=False,include_mhr_params=False)
            if mask_id: payload['mask_url']=data_uri(self.store.artifact_path(mask_id))
        else:
            payload.update(export_textured_glb=True,enable_safety_checker=True,seed=17)
            if mask_id: payload['mask_urls']=[data_uri(self.store.artifact_path(mask_id))]
            else: payload['prompt']=prompt.strip()
        job=self.prepare('fal',model,payload,'assets_3d',.1,run_id=run_id,task_id=task_id)
        data=await self.post(job,'https://queue.fal.run/'+model,payload)
        if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not data['request_id'] or not isinstance(data.get('status_url'),str) or not data['status_url'].startswith('https://queue.fal.run/'):
            self.store.mark_submission_unknown(job['id'],error='fal accepted without usable job locator')
            raise ProviderError('fal response missing job locator',uncertain=True)
        self.store.record_submission(job['id'],data['request_id'],polling_locator=data['status_url'])
        return await self.poll_fal(job|{'remote_id':data['request_id'],'polling_locator':data['status_url']})

    async def meshy(self,image_id,mask_id,*,run_id=None):
        # This measured fallback starts from the original Meta image and actual
        # SAM foreground. Its model provenance remains visible beside SAM3D.
        source=Image.open(self.store.artifact_path(image_id)).convert('RGBA')
        mask=Image.open(self.store.artifact_path(mask_id)).convert('L')
        if source.size!=mask.size:raise ValueError('SAM mask dimensions differ from original image')
        source.putalpha(mask);buffer=BytesIO();source.save(buffer,format='PNG')
        model='meshy/v7.1/image-to-3d'
        isolated=self.artifact(buffer.getvalue(),kind='image',mime='image/png',provider='meta',model='sam-3.1',name='SAM isolated original actor',run_id=run_id,input_refs=[image_id,mask_id])
        payload={'image_url':data_uri(self.store.artifact_path(isolated['id'])),'model_type':'standard','topology':'triangle','target_polycount':100000,'symmetry_mode':'off','should_remesh':True,'should_texture':True,'enable_pbr':True,'pose_mode':'','geometry_resolution':'2k','enable_rigging':False,'enable_animation':False,'enable_safety_checker':True}
        job=self.prepare('fal',model,payload,'assets_3d',2,run_id=run_id)
        data=await self.post(job,'https://queue.fal.run/'+model,payload)
        if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not data['request_id'] or not isinstance(data.get('status_url'),str) or not data['status_url'].startswith('https://queue.fal.run/'):
            self.store.mark_submission_unknown(job['id'],error='Meshy accepted without usable job locator')
            raise ProviderError('Meshy response missing job locator',uncertain=True)
        self.store.record_submission(job['id'],data['request_id'],polling_locator=data['status_url'])
        return await self.poll_fal(job|{'remote_id':data['request_id'],'polling_locator':data['status_url']})

    async def _fal_completed_result(self, job, response_url):
        """A completed queue entry can still carry a permanent result error.

        Only a 400/422 result response establishes that terminal rejection.
        Transient result GET failures keep the known remote ID resumable; no
        result-fetch outcome authorizes another paid POST or assumes zero cost.
        """
        try:
            return await self.http.request('fal','GET',response_url)
        except ProviderError as error:
            if error.status in (400,422):
                self.store.finish_job(job['id'],'failed',cost_usd=None,error=str(error),receipt={
                    'request_id':job['remote_id'],'queue_status':'COMPLETED','result_http_status':error.status,
                })
            raise

    async def poll_fal(self, job):
        locator=job.get('polling_locator') or f"https://queue.fal.run/fal-ai/sam-3/requests/{job['remote_id']}/status"
        deadline=time.monotonic()+1200
        while time.monotonic()<deadline:
            data=await self.http.request('fal','GET',locator)
            status=data.get('status')
            if status=='COMPLETED':
                response_url=data.get('response_url') or locator.removesuffix('/status')
                result=await self._fal_completed_result(job,response_url)
                models=[]
                if isinstance(result.get('model_glb'),dict): models=[result['model_glb']]
                if not models:
                    for item in result.get('meshes',[]):
                        if isinstance(item,dict) and item.get('url'): models.append(item)
                if not models:
                    self.store.finish_job(job['id'],'failed',error='fal returned no GLB',cost_usd=None,cost_basis='estimated')
                    raise ProviderError('fal returned no GLB')
                raw=await self.http.download(models[0]['url'])
                if raw[:4]!=b'glTF': raise ProviderError('fal artifact is not GLB')
                artifact=self.artifact(raw,kind='model',mime='model/gltf-binary',provider='fal',model=job['model'],name='Meshy quality comparison' if 'meshy/' in job['model'] else 'SAM 3D reconstruction',run_id=job.get('run_id'),task_id=job.get('task_id'))
                self.store.finish_job(job['id'],result_refs=[artifact['id']],cost_usd=1.2 if 'meshy/' in job['model'] else .02,cost_basis='estimated',receipt={'request_id':job['remote_id']})
                return artifact,job
            if status in ('FAILED','CANCELLED'):
                self.store.finish_job(job['id'],'failed',error='fal job '+status,cost_usd=None)
                raise ProviderError('fal job '+status)
            if status=='IN_PROGRESS': self.store.set_job_running(job['id'])
            await asyncio.sleep(3)
        raise TimeoutError('fal polling paused after 20 minutes; remote ID remains resumable')

    async def video(self,prompt,reference_ids,*,duration=None,resolution=None,run_id=None,task_id=None,root_id=None,automatic_repair=False,model_override=None,video_reference_ids=None):
        if not reference_ids: raise ValueError('Video requires actual ordered image references')
        if model_override is not None and model_override not in (self.settings.video_model,REFERENCE_VIDEO_MODEL):
            raise ValueError('Unvalidated video model override')
        model=model_override or self.settings.video_model
        if video_reference_ids and model!=REFERENCE_VIDEO_MODEL:
            raise ValueError('Selected video route does not support motion-video inputs')
        duration=duration if duration is not None else 8 if model in VEO_MODELS else 5
        resolution=resolution or ('720p' if model in VEO_MODELS else '480p')
        if model==REFERENCE_VIDEO_MODEL:
            payload,pricing=await compile_reference_video(self.store,prompt,reference_ids,video_reference_ids or [],duration=duration,resolution=resolution)
            job=self.prepare('fal',model,payload,'video',pricing['reserve_usd'],run_id=run_id,task_id=task_id,root_id=root_id,automatic_repair=automatic_repair,pricing_estimate=pricing)
            data=await self.post(job,'https://queue.fal.run/'+model,payload)
            if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not data['request_id'] or not isinstance(data.get('status_url'),str) or not data['status_url'].startswith('https://queue.fal.run/'):
                self.store.mark_submission_unknown(job['id'],error='fal accepted reference video without job locator')
                raise ProviderError('fal reference video response missing job locator',uncertain=True)
            self.store.record_submission(job['id'],data['request_id'],polling_locator=data['status_url'])
            return await self.poll_fal_video(job|{'remote_id':data['request_id'],'polling_locator':data['status_url']})
        if model in VEO_MODELS:
            if resolution not in ('720p','1080p') or duration!=8 or len(reference_ids)>3:
                raise ValueError('Validated Veo3.1 modes require 8 seconds, 720p/1080p, and at most three images')
            if model=='fal-ai/veo3.1/image-to-video':
                if len(reference_ids)!=1:
                    raise ValueError('Veo3.1 image-to-video requires exactly one first-frame image')
                if self.store.artifact_path(reference_ids[0]).stat().st_size>8_000_000:
                    raise ValueError('Veo3.1 image-to-video input exceeds 8 MB')
            if model=='fal-ai/veo3.1/first-last-frame-to-video':
                if len(reference_ids)!=2:
                    raise ValueError('Veo3.1 first-last-frame requires exactly two ordered endpoint images')
                if any(self.store.artifact_path(ref).stat().st_size>8_000_000 for ref in reference_ids):
                    raise ValueError('Veo3.1 endpoint image exceeds 8 MB')
            reserve=duration*.20*1.2
        elif model=='bytedance/seedance-2.5':
            if resolution not in ('480p','720p') or duration not in range(4,31): raise ValueError('Unsupported video parameters')
            reserve=(1280*720 if resolution=='720p' else 854*480)*((duration*24)+2)/1024*.0000107*1.2
        else:raise ValueError('Unvalidated video model')
        refs=[{'type':'image_url','image_url':{'url':data_uri(self.store.artifact_path(ref),self.store.artifact(ref)['mime_type'])}} for ref in reference_ids]
        if model in VEO_MODELS and model.startswith('fal-ai/'):
            payload={'prompt':prompt,'duration':'8s','resolution':resolution,'aspect_ratio':'16:9','generate_audio':False,'auto_fix':False}
            if model=='fal-ai/veo3.1/image-to-video':payload['image_url']=refs[0]['image_url']['url']
            elif model=='fal-ai/veo3.1/first-last-frame-to-video':
                payload['first_frame_url']=refs[0]['image_url']['url']
                payload['last_frame_url']=refs[1]['image_url']['url']
            else:payload['image_urls']=[x['image_url']['url'] for x in refs]
            job=self.prepare('fal',model,payload,'video',reserve,run_id=run_id,task_id=task_id,root_id=root_id,automatic_repair=automatic_repair)
            data=await self.post(job,'https://queue.fal.run/'+model,payload)
            if not isinstance(data,dict) or not isinstance(data.get('request_id'),str) or not data['request_id'] or not isinstance(data.get('status_url'),str) or not data['status_url'].startswith('https://queue.fal.run/'):
                self.store.mark_submission_unknown(job['id'],error='fal accepted video without job locator')
                raise ProviderError('fal video response missing job locator',uncertain=True)
            self.store.record_submission(job['id'],data['request_id'],polling_locator=data['status_url'])
            return await self.poll_fal_video(job|{'remote_id':data['request_id'],'polling_locator':data['status_url']})
        payload={'model':model,'prompt':prompt,'input_references':refs,'duration':duration,'resolution':resolution,'aspect_ratio':'16:9','generate_audio':False,'session_id':run_id or new_id('video-session')}
        job=self.prepare('openrouter',model,payload,'video',reserve,run_id=run_id,task_id=task_id,root_id=root_id,automatic_repair=automatic_repair)
        data=await self.post(job,'https://openrouter.ai/api/v1/videos',payload)
        if not isinstance(data,dict) or not isinstance(data.get('id'),str) or not data['id'] or ('polling_url' in data and not isinstance(data['polling_url'],str)):
            self.store.mark_submission_unknown(job['id'],error='OpenRouter accepted without job ID')
            raise ProviderError('OpenRouter response missing job ID',uncertain=True)
        locator=data.get('polling_url') or 'https://openrouter.ai/api/v1/videos/'+data['id']
        self.store.record_submission(job['id'],data['id'],polling_locator=locator)
        return await self.poll_video(job|{'remote_id':data['id'],'polling_locator':locator,'reserved_usd':reserve})

    async def poll_fal_video(self,job):
        locator=job['polling_locator'];deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            data=await self.http.request('fal','GET',locator)
            status=data.get('status')
            if status=='COMPLETED':
                result=await self._fal_completed_result(job,data.get('response_url') or locator.removesuffix('/status'))
                video=result.get('video',{})
                if not isinstance(video,dict) or not video.get('url'):
                    self.store.finish_job(job['id'],'failed',cost_usd=None,error='Completed fal video omitted artifact')
                    raise ProviderError('Completed fal video omitted artifact')
                raw=await self.http.download(video['url'])
                if len(raw)<12 or raw[4:8]!=b'ftyp':raise ProviderError('fal video output is not MP4')
                metadata={}
                cost=1.6
                pricing='8s x $0.20/s; no audio'
                if job['model']==REFERENCE_VIDEO_MODEL:
                    estimate=job.get('pricing_estimate')
                    if not isinstance(estimate,dict) or 'input_video_seconds' not in estimate or 'resolution' not in estimate:
                        raise ProviderError('Reference video pricing evidence is missing; reservation retained')
                    with tempfile.TemporaryDirectory(prefix='gdr-video-',dir=self.settings.root) as temporary:
                        path=Path(temporary)/'output.mp4';path.write_bytes(raw)
                        metadata=await probe_video(path)
                    cost=estimate_reference_cost(metadata['width'],metadata['height'],metadata['duration'],estimate['input_video_seconds'],resolution=estimate['resolution'])
                    pricing={**estimate,'estimated_usd':cost,'actual_output_dimensions':[metadata['width'],metadata['height']],'actual_output_seconds':metadata['duration']}
                artifact=self.artifact(raw,kind='video',mime='video/mp4',provider='fal',model=job['model'],name='Reference-guided take' if job['model']==REFERENCE_VIDEO_MODEL else 'Veo3.1 take',run_id=job.get('run_id'),task_id=job.get('task_id'),**metadata)
                self.store.finish_job(job['id'],result_refs=[artifact['id']],cost_usd=cost,cost_basis='estimated',receipt={'request_id':job['remote_id'],'pricing':pricing})
                return artifact,job
            if status in ('FAILED','CANCELLED'):
                self.store.finish_job(job['id'],'failed',cost_usd=None,error='fal video '+status)
                raise ProviderError('fal video '+status)
            if status=='IN_PROGRESS':self.store.set_job_running(job['id'])
            await asyncio.sleep(3)
        raise TimeoutError('Video polling paused after 30 minutes; remote ID remains resumable')

    async def poll_video(self, job):
        locator=job.get('polling_locator') or 'https://openrouter.ai/api/v1/videos/'+job['remote_id']
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            data=await self.http.request('openrouter','GET',locator)
            status=data.get('status')
            if status=='completed':
                urls=data.get('unsigned_urls',[])
                if not urls: raise ProviderError('Completed video has no downloadable output')
                raw=await self.http.download(urls[0])
                if len(raw)<12 or raw[4:8]!=b'ftyp': raise ProviderError('Video output is not MP4')
                artifact=self.artifact(raw,kind='video',mime='video/mp4',provider='openrouter',model=job['model'],name=('Veo3.1' if job['model']=='google/veo-3.1' else 'Seedance')+' take',run_id=job.get('run_id'),task_id=job.get('task_id'))
                cost=data.get('usage',{}).get('cost')
                self.store.finish_job(job['id'],result_refs=[artifact['id']],cost_usd=cost,cost_basis='reported' if cost is not None else 'estimated',receipt={'id':job['remote_id'],'usage':data.get('usage')})
                return artifact,job
            if status in ('failed','cancelled'):
                self.store.finish_job(job['id'],'failed',error='Video '+status,cost_usd=data.get('usage',{}).get('cost'))
                raise ProviderError('Video '+status)
            if status in ('running','in_progress','processing'): self.store.set_job_running(job['id'])
            await asyncio.sleep(3)
        raise TimeoutError('Video polling paused after 30 minutes; remote ID remains resumable')
