import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ChevronRight, FileText, Pin, PinOff, RotateCcw, X } from 'lucide-react';
import type { Artifact, Command, Entity, EntityPatch, SceneRevision, Snapshot, Take } from '../api/contracts';
import type { InspectionFocus } from '../state/workspace';
import { applicationPromptPreview, handoffDetails, takeSourceDiffers } from './records';
import { ArtifactRows, isExportedDerivative, useArtifactText } from './Artifacts';
import { Dialog } from './Dialog';
import { displayText, modelLabel, providerLabel } from './modelPresentation';
import { EmptyState, humanize, IconButton, isTerminalJobStatus, shortId, StatusBadge, Working } from './primitives';

type InspectorProps = {
  snapshot: Snapshot;
  inspectionScene: SceneRevision | null;
  focus: InspectionFocus | null;
  pinned: boolean;
  drawerOpen: boolean;
  readOnly: boolean;
  canGoBack: boolean;
  onPin: () => void;
  onBack: () => void;
  onClose: () => void;
  onInspect: (focus: InspectionFocus) => void;
  onOpen: (artifact: Artifact) => void;
  onTakeAction: (operation: 'accept_take' | 'reject_take' | 'repair_take', take: Take) => void;
  onCommand: (operation: Command['operation'], payload: Record<string, unknown>) => Promise<boolean>;
  onDraftPrompt: (shotId: string, prompt: string) => void;
  promptDrafts: Record<string, string>;
  onEntityDraft: (entity: Entity | null) => void;
  onSeek: (takeId: string, timeMs: number) => void;
  newResult?: Artifact | null;
  onOpenNewResult?: () => void;
  onReturnCurrent: () => void;
};

