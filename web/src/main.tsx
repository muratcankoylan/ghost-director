import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { TerminalApp } from './app/TerminalApp';
const SceneViewport = React.lazy(() => import('./viewport/SceneViewport').then(module => ({ default: module.SceneViewport })));
import type { Command, Snapshot } from './api/contracts';

const base='/api/productions/ghost-director-hackathon';
async function request<T>(path:string, init?:RequestInit):Promise<T> {
  const response=await fetch(base+path,init);
  if(!response.ok){let detail='';try{const body=await response.json();detail=typeof body.detail==='string'?body.detail:JSON.stringify(body.detail);}catch{}throw new Error(detail||`Runtime HTTP ${response.status}`);}
  return response.json();
}
function App(){
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null);
  const [connection,setConnection]=useState<'connecting'|'connected'|'disconnected'>('connecting');
  const [lastEventAt,setLastEventAt]=useState<string>();
  const [replayRunId,setReplayRunId]=useState<string|null>(null);
  const [reconnect,setReconnect]=useState(0);
  const live=useRef<Snapshot|null>(null);
  const replay=useRef<string|null>(null);
  const refresh=useCallback(async()=>{const value=await request<Snapshot>('/snapshot');live.current=value;if(!replay.current)setSnapshot(value);return value;},[]);
  useEffect(()=>{
    let stopped=false,events:EventSource|undefined,timer:ReturnType<typeof setTimeout>|undefined,connectTimer:ReturnType<typeof setTimeout>|undefined;
    let connectAttempt=0;
    let refreshPending=false;
    const update=async()=>{if(refreshPending||stopped)return;refreshPending=true;try{await refresh();}catch{setConnection('disconnected');}finally{refreshPending=false;}};
    const connect=()=>refresh().then(value=>{
      if(stopped)return;
      events=new EventSource(`${base}/events?after=${value.last_seq??0}`);
      events.onopen=()=>setConnection('connected');
      events.onerror=()=>setConnection('disconnected');
      events.addEventListener('production',(event)=>{
        try{const record=JSON.parse((event as MessageEvent).data);setLastEventAt(record.recorded_at);}catch{}
        // Coalesce bursts; the durable watermark makes snapshot/SSE races harmless.
        if(!timer)timer=setTimeout(()=>{timer=undefined;void update();},120);
      });
    }).catch(()=>{if(!stopped){setConnection('disconnected');if(connectAttempt<7)connectTimer=setTimeout(()=>{void connect();},Math.min(1000*2**connectAttempt++,10000));}});
    void connect();
    return()=>{stopped=true;events?.close();if(timer)clearTimeout(timer);if(connectTimer)clearTimeout(connectTimer);};
  },[refresh,reconnect]);
  const command=async(value:Command)=>{
    if(replay.current)throw new Error('Recorded replay is read-only');
    const receipt=await request<Record<string,unknown>>('/commands',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    await refresh();
    // The returned receipt acknowledges dispatch; completion is separately visible
    // in the actual event feed. Do not hold the entire terminal for a video render.
    return receipt;
  };
  const selectReplay=async(runId:string|null)=>{
    if(runId){const saved=await request<Snapshot>(`/runs/${encodeURIComponent(runId)}/snapshot`);replay.current=runId;setReplayRunId(runId);setSnapshot(saved);}
    else{replay.current=null;setReplayRunId(null);await refresh();}
  };
  return <TerminalApp snapshot={snapshot} connection={connection} lastEventAt={lastEventAt} onCommand={command} onRefresh={()=>setReconnect(n=>n+1)} onReplay={selectReplay} replayRunId={replayRunId} renderViewport={props=><React.Suspense fallback={<div className="viewport-empty">Loading the 3D world…</div>}><SceneViewport {...props}/></React.Suspense>}/>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
