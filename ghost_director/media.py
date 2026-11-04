from __future__ import annotations
import asyncio
import json
from pathlib import Path

async def process(*args,timeout=120):
    child=await asyncio.create_subprocess_exec(*map(str,args),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try: stdout,stderr=await asyncio.wait_for(child.communicate(),timeout)
    except BaseException:
        child.kill(); await child.wait(); raise
    if child.returncode: raise RuntimeError(f'{Path(str(args[0])).name} exited {child.returncode}: '+stderr.decode(errors='replace')[-900:])
    return stdout

async def video_evidence(path:Path,directory:Path):
    directory.mkdir(parents=True,exist_ok=True)
    probe=json.loads(await process('ffprobe','-v','error','-show_streams','-show_format','-of','json',path))
    stream=next((s for s in probe['streams'] if s.get('codec_type')=='video'),None)
    if stream is None: raise ValueError('Output has no video stream')
    duration=float(probe['format']['duration'])
    if not 1<=duration<=35: raise ValueError('Video duration outside validated range')
    frames=[]
    for index,seconds in enumerate((0,max(0,duration/2),max(0,duration-.15))):
        target=directory/f'frame-{index}.jpg'
        await process('ffmpeg','-v','error','-y','-ss',str(seconds),'-i',path,'-frames:v','1','-q:v','2',target)
        frames.append({'path':target,'time_ms':round(seconds*1000)})
    # Decode all frames to catch corrupt tails that a poster alone would miss.
    await process('ffmpeg','-v','error','-i',path,'-f','null','-')
    return {'width':stream['width'],'height':stream['height'],'duration':duration,'codec':stream['codec_name'],'frame_rate':stream['avg_frame_rate']},frames
