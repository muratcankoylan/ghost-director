import type { Artifact, Command, ShotPlan, Snapshot } from '../api/contracts';
import { displayText } from './modelPresentation';
import { humanize, localTime } from './primitives';
import './blender-workflow.css';

type Props = {
  snapshot: Snapshot;
  shot?: ShotPlan;
  disabled: boolean;
  onCommand: (operation: Command['operation'], payload: Record<string, unknown>) => unknown;
  onOpen: (artifact: Artifact) => void;
  onPlan: () => void;
};

const localOperations = new Set(['render_motion', 'render_preview', 'compile_shot']);
const activeStatuses = new Set(['accepted', 'running', 'in_progress']);

/** An operator view of existing local commands and immutable outputs, not a second job system. */
export function BlenderWorkflow({ snapshot, shot, disabled, onCommand, onOpen, onPlan }: Props) {
  const scene = snapshot.scene;
  const previews = (snapshot.motion_previews ?? []).filter((item) => item.shot_id === shot?.id);
  const preview = previews.at(-1);
  const currentPlan = Boolean(scene && shot && shot.scene_revision_id === scene.id);
  const currentOutput = Boolean(currentPlan && preview?.scene_revision_id === scene?.id);
  const artifacts = snapshot.artifacts ?? [];
  const artifact = (id: unknown) => artifacts.find((item) => item.id === id);
  const video = artifact(preview?.video_id);
  const blend = artifact(preview?.blend_id);
  const receipt = artifact(preview?.receipt_id);
  const camera = scene?.cameras.find((item) => item.id === shot?.camera_id);
  const still = artifacts.filter((item) => item.kind === 'image' && item.metadata?.render_kind === 'preview' && item.metadata.scene_revision_id === scene?.id && item.metadata.camera_id === camera?.id).at(-1);
  const stillReceipt = artifact(still?.metadata?.receipt_artifact_id);
  const commands = (snapshot.commands ?? []).filter((item) => localOperations.has(item.operation));
  const active = commands.filter((item) => activeStatuses.has(item.status));
  const locked = disabled || active.length > 0;
  const canRenderMotion = Boolean(currentPlan && shot?.motion && camera);
  const events = (snapshot.events ?? []).filter((event) => ['motion.render_started', 'motion.render_completed', 'preview.completed'].includes(event.type));
  const latestEvent = events.at(-1);
  const hasPins = Boolean(shot?.reference_pins?.length);

  return <section className="blender-workflow" aria-label="Blender workflow">
    <div className="blender-intro"><span className="eyebrow">LOCAL PRODUCTION TOOLS</span><h2>Blender workflow</h2><p>The saved set drives a minimal Blender scene for camera movement and rigid blocking.</p><div className="blender-transport">Local Blender subprocess <span>·</span> MCP not connected</div></div>

    <section className="blender-section" aria-labelledby="blender-source-title"><h3 id="blender-source-title">Scene & shot synchronization</h3>
      <dl className="detail-grid"><dt>Current scene</dt><dd>{scene?.id ?? 'No scene'}</dd><dt>Selected shot</dt><dd>{shot?.name ?? 'No shot selected'}</dd><dt>Shot ID</dt><dd>{shot?.id ?? '—'}</dd><dt>Shot scene</dt><dd>{shot?.scene_revision_id ?? '—'}</dd><dt>Camera</dt><dd>{camera?.name ?? 'Unavailable in current scene'}</dd><dt>Timeline</dt><dd>{shot ? `${shot.duration} s · ${shot.fps} fps · ${shot.duration * shot.fps} frames` : '—'}</dd></dl>
      <p className={`blender-sync ${currentPlan && shot?.motion ? 'ready' : 'attention'}`}>{!shot ? 'Select a shot from the sequence below the world.' : !currentPlan ? 'Scene changed. Review and save a new shared shot plan before rendering motion.' : !shot.motion ? 'Save a shared motion plan before rendering technical motion.' : 'Saved shot and current scene match. Rendering uses this exact saved plan.'}</p>
      <button className="button" onClick={onPlan}>Open shot planning</button>
    </section>

    <section className="blender-section" aria-labelledby="blender-actions-title"><h3 id="blender-actions-title">Local render controls</h3><p>These actions run locally and do not call paid generation APIs. Render the saved state; apply any set or camera edits first.</p><div className="blender-actions"><button className="button primary" disabled={locked || !canRenderMotion} onClick={() => shot && onCommand('render_motion', { shot_id: shot.id })}>Render Blender motion</button><button className="button" disabled={locked || !camera} onClick={() => camera && onCommand('render_preview', { camera_id: camera.id, quality: 'draft' })}>Render Blender still</button></div></section>

    <section className="blender-section" aria-labelledby="blender-output-title"><h3 id="blender-output-title">Motion reference output</h3>{video ? <>
      <div className={`blender-sync ${currentOutput ? 'ready' : 'attention'}`}>{currentOutput ? 'Recorded output for the selected shot and scene' : 'Historical output · differs from the current scene'}</div>
      <video className="blender-video" controls preload="metadata" src={video.url} poster={artifact(preview?.poster_id)?.url} aria-label="Blender motion reference" />
      <p>Technical clay geometry encodes motion only. Approved photographs remain the appearance source for final video.</p>
      <div className="blender-actions"><button className="button" onClick={() => onOpen(video)}>Inspect motion video</button>{blend && <a className="button" href={blend.url} download="technical-motion.blend">Download .blend</a>}{receipt && <button className="button" onClick={() => onOpen(receipt)}>Inspect Blender receipt</button>}</div>
      <dl className="detail-grid blender-output-meta"><dt>Output scene</dt><dd>{preview?.scene_revision_id ?? 'Not recorded'}</dd><dt>Output shot</dt><dd>{preview?.shot_id}</dd><dt>Rendered</dt><dd>{preview?.created_at ? localTime(preview.created_at) : 'Time not recorded'}</dd></dl>
      <button className="button" disabled={locked || !canRenderMotion || !currentOutput || !hasPins || !receipt} onClick={() => shot && onCommand('compile_shot', { shot_id: shot.id })}>Prepare production request</button><p>{hasPins ? 'Compilation verifies the plan, receipt and file hashes. Review the exact request in Compose shots before authorizing video generation.' : 'Select approved references in shot planning before preparing a production request.'}</p>
    </> : <p>No Blender motion output is recorded for this shot. Save a shared plan and render it to produce a playable motion reference, a .blend scene and a validation receipt.</p>}</section>

    {still && <section className="blender-section" aria-labelledby="blender-still-title"><h3 id="blender-still-title">Current camera still</h3><button className="blender-still" onClick={() => onOpen(still)}><img src={still.url} alt="Blender render of the saved camera" /></button><p>Local draft render of the current saved scene. This is a planning output, not a substitute for an approved photograph.</p>{stillReceipt && <button className="button" onClick={() => onOpen(stillReceipt)}>Inspect still receipt</button>}</section>}

    <section className="blender-section" aria-labelledby="blender-status-title"><h3 id="blender-status-title">Production task status</h3><p className="blender-live-status" role="status">{active.length ? `${active.length} local command${active.length === 1 ? '' : 's'} accepted or running` : 'No active local render commands'}</p><p>Recorded command status across this production. Frame progress and subprocess logs are not streamed.</p>
      {commands.length ? <ol className="blender-tasks">{commands.slice(-6).reverse().map((command) => <li key={command.command_id}><div><strong>{humanize(command.operation)}</strong><span className={command.status === 'failed' ? 'blender-error' : ''}>{humanize(command.status)}</span></div><small>{command.accepted_at ? localTime(command.accepted_at) : 'Time not recorded'} · {command.command_id}</small>{command.result?.shot_id && <small>Shot: {command.result.shot_id}</small>}{command.error && <p className="blender-error">{displayText(String(command.error))}</p>}</li>)}</ol> : <p>No local render commands have been recorded yet.</p>}
      {latestEvent && <details className="blender-milestone"><summary>Latest recorded render milestone</summary><dl className="detail-grid"><dt>Event</dt><dd>{humanize(latestEvent.type.replaceAll('.', ' '))}</dd><dt>Recorded</dt><dd>{localTime(latestEvent.recorded_at)}</dd>{latestEvent.payload?.shot_id && <><dt>Shot</dt><dd>{latestEvent.payload.shot_id}</dd></>}{typeof latestEvent.payload?.elapsed_seconds === 'number' && <><dt>Elapsed</dt><dd>{latestEvent.payload.elapsed_seconds.toFixed(2)} s</dd></>}</dl></details>}
    </section>
    <p className="blender-limitations">Supported: camera paths and rigid root transforms. Character rigging, hand contact and facial performance are not simulated. The 3D world stays interactive beside this panel.</p>
  </section>;
}