export function Inspector(props: InspectorProps) {
  const { snapshot, focus, pinned, onInspect, onOpen } = props;
  const scene = props.inspectionScene;
  const historicalEntity = Boolean(focus && ['entity', 'camera'].includes(focus.kind) && focus.scene_revision_id !== snapshot.scene?.id);
  const artifacts = snapshot.artifacts ?? [];
  const entity = focus?.kind === 'entity' ? scene?.entities.find((item) => item.id === focus.id) : undefined;
  const includedInId = typeof entity?.state?.included_in_entity_id === 'string' ? entity.state.included_in_entity_id : undefined;
  const includedIn = includedInId ? scene?.entities.find((item) => item.id === includedInId) : undefined;
  const camera = focus?.kind === 'camera' ? scene?.cameras.find((item) => item.id === focus.id) : undefined;
  const take = focus?.kind === 'take' ? snapshot.takes?.find((item) => item.id === focus.id) : undefined;
  const staleTakeSource = take ? takeSourceDiffers(take, snapshot.scene?.id) : false;
  const takeDisposed = Boolean(take && ['accepted', 'rejected'].includes(take.status));
  const creativeDecision = take ? (snapshot.events ?? []).filter((event) => ['take.accepted', 'take.rejected', 'demo.disposition_imported'].includes(event.type) && event.payload?.take_id === take.id).sort((a, b) => b.seq - a.seq)[0] : undefined;
  const importedDecision = creativeDecision?.type === 'demo.disposition_imported';
  const observation = focus?.kind === 'observation' ? snapshot.observations?.find((item) => item.id === focus.id) : undefined;
  const job = focus?.kind === 'model_call' ? snapshot.jobs?.find((item) => item.id === focus.id) : take ? snapshot.jobs?.find((item) => item.id === take.job_id) : undefined;
  const taskId = focus?.kind === 'task' ? focus.id : focus?.task_id ?? job?.task_id;
  const task = snapshot.tasks?.find((item) => item.id === taskId);
  const runRecord = snapshot.runs?.find((item) => item.id === (take?.run_id ?? job?.run_id ?? task?.run_id));
  const shotId = focus?.kind === 'shot' ? focus.id : take?.shot_id ?? task?.shot_id ?? job?.shot_id ?? runRecord?.shot_id;
  const shot = snapshot.shots?.find((item) => item.id === shotId);
  const generationPackage = take ? snapshot.packages?.find((item) => item.id === take.package_id) : job?.package_id ? snapshot.packages?.find((item) => item.id === job.package_id) : [...(snapshot.packages ?? [])].reverse().find((item) => job ? item.run_id === job.run_id : shot && item.shot_id === shot.id);
  const sourcePackage = take || focus?.kind === 'model_call' ? generationPackage : undefined;
  const sourceDuration = sourcePackage ? sourcePackage.duration : take ? undefined : shot?.duration;
  const sourceCameraId = sourcePackage ? sourcePackage.camera_id : take ? undefined : shot?.camera_id;
  const sourceRevisionId = sourcePackage?.scene_revision_id ?? take?.scene_revision_id ?? shot?.scene_revision_id;
  const sourceAssertions: string[] = sourcePackage ? sourcePackage.assertions ?? [] : take ? [] : shot?.assertions ?? [];
  const latestShotTake = shot ? snapshot.takes?.filter((item) => item.shot_id === shot.id).at(-1) : undefined;
  const handoff = focus?.kind === 'handoff' ? snapshot.handoffs?.find((item) => item.id === focus.id) : undefined;
  const importedRecord = task?.evidence_origin === 'imported' ? task : handoff?.evidence_origin === 'imported' ? handoff : undefined;
  const linkedTaskIds = new Set((snapshot.handoffs ?? []).flatMap((row) => { const edge = handoffDetails(row, snapshot.tasks); return task && (edge.senderId === task.id || edge.receiverId === task.id) ? [edge.senderId, edge.receiverId] : []; }));
  const relatedTasks = snapshot.tasks?.filter((item) => (shot && (item.shot_id === shot.id || snapshot.runs?.some((run) => run.id === item.run_id && run.shot_id === shot.id))) || (take && (item.result?.output_refs ?? item.output_refs ?? []).includes(take.package_id)) || linkedTaskIds.has(item.id)).filter((item) => item.id !== task?.id) ?? [];
  const promptId = (focus?.kind === 'task' ? task?.prompt_ref : generationPackage?.prompt_ref ?? job?.prompt_ref ?? task?.prompt_ref) ?? artifacts.find((item) => item.kind === 'prompt' && (item.metadata?.package_id === take?.package_id && take || item.metadata?.shot_id === shot?.id && shot))?.id;
  const prompt = artifacts.find((item) => item.id === promptId);
  const referenceWorkflow = generationPackage?.workflow === 'reference-motion-v1' || Boolean(shot?.motion || shot?.reference_pins?.length);
  const imageInputs: string[] = (focus?.kind === 'task' ? task?.input_refs : generationPackage?.reference_ids ?? job?.input_refs ?? shot?.reference_ids ?? (handoff ? handoffDetails(handoff, snapshot.tasks).references : undefined)) ?? [];
  const inputs = generationPackage?.workflow === 'reference-motion-v1' && focus?.kind !== 'task' && generationPackage.motion_video_id ? [...imageInputs, generationPackage.motion_video_id] : imageInputs;
  const submittedVideoInputs = Boolean(generationPackage && focus?.kind !== 'task');
  const inputRoles = generationPackage?.workflow === 'reference-motion-v1' ? { ...Object.fromEntries(imageInputs.map((id, index) => [id, `@Image${index + 1} · approved appearance`])), [generationPackage.motion_video_id]: '@Video1 · Blender motion only' } : submittedVideoInputs && generationPackage?.conditioning_mode === 'first_last_frame' ? Object.fromEntries(inputs.slice(0, 2).map((id, index) => [id, index === 0 ? 'Start frame' : 'Proposed end frame'])) : {};
  const planningReferences: string[] = submittedVideoInputs && Array.isArray(generationPackage?.planning_reference_ids) ? generationPackage.planning_reference_ids.filter((id: unknown): id is string => typeof id === 'string') : [];
  const separatePlanningContext = planningReferences.length > 0 && (planningReferences.length !== inputs.length || planningReferences.some((id, index) => id !== inputs[index]));
  const outputs: string[] = take ? [take.artifact_id] : task?.result?.output_refs ?? task?.output_refs ?? job?.result_refs ?? job?.output_refs ?? (latestShotTake ? [latestShotTake.artifact_id] : []);
  const observations = snapshot.observations?.filter((item) => item.take_id === take?.id) ?? [];
  const title = focus?.kind === 'models' ? 'Video history' : entity?.name ?? camera?.name ?? (take ? `Take ${shortId(take.id, 8)}` : focus?.kind === 'model_call' && job ? modelLabel(job) : focus?.kind === 'task' && task ? task.role_label : shot?.name ?? (handoff ? 'Artifact handoff' : observation ? 'Continuity observation' : 'Scene inspector'));
  const subtitle = focus?.kind === 'models' ? 'Previous video services and their recorded receipts.' : entity ? `${humanize(entity.kind)} · ${entity.asset_id ? 'Versioned asset' : 'No model asset linked'}` : camera ? 'Saved camera · meters, Z-up' : take ? 'Generated media · inspect its source and creative decision below.' : focus?.kind === 'model_call' && job ? `Recorded ${providerLabel(job)} request · ${shot?.name ?? 'production'}` : task?.goal ?? shot?.action ?? (handoff ? handoffDetails(handoff, snapshot.tasks).summary : undefined) ?? observation?.assertion ?? 'Select an object, shot, task, or take to inspect its source and result.';
  const status = take?.status ?? (focus?.kind === 'model_call' ? job?.status : focus?.kind === 'task' ? task?.status : shot?.status ?? (handoff ? handoffDetails(handoff, snapshot.tasks).state : undefined) ?? observation?.status);
  const revision = focus?.scene_revision_id ?? scene?.id;
  return <aside className={`inspector ${props.drawerOpen ? 'drawer-open' : ''}`} aria-label="Inspector"><div className="inspector-heading"><div className="inspector-heading-left"><span className="eyebrow">{pinned ? 'Pinned context' : 'Selected context'}</span><h2>Inspector</h2></div><div className="inspector-heading-actions"><IconButton label="Previous inspection" disabled={!props.canGoBack || pinned} onClick={props.onBack}><ArrowLeft size={14} /></IconButton><IconButton label={pinned ? 'Unpin inspector' : 'Pin inspector'} aria-pressed={pinned} className={pinned ? 'active' : ''} disabled={!focus} onClick={props.onPin}>{pinned ? <PinOff size={14} /> : <Pin size={14} />}</IconButton><IconButton label="Close inspector" className="inspector-mobile-button" onClick={props.onClose}><X size={14} /></IconButton></div></div>
    <div className="inspector-scroll">
      {props.newResult && <div className="inspector-section"><button className="text-button" onClick={props.onOpenNewResult}>New result available · inspect <ArrowUpRight size={11} /></button><p>Your pinned context is unchanged.</p></div>}
      {historicalEntity && <div className="inspector-section"><p>{scene ? 'Historical scene revision · read-only.' : 'This historical scene revision is not loaded.'}</p><button className="text-button" onClick={props.onReturnCurrent}>Inspect current revision <ArrowUpRight size={11} /></button></div>}
      <div className="inspector-overview"><h3 className="inspector-object-name">{displayText(title)}</h3><div className="inspector-id">{focus ? `${focus.kind} / ${shortId(focus.id, 16)}` : 'PRODUCTION CONTEXT'}</div><p className="inspector-subtitle">{displayText(subtitle)}</p><div className="inspector-meta">{status && <StatusBadge status={status} />}<span className="inspector-id">r/{shortId(revision, 8)}</span>{pinned && <span className="status-badge"><Pin size={10} />Pinned</span>}</div></div>
      {!focus && <EmptyState icon={<FileText size={24} />} title="Follow a shot through production" detail="Inspect its prompt, ordered references, generated take, and continuity evidence here." />}
      {importedRecord && <section className="inspector-section"><h3>Imported source evidence</h3><p>These records document a historical run. Importing them executed no provider calls.</p><dl className="detail-grid" style={{ marginTop: 10 }}><dt>Source run</dt><dd>{shortId(importedRecord.source_run_id, 18)}</dd>{typeof importedRecord.source_status === 'string' && <><dt>Source status</dt><dd>{humanize(importedRecord.source_status)}</dd></>}</dl></section>}
      {take && <section className="inspector-section creative-decision"><h3>{importedDecision ? 'Historical creative decision' : 'Creative decision'}</h3>{creativeDecision ? <>{importedDecision && <p style={{ marginBottom: 10 }}>Imported source decision; no new acceptance or rejection was executed.</p>}<dl className="detail-grid"><dt>Decision</dt><dd>{humanize(importedDecision ? String(creativeDecision.payload?.disposition ?? 'unavailable') : creativeDecision.type.slice(5))}</dd><dt>Actor</dt><dd>{typeof creativeDecision.payload?.actor === 'string' && creativeDecision.payload.actor.trim() ? displayText(creativeDecision.payload.actor) : 'Unavailable'}</dd></dl><p style={{ marginTop: 10 }}>{typeof creativeDecision.payload?.reason === 'string' && creativeDecision.payload.reason.trim() ? displayText(creativeDecision.payload.reason) : 'Decision reason unavailable.'}</p></> : <p>No creative decision event available. Actor and reason are unavailable.</p>}</section>}
      {includedInId && <section className="inspector-section"><div className="notice-box">Included in {displayText(includedIn?.name ?? includedInId)}; moves with actor. Independent transform and material edits are unavailable because this geometry is part of the actor asset.</div>{includedIn && <button className="text-button" onClick={() => onInspect({ kind: 'entity', id: includedIn.id, scene_revision_id: scene?.id ?? '' })}>Inspect {displayText(includedIn.name)} <ArrowUpRight size={11} /></button>}</section>}
      {entity && <EntityEditor key={JSON.stringify(entity)} entity={entity} readOnly={props.readOnly || historicalEntity || Boolean(includedInId)} onDraft={props.onEntityDraft} onApply={async (patch) => props.onCommand('apply_scene', { patches: [patch] })} />}
      {camera && <section className="inspector-section"><h3>Camera geometry</h3><dl className="detail-grid"><dt>Focal length</dt><dd>{camera.focal_length_mm} mm</dd><dt>Sensor width</dt><dd>{camera.sensor_width_mm} mm</dd><dt>Position</dt><dd>{camera.position.map((v) => v.toFixed(2)).join(', ')} m</dd><dt>Target</dt><dd>{camera.target.map((v) => v.toFixed(2)).join(', ')} m</dd><dt>Aspect ratio</dt><dd>{camera.aspect_ratio.toFixed(3)}</dd></dl><p style={{ marginTop: 12 }}>Navigate the world, then use Save view to create a shot with the camera draft.</p></section>}
      {shot && <section className="inspector-section"><h3>{sourcePackage ? 'Generation package' : take ? 'Recorded shot source' : 'Shot plan'}</h3><dl className="detail-grid"><dt>Duration</dt><dd>{typeof sourceDuration === 'number' ? `${sourceDuration} s` : 'Not recorded'}</dd><dt>Camera</dt><dd>{sourceCameraId ? <button className="text-button" onClick={() => onInspect({ kind: 'camera', id: sourceCameraId, scene_revision_id: sourceRevisionId ?? '' })}>{shortId(sourceCameraId, 12)} <ChevronRight size={10} /></button> : 'Not recorded'}</dd><dt>Scene revision</dt><dd>{shortId(sourceRevisionId, 12)}</dd></dl>{sourceAssertions.length ? <p style={{ marginTop: 10 }}>{displayText(sourceAssertions.join(' · '))}</p> : null}</section>}
      {take && <section className="inspector-section"><h3>Immutable source</h3><dl className="detail-grid"><dt>Package</dt><dd>{shortId(take.package_id, 16)}</dd><dt>Generated from</dt><dd>{shortId(take.scene_revision_id, 14)}</dd><dt>Job</dt><dd>{importedDecision && !job ? 'Source call not imported' : <button className="text-button" onClick={() => onInspect({ kind: 'model_call', id: take.job_id, scene_revision_id: take.scene_revision_id })}>{shortId(take.job_id, 12)} <ChevronRight size={10} /></button>}</dd></dl></section>}
      {job && <section className="inspector-section"><h3>Service receipt</h3><dl className="detail-grid"><dt>Service</dt><dd>{providerLabel(job)}</dd><dt>Model or capability</dt><dd>{modelLabel(job)}</dd><dt>Remote ID</dt><dd>{shortId(job.remote_id ?? job.remote_job_id, 16)}</dd><dt>State</dt><dd>{humanize(job.status ?? 'unknown')}</dd></dl>{job.error && <p className="artifact-error" style={{ marginTop: 8 }}>{displayText(job.error)}</p>}{job.status === 'submission_unknown' && <UnknownSubmissionActions key={job.id} job={job} readOnly={props.readOnly} onCommand={props.onCommand} />}{!props.readOnly && job.status !== 'submission_unknown' && !isTerminalJobStatus(job.status) && <button className="text-button" onClick={() => props.onCommand('cancel_job', { job_id: job.id })}>Request cancellation</button>}</section>}
      {handoff && <section className="inspector-section"><h3>Recorded dependency</h3>{['from_task_id', 'to_task_id'].map((field) => <div className="context-row" key={field}><span>{field.startsWith('from') ? 'From' : 'To'}</span><button className="text-button" onClick={() => onInspect({ kind: 'task', id: handoff[field], scene_revision_id: revision ?? '', run_id: handoff.run_id })}>{displayText(snapshot.tasks?.find((item) => item.id === handoff[field])?.role_label ?? shortId(handoff[field]))} <ArrowUpRight size={11} /></button></div>)}{handoff.error && <p className="artifact-error">{displayText(handoff.error)}</p>}</section>}
      {focus && focus.kind !== 'models' && !entity && !camera && <>
        <PromptSection key={prompt?.id ?? focus.id} prompt={prompt} onOpen={onOpen} shotId={shot?.id} readOnly={props.readOnly || referenceWorkflow} draft={referenceWorkflow ? undefined : props.promptDrafts[shot?.id ?? '']} onDraft={props.onDraftPrompt} />
        {referenceWorkflow && <p className="inspector-subtitle">Revise the shared shot plan in Compose shots, then render and prepare a new production request. Its prompt is compiled from that plan.</p>}
        <section className="inspector-section"><h3>{submittedVideoInputs ? 'Submitted video inputs' : 'Inputs'} <span className="inspector-id">{inputs.length} {inputs.length === 1 ? 'reference' : 'references'}</span></h3>{submittedVideoInputs && generationPackage?.conditioning_mode === 'first_frame' && <p style={{ marginBottom: 8 }}>One first-frame image attached to the video request.</p>}{submittedVideoInputs && generationPackage?.conditioning_mode === 'first_last_frame' && <p style={{ marginBottom: 8 }}>Start and proposed end frame attached to the video request. The end frame is guidance, not an accepted scene revision.</p>}<ArtifactRows artifacts={inputs.map((id) => artifacts.find((item) => item.id === id)).filter((item): item is Artifact => Boolean(item))} labels={inputRoles} onOpen={onOpen} empty="No input artifacts recorded for this selection." /></section>
        {separatePlanningContext && <details className="inspector-section planning-context"><summary>Planner context <span className="inspector-id">{planningReferences.length} references</span></summary><p style={{ margin: '10px 0' }}>Context supplied to the planner. The video attachments are listed separately above.</p><ArtifactRows artifacts={planningReferences.map((id) => artifacts.find((item) => item.id === id)).filter((item): item is Artifact => Boolean(item))} onOpen={onOpen} /></details>}
        <section className="inspector-section"><h3>Outputs <span className="inspector-id">{outputs.length} {outputs.length === 1 ? 'artifact' : 'artifacts'}</span></h3><ArtifactRows artifacts={outputs.map((id) => artifacts.find((item) => item.id === id)).filter((item): item is Artifact => Boolean(item))} onOpen={onOpen} empty={status === 'running' || status === 'queued' ? 'Waiting for the recorded result.' : 'No output artifacts recorded.'} /></section>
      </>}
      {focus?.kind === 'models' && <section className="inspector-section"><h3>Previous video services</h3>{(snapshot.models ?? []).filter((model) => model.category === 'video' && model.active === false).map((model, index) => {
        const id = String(model.model ?? model.model_id ?? model.id);
        const latest = snapshot.jobs?.filter((item) => item.model === id).at(-1);
        return <button type="button" className="artifact-row" key={id} disabled={!latest} onClick={() => latest && onInspect({ kind: 'model_call', id: latest.id, scene_revision_id: latest.scene_revision_id ?? snapshot.scene?.id ?? '' })}><span className="artifact-row-copy"><span className="artifact-name">{modelLabel(model)} · {index + 1}</span><span className="artifact-hash">{providerLabel(model)} · previous binding</span></span><StatusBadge status={latest?.status ?? model.status ?? 'configured'} compact /><ArrowUpRight size={12} /></button>;
      })}<p style={{ marginTop: 12 }}>Open a service's recorded call to inspect its inputs, result, and receipt.</p></section>}
      {task?.context_ref && <section className="inspector-section"><h3>Loaded context</h3><ArtifactRows artifacts={artifacts.filter((item) => item.id === task.context_ref)} onOpen={onOpen} /></section>}
      {!!relatedTasks.length && <section className="inspector-section"><h3>Related work</h3>{relatedTasks.map((item) => <button key={item.id} className="text-button" onClick={() => onInspect({ kind: 'task', id: item.id, scene_revision_id: item.scene_revision_id ?? revision ?? '', task_id: item.id, run_id: item.run_id })}>{displayText(item.role_label)} <ArrowUpRight size={11} /></button>)}</section>}
      {(take || observation) && <section className="inspector-section"><h3>Continuity evidence</h3>{(observation ? [observation] : observations).map((item) => <div className="observation" key={item.id}><div className="observation-head"><StatusBadge status={item.status} /><span className="inspector-id">{displayText(item.method)}</span></div><p><strong>{displayText(item.assertion)}</strong><br />{displayText(item.detail)}</p>{item.time_ms != null && <button className="text-button" onClick={() => props.onSeek(item.take_id, item.time_ms!)}>View evidence at {(item.time_ms / 1000).toFixed(2)} s <ArrowUpRight size={11} /></button>}<ArtifactRows artifacts={artifacts.filter((artifact) => item.evidence_ids?.includes(artifact.id))} onOpen={onOpen} empty="" /></div>)}{!observation && !observations.length && <p>No continuity observations recorded. Spatial claims remain unverified.</p>}</section>}
    </div>
    {take && <div className="inspector-actions"><div className="inspector-actions-target">Target: {take.id}<br />Package: {shortId(take.package_id, 22)} · head {shortId(snapshot.scene?.id, 8)}</div>{staleTakeSource && !takeDisposed && <div className="draft-notice">Source scene differs; inspect or restore before applying.</div>}<div className="inspector-actions-buttons"><button className="button primary" disabled={props.readOnly || staleTakeSource || takeDisposed} onClick={() => props.onTakeAction('accept_take', take)}><Check size={13} />Accept</button><button className="button" disabled={props.readOnly || takeDisposed} onClick={() => props.onTakeAction('reject_take', take)}><X size={12} />Reject</button><button className="button" disabled={props.readOnly || staleTakeSource || referenceWorkflow} title={referenceWorkflow ? 'Revise the shared plan and review a new package in Compose shots.' : undefined} onClick={() => props.onTakeAction('repair_take', take)}><RotateCcw size={12} />Repair</button></div></div>}
  </aside>;
}

function PromptSection({ prompt, onOpen, shotId, readOnly, onDraft, draft }: { prompt?: Artifact; onOpen: (artifact: Artifact) => void; shotId?: string; readOnly: boolean; onDraft: (id: string, prompt: string) => void; draft?: string }) {
  const { text, loading, error } = useArtifactText(prompt);
  const [editing, setEditing] = useState(false);
  const [localDraft, setLocalDraft] = useState('');
  const derivative = prompt ? isExportedDerivative(prompt) : false;
  const originalPreview = applicationPromptPreview(text);
  const preview = displayText(originalPreview);
  return <section className="inspector-section"><h3>Prompt {prompt && <button className="text-button" onClick={() => onOpen(prompt)}>{derivative ? 'Exported prompt' : 'Full request'} <ArrowUpRight size={11} /></button>}</h3>{loading ? <Working label="Loading application prompt" /> : error ? <p className="artifact-error">{displayText(error)}</p> : text ? <pre className="clamped">{preview}</pre> : <p>No application prompt artifact recorded.</p>}{preview !== originalPreview && <p className="inspector-subtitle">Display preview · open original for the recorded payload.</p>}{prompt && <div className="inspector-id" style={{ marginTop: 9 }}>{derivative ? 'Exported derivative hash' : 'Original SHA-256'} {shortId(prompt.sha256, 12)}</div>}{derivative && <p style={{ marginTop: 8 }}>{displayText(prompt?.metadata?.export_transformation)}</p>}{shotId && prompt && !readOnly && <button className="text-button" onClick={() => { setLocalDraft(draft ?? text); setEditing(!editing); }}>Fork an editable prompt draft</button>}{draft != null && <div className="draft-notice">Draft for a future generation; {derivative ? 'exported' : 'submitted'} source preserved.</div>}{editing && shotId && <><textarea className="prompt-editor" aria-label="Prompt draft" value={localDraft} onChange={(event) => setLocalDraft(event.target.value)} /><div style={{ display: 'flex', gap: 7, marginTop: 8 }}><button className="button primary" disabled={!localDraft.trim() || readOnly} onClick={() => { onDraft(shotId, localDraft); setEditing(false); }}>Keep draft</button><button className="button" onClick={() => setEditing(false)}>Cancel</button></div></>}</section>;
}

function UnknownSubmissionActions({ job, readOnly, onCommand }: { job: Record<string, unknown>; readOnly: boolean; onCommand: InspectorProps['onCommand'] }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState(false);
  if (job.dispatch_retired) return <div className="notice-box">Retired from dispatch exclusion. The remote outcome is still unknown and the full budget reservation remains held.</div>;
  return <>
    <div className="notice-box">The provider may still be processing this request. Its full budget reservation remains held until the outcome is reconciled.</div>
    <button type="button" className="text-button" disabled={readOnly} onClick={() => { setReason(''); setOpen(true); }}>Retire uncertain attempt</button>
    {open && <Dialog title="Retire uncertain attempt" onClose={() => !pending && setOpen(false)} footer={<><button className="button" disabled={pending} onClick={() => setOpen(false)}>Keep unresolved</button><button className="button primary" disabled={readOnly || pending || !reason.trim()} onClick={async () => { setPending(true); try { if (await onCommand('retire_unknown', { job_id: job.id, reason: reason.trim() })) setOpen(false); } finally { setPending(false); } }}>{pending ? 'Recording decision…' : 'Retire from dispatch'}</button></>}>
      <dl className="detail-grid"><dt>Exact job</dt><dd>{String(job.id)}</dd><dt>Remote outcome</dt><dd>Unknown</dd><dt>Budget reservation</dt><dd>Remains fully held</dd></dl>
      <div className="notice-box">Retirement permits a separate new attempt to be submitted later. It does not cancel the original remote job, release its reservation, or repeat its request. The original may still complete or incur a charge; a new attempt could duplicate provider work and needs its own reservation.</div>
      <label className="dialog-label" htmlFor="retirement-reason">Operator reason (required)</label>
      <textarea id="retirement-reason" autoFocus value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Record why this unresolved attempt should stop blocking new work." />
    </Dialog>}
  </>;
}

function EntityEditor({ entity, readOnly, onApply, onDraft }: { entity: Entity; readOnly: boolean; onApply: (patch: EntityPatch) => Promise<boolean>; onDraft: (entity: Entity | null) => void }) {
  const [position, setPosition] = useState<[number, number, number]>([...entity.position]);
  const [rotation, setRotation] = useState<[number, number, number]>([...entity.rotation]);
  const [scale, setScale] = useState<[number, number, number]>([...entity.scale]);
  const [color, setColor] = useState(entity.material?.color ?? '#d1c7b5');
  const isLight = entity.kind === 'light';
  const initialIntensity = typeof entity.state?.intensity === 'number' ? entity.state.intensity : entity.state?.light_type === 'point' ? 35 : 1.4;
  const [intensity, setIntensity] = useState(initialIntensity);
  const [pending, setPending] = useState(false);
  const dirty = JSON.stringify(position) !== JSON.stringify(entity.position) || JSON.stringify(rotation) !== JSON.stringify(entity.rotation) || JSON.stringify(scale) !== JSON.stringify(entity.scale) || color !== (entity.material?.color ?? '#d1c7b5') || isLight && intensity !== initialIntensity;
  const valid = [...position, ...rotation, ...scale].every(Number.isFinite) && position.every((value) => Math.abs(value) <= 1000) && scale.every((value) => value > 0) && (!isLight || Number.isFinite(intensity) && intensity >= 0 && intensity <= 1000);
  const material = { color, roughness: entity.material?.roughness ?? 0.7, metalness: entity.material?.metalness ?? 0 };
  // The preview retains provenance; commands carry only the editable state whitelist.
  const creativeState = { material_override: color !== entity.material?.color || entity.state?.material_override === true, ...(isLight ? { intensity } : {}) };
  useEffect(() => {
    if (!readOnly && dirty && valid) onDraft({ ...entity, position, rotation, scale, material, state: { ...entity.state, ...creativeState } });
    else onDraft(null);
  }, [position, rotation, scale, color, intensity, readOnly]);
  useEffect(() => () => onDraft(null), []);
  const reset = () => { setPosition([...entity.position]); setRotation([...entity.rotation]); setScale([...entity.scale]); setColor(entity.material?.color ?? '#d1c7b5'); setIntensity(initialIntensity); onDraft(null); };
  useEffect(() => {
    if (!dirty) return;
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.pointerLockElement) return;
      event.preventDefault(); event.stopPropagation(); reset();
    };
    window.addEventListener('keydown', cancel, true);
    return () => window.removeEventListener('keydown', cancel, true);
  }, [dirty]);
  const vectors = [
    { name: 'Position', hint: 'meters', value: position, set: setPosition },
    { name: 'Rotation', hint: 'radians', value: rotation, set: setRotation },
    { name: 'Scale', hint: 'positive multipliers', value: scale, set: setScale },
  ];
  return <section className="inspector-section">
    <h3>Transform <span className="inspector-id">Z-UP · METERS</span></h3>
    {vectors.map(({ name, hint, value, set }) => <div key={name}>
      <div className="transform-label">{name} · {hint}</div>
      <div className="transform-grid">{['X', 'Y', 'Z'].map((axis, index) => <label key={axis} className="transform-field"><span>{axis}</span><input type="number" step="0.05" min={name === 'Scale' ? 0 : undefined} aria-label={`${name} ${axis}`} disabled={readOnly} value={Number.isFinite(value[index]) ? value[index] : ''} onChange={(event) => set(value.map((v, i) => i === index ? event.target.valueAsNumber : v) as [number, number, number])} /></label>)}</div>
    </div>)}
    {isLight && <div style={{ marginTop: 12 }}><label className="dialog-label" htmlFor="light-intensity">Light intensity</label><input id="light-intensity" type="number" min={0} max={1000} step="0.1" disabled={readOnly} value={Number.isFinite(intensity) ? intensity : ''} onChange={(event) => setIntensity(event.target.valueAsNumber)} /><p className="inspector-id" style={{ marginTop: 5 }}>Authored strength · 0–1000</p></div>}
    <div className="context-row" style={{ marginTop: 10 }}><span>Material color</span><input type="color" aria-label="Material color" value={color} disabled={readOnly} onChange={(event) => setColor(event.target.value)} style={{ width: 37, height: 25, border: '1px solid var(--line)', padding: 2, background: 'transparent' }} /></div>
    <dl className="detail-grid"><dt>Asset</dt><dd>{entity.asset_id ? shortId(entity.asset_id, 14) : 'Not linked'}</dd><dt>Mesh dimensions</dt><dd>{entity.dimensions.map((v) => v.toFixed(2)).join(' × ')} m</dd></dl>
    {dirty && <>
      <div className="draft-notice">{valid ? 'Unsaved preview · Apply creates a new revision.' : 'Unsaved values · correct the fields before previewing or applying.'}</div>
      {!valid && <p className="artifact-error" role="alert">Use finite values, positions within ±1000 m, and scale greater than zero.{isLight ? ' Light intensity must be between 0 and 1000.' : ''}</p>}
      <div style={{ display: 'flex', gap: 7, marginTop: 8 }}><button className="button primary" disabled={readOnly || pending || !valid} onClick={async () => { setPending(true); try { await onApply({ entity_id: entity.id, position, rotation, scale, material, state: creativeState }); } finally { setPending(false); } }}>{pending ? 'Applying…' : 'Apply revision'}</button><button className="button" onClick={reset}>Cancel edit</button></div>
    </>}
  </section>;
}
