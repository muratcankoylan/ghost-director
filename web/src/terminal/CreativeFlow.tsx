import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUpRight, Check, FileText, GitBranch, History, LoaderCircle, Radio, X } from 'lucide-react';
import type { Artifact, Snapshot } from '../api/contracts';
import type { InspectionFocus } from '../state/workspace';
import { ActivityDock } from './ActivityDock';
import { displayText, modelLabel } from './modelPresentation';
import { handoffDetails } from './records';
import { humanize, localTime, shortId, StatusBadge } from './primitives';

const RUNNING = new Set(['accepted', 'running', 'in_progress', 'queued', 'submitting', 'submitted', 'pending']);
const references = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/** Every node and edge comes from a recorded task or handoff; an empty run stays empty. */
export function CreativeFlow({ snapshot, focus, onInspect, onOpen, onReplay, replay }: {
  snapshot: Snapshot; focus: InspectionFocus | null; onInspect: (focus: InspectionFocus) => void;
  onOpen: (artifact: Artifact) => void; onReplay: () => void; replay: boolean;
}) {
  const [selectedRun, setSelectedRun] = useState('latest');
  const [showEvents, setShowEvents] = useState(false);
  const allTasks = snapshot.tasks ?? [];
  const autoRun = [...allTasks].reverse().find((task) => RUNNING.has(task.status))?.run_id ?? allTasks.at(-1)?.run_id;
  const runId = selectedRun === 'latest' ? autoRun : selectedRun;
  const tasks = allTasks.filter((task) => !runId || task.run_id === runId);
  const run = snapshot.runs?.find((item) => item.id === runId);
  const runOptions = useMemo(() => [...new Set(allTasks.map((task) => task.run_id).filter((id): id is string => typeof id === 'string'))].reverse(), [allTasks]);
  const handoffs = (snapshot.handoffs ?? []).filter((handoff) => !runId || handoff.run_id === runId);
  const artifacts = useMemo(() => new Map((snapshot.artifacts ?? []).map((artifact) => [artifact.id, artifact])), [snapshot.artifacts]);
  const activeCommands = (snapshot.commands ?? []).filter((command) => RUNNING.has(command.status));
  const active = tasks.some((task) => RUNNING.has(task.status));
  const taskFocus = (task: Record<string, any>): InspectionFocus => ({ kind: 'task', id: task.id, task_id: task.id, scene_revision_id: task.scene_revision_id ?? snapshot.scene?.id ?? '', run_id: task.run_id });

  return <section className="creative-flow" aria-label="Live creative crew">
    <header className="creative-flow-header"><GitBranch size={14} /><h2>Creative crew</h2><span className={`crew-live-state ${active && !replay ? 'active' : ''}`}>{replay ? 'REPLAY' : active ? 'WORKING' : tasks.length ? 'RECORDED' : 'READY'}</span></header>
    <div className="creative-flow-toolbar"><label className="sr-only" htmlFor="creative-run">Creative run</label><select id="creative-run" value={selectedRun} onChange={(event) => setSelectedRun(event.target.value)}><option value="latest">Follow latest work</option>{runOptions.map((id) => <option key={id} value={id}>{displayText(snapshot.runs?.find((item) => item.id === id)?.name ?? `Run ${shortId(id, 8)}`)}</option>)}</select><button className="text-button" aria-pressed={showEvents} onClick={() => setShowEvents(!showEvents)}>{showEvents ? 'Crew' : 'Events'}</button></div>
    <div className="creative-flow-body">
      {activeCommands.length > 0 && !replay && <div className="crew-command" role="status"><LoaderCircle size={13} /><span>{humanize(activeCommands[0].operation)}<small>Runtime command in progress</small></span></div>}
      {showEvents || !tasks.length ? <ActivityDock key={String(showEvents)} initialShowEvents={showEvents} snapshot={snapshot} focus={focus} onInspect={onInspect} onReplay={onReplay} replay={replay} /> : <>
        {typeof run?.direction === 'string' && <div className="crew-direction"><span>DIRECTOR'S BRIEF</span><p>{displayText(run.direction)}</p></div>}
        <ol className="crew-task-list">{tasks.map((task) => {
          const outputs = references(task.result?.output_refs ?? task.output_refs).map((id) => artifacts.get(id)).filter((item): item is Artifact => Boolean(item));
          const prompt = artifacts.get(String(task.prompt_ref ?? ''));
          const inputs = references(task.input_refs).map((id) => artifacts.get(id)).filter((item): item is Artifact => Boolean(item));
          const incoming = handoffs.filter((handoff) => handoffDetails(handoff, allTasks).receiverId === task.id);
          const busy = RUNNING.has(task.status) && !replay;
          const failed = ['failed', 'error'].includes(task.status);
          return <li key={task.id}>
            {incoming.map((handoff) => {
              const details = handoffDetails(handoff, allTasks);
              const sender = allTasks.find((item) => item.id === details.senderId);
              return <button key={handoff.id} className="crew-handoff" onClick={() => onInspect({ kind: 'handoff', id: handoff.id, scene_revision_id: handoff.scene_revision_id ?? snapshot.scene?.id ?? '', run_id: handoff.run_id })}><ArrowDown size={13} /><span>{handoff.evidence_origin === 'imported' ? 'Imported · Historical handoff · ' : ''}{displayText(sender?.role_label ?? 'Recorded task')}<small>{details.references.length} artifact{details.references.length === 1 ? '' : 's'} passed · <StatusBadge status={details.state} compact /></small></span></button>;
            })}
            <article className={`crew-task-card ${busy ? 'working-task' : ''} ${failed ? 'failed-task' : ''} ${focus?.id === task.id ? 'selected' : ''}`}>
              <button className="crew-task-heading" onClick={() => onInspect(taskFocus(task))}><span className="crew-task-icon">{busy ? <LoaderCircle size={15} /> : failed ? <X size={15} /> : task.status === 'completed' ? <Check size={15} /> : <Radio size={15} />}</span><span><strong>{displayText(task.role_label ?? humanize(task.role_id ?? 'Recorded task'))}</strong><small>{modelLabel({ model: task.model })}</small></span><ArrowUpRight size={11} /></button>
              {task.goal && <p className="crew-task-goal">{displayText(task.goal)}</p>}
              <div className="crew-task-state"><StatusBadge status={task.status ?? 'unknown'} compact />{task.created_at && <time dateTime={task.created_at}>{localTime(task.created_at)}</time>}</div>
              {(prompt || inputs.length > 0) && <div className="crew-task-links">{prompt && <button onClick={() => onOpen(prompt)}><FileText size={11} />Prompt</button>}{inputs.length > 0 && <button onClick={() => onInspect(taskFocus(task))}>{inputs.length} input{inputs.length === 1 ? '' : 's'}<ArrowUpRight size={10} /></button>}</div>}
              {outputs.length > 0 && <div className="crew-artifacts">{outputs.slice(-3).map((artifact) => <button key={artifact.id} onClick={() => onOpen(artifact)} className={artifact.mime_type.startsWith('image/') ? 'crew-image-result' : 'crew-file-result'}>{artifact.mime_type.startsWith('image/') ? <img src={artifact.url} alt={displayText(artifact.name)} loading="lazy" /> : <FileText size={13} />}<span>{displayText(artifact.name)}<small>{humanize(artifact.kind)} · open result</small></span><ArrowUpRight size={11} /></button>)}</div>}
            </article>
          </li>;
        })}</ol>
      </>}
    </div>
    <footer className="creative-flow-footer"><span>{tasks.length} recorded task{tasks.length === 1 ? '' : 's'} · {handoffs.length} handoff{handoffs.length === 1 ? '' : 's'}</span><button className="text-button" onClick={onReplay}><History size={11} />{replay ? 'Live' : 'Replay'}</button></footer>
  </section>;
}
