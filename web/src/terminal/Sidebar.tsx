import { Box, Camera, ChevronDown, CircleDot, Layers3, Lightbulb, UserRound, Paintbrush, Ruler } from 'lucide-react';
import type { Snapshot } from '../api/contracts';
import type { InspectionFocus } from '../state/workspace';
import type { DesignTool } from './types';
import { shortId, StatusBadge } from './primitives';

const ROLES = [
  { id: 'role.producer.producer-orchestrator', name: 'Producer', initials: 'PR' },
  { id: 'role.cinematography.director-of-photography', name: 'Director of Photography', initials: 'DP' },
  { id: 'role.generation-technical.prompt-compiler-generation-planner', name: 'Generation Planner', initials: 'GP' },
  { id: 'role.cinematography.photographic-spatial-continuity-specialist', name: 'Script Supervisor', initials: 'SS' },
];

export function Sidebar({ snapshot, focus, onInspect, onCamera, tool, onTool }: { snapshot: Snapshot; focus: InspectionFocus | null; onInspect: (focus: InspectionFocus) => void; onCamera: (id: string) => void; tool?: DesignTool; onTool: (tool: DesignTool) => void }) {
  const scene = snapshot.scene;
  const tasks = snapshot.tasks ?? [];
  return <aside className="left-rail" aria-label="Production navigator">
    <div className="rail-heading"><h2>Production</h2><Layers3 size={14} color="#777e70" /></div>
    <nav className="design-tools" aria-label="Design tools">{([{id:'scene',label:'Scene',detail:'Architecture & set',icon:Ruler},{id:'materials',label:'Materials',detail:'Color & finish',icon:Paintbrush},{id:'lighting',label:'Lighting',detail:'Exposure & lights',icon:Lightbulb},{id:'camera',label:'Camera',detail:'Framing & lens',icon:Camera}] as const).map(({id,label,detail,icon:Icon}) => <button key={id} className={tool === id ? 'selected' : ''} aria-pressed={tool === id} onClick={() => onTool(id)}><Icon size={17}/><span><strong>{label}</strong><small>{detail}</small></span></button>)}</nav><section className="rail-section"><div className="rail-section-heading"><span className="eyebrow">The set</span><span className="count">{scene?.entities.length ?? 0}</span></div>
      {scene?.entities.map((entity) => { const Icon = entity.kind === 'actor' ? UserRound : entity.kind === 'light' ? Lightbulb : entity.kind === 'room' || entity.kind === 'set' ? Box : CircleDot; return <button key={entity.id} type="button" className={`rail-row ${focus?.kind === 'entity' && focus.id === entity.id ? 'selected' : ''}`} onClick={() => onInspect({ kind: 'entity', id: entity.id, scene_revision_id: scene.id })}><Icon size={14} /><span className="rail-row-label">{entity.name}</span>{!entity.visible && <span className="rail-row-meta">hidden</span>}</button>; })}
      {!scene && <div className="crew-task">Waiting for a saved scene.</div>}
    </section>
    <section className="rail-section"><div className="rail-section-heading"><span className="eyebrow">Cameras</span><span className="count">{scene?.cameras.length ?? 0}</span></div>
      {scene?.cameras.map((camera) => <button key={camera.id} className={`rail-row ${focus?.kind === 'camera' && focus.id === camera.id ? 'selected' : ''}`} onClick={() => onCamera(camera.id)}><Camera size={14} /><span className="rail-row-label">{camera.name}</span><span className="rail-row-meta">{camera.focal_length_mm}mm</span></button>)}
    </section>
    <section className="rail-section"><div className="rail-section-heading"><span className="eyebrow">Production crew</span><ChevronDown size={12} color="#777e70" /></div>
      {ROLES.map((role) => { const task = tasks.filter((item) => item.role_id === role.id).at(-1); return <button key={role.id} className={`rail-row crew-row ${focus?.kind === 'task' && focus.id === task?.id ? 'selected' : ''}`} disabled={!task} onClick={() => task && onInspect({ kind: 'task', id: task.id, scene_revision_id: task.scene_revision_id ?? scene?.id ?? '', run_id: task.run_id, task_id: task.id })}><span className="crew-symbol">{role.initials}</span><span className="crew-info"><span className="crew-name">{role.name}</span><span className="crew-task">{task ? <StatusBadge status={task.status ?? 'unknown'} compact /> : 'No task dispatched'}</span></span></button>; })}
    </section>
    <div className="rail-footer"><span>One world. Every shot connected.</span><span className="record-count">{snapshot.takes?.length ?? 0} takes · {tasks.length} tasks · r/{shortId(scene?.id, 6)}</span></div>
  </aside>;
}
