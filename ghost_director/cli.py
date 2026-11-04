from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
from ghost_director.config import load_settings
from ghost_director.jobs.store import ProductionStore
from ghost_director.providers.runner import ProviderRunner

async def provider_command(args,settings,store):
    runner=ProviderRunner(store,settings)
    run=store.create_run(metadata={'name':args.command,'source':'authorized-build-experiment'})
    try:
        if args.command=='image': result=await runner.image(Path(args.prompt_file).read_text(),run_id=run['id'],name=args.name)
        elif args.command=='meshy': result=await runner.meshy(args.image_id,args.mask_id,run_id=run['id'])
        elif args.command=='segment': result=await runner.segment(args.image_id,run_id=run['id'])
        elif args.command=='reconstruct': result=await runner.reconstruct(args.image_id,args.mask_id,run_id=run['id'],body=args.body)
        elif args.command=='resume':
            job=next(row for row in store.snapshot()['jobs'] if row['id']==args.job_id)
            result=await (runner.poll_fal_video(job) if job['provider']=='fal' and job.get('category')=='video' else runner.poll_fal(job) if job['provider']=='fal' else runner.poll_video(job))
        artifact,job=result
        store.finish_run(run['id'],'completed')
        print(json.dumps({'artifact_id':artifact['id'],'path':str(store.artifact_path(artifact['id'])),'job_id':job['id'],'budget':store.budget()},indent=2))
    except Exception as error:
        store.finish_run(run['id'],'failed')
        print(json.dumps({'error':store.redactor.text(str(error)),'kind':type(error).__name__,'run_id':run['id']}))
        raise SystemExit(1) from None
    finally: await runner.http.close()

def main():
    parser=argparse.ArgumentParser(description='Ghost Director production workstation')
    parser.add_argument('--env-file')
    sub=parser.add_subparsers(dest='command',required=True)
    image=sub.add_parser('image'); image.add_argument('--prompt-file',required=True); image.add_argument('--name',default='Original Meta reference')
    segment=sub.add_parser('segment'); segment.add_argument('--image-id',required=True)
    reconstruct=sub.add_parser('reconstruct'); reconstruct.add_argument('--image-id',required=True); reconstruct.add_argument('--mask-id'); reconstruct.add_argument('--body',action='store_true')
    meshy=sub.add_parser('meshy'); meshy.add_argument('--image-id',required=True); meshy.add_argument('--mask-id',required=True)
    resume=sub.add_parser('resume'); resume.add_argument('--job-id',required=True)
    serve=sub.add_parser('serve'); serve.add_argument('--port',type=int,default=8000)
    sub.add_parser('init');sub.add_parser('sync-assets');sub.add_parser('bootstrap',help='Create an original empty set locally, without model calls or a demo bundle')
    args=parser.parse_args(); settings=load_settings(args.env_file)
    store=ProductionStore(settings.root,secrets=settings.secrets,opening_entries=())
    if args.command in ('serve','init','sync-assets','bootstrap'):
        if args.command=='bootstrap':
            from ghost_director.bootstrap import bootstrap_set
            asyncio.run(bootstrap_set(store,settings))
        from ghost_director.service import ProductionService
        service=ProductionService(store,settings); service.initialize()
        if args.command=='sync-assets': service.sync_assets()
        if args.command=='serve':
            import uvicorn
            from ghost_director.api import create_app
            origins=[f'http://{host}:{port}' for host in ('localhost','127.0.0.1') for port in sorted({5173,args.port})]
            app=create_app(store,service.handle,allowed_origins=origins,recovery_handler=service.resume,shutdown_handler=service.runner.http.close)
            from fastapi.staticfiles import StaticFiles
            from ghost_director.config import REPO
            if (REPO/'web/dist/index.html').exists(): app.mount('/',StaticFiles(directory=REPO/'web/dist',html=True),name='terminal')
            uvicorn.run(app,host='127.0.0.1',port=args.port,timeout_graceful_shutdown=3,access_log=False)
        else: print(json.dumps({'production':store.production_id,'scene':store.snapshot()['scene']['id']}))
    else: asyncio.run(provider_command(args,settings,store))

if __name__=='__main__': main()
