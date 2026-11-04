import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, Aperture, Undo2, Image as ImageIcon, ArrowRight, Box, Camera, ChevronDown, Command as CommandIcon, Focus, Footprints, History, Layers3, Orbit, PanelRight, Pause, Play, Search, Send, Video, X } from 'lucide-react';
import type { Artifact, Camera as SceneCamera, Command, Entity, Room, SceneRevision, Snapshot, Take } from '../api/contracts';
import { initialWorkspace, inspectorTarget, isTypingTarget, workspaceReducer, type InspectionFocus } from '../state/workspace';
import { DesignControls } from '../terminal/DesignControls';
import { ShotSequence } from '../terminal/ShotSequence';
import { ShotPlanner } from '../terminal/ShotPlanner';
import { BlenderWorkflow } from '../terminal/BlenderWorkflow';
import { LocationComparison } from '../terminal/LocationComparison';
import { designRoom, latestStill as findLatestStill, roomError, validCamera } from '../state/design';
import { CreativeFlow } from '../terminal/CreativeFlow';
import { ReferenceManager } from '../terminal/ReferenceManager';
import { ReconstructionReview } from '../terminal/ReconstructionReview';
import { ArtifactDialog } from '../terminal/Artifacts';
import { Dialog } from '../terminal/Dialog';
import { Inspector } from '../terminal/Inspector';
import { OutputShelf } from '../terminal/OutputShelf';
import { Sidebar } from '../terminal/Sidebar';
import { isVideoJob, takeSourceDiffers } from '../terminal/records';
import { displayText, modelLabel, providerLabel } from '../terminal/modelPresentation';
import { EmptyState, humanize, IconButton, localTime, money, shortId } from '../terminal/primitives';
import type { CameraDraft, DesignTool, SurfaceName, TerminalViewportProps } from '../terminal/types';
import './terminal.css';

export interface TerminalAppProps {
  snapshot: Snapshot | null;
  connection: 'connecting' | 'connected' | 'disconnected';
  lastEventAt?: string;
  onCommand: (command: Command) => Promise<unknown>;
  onRefresh?: () => void;
  renderViewport: (props: TerminalViewportProps) => ReactNode;
  onReplay?: (runId: string | null) => Promise<void> | void;
  replayRunId?: string | null;
}

export function TerminalApp({ snapshot, connection, lastEventAt, onCommand, onRefresh, renderViewport, onReplay, replayRunId }: TerminalAppProps) {
  const [workspace, dispatch] = useReducer(workspaceReducer, initialWorkspace);
  const [workMode, setWorkMode] = useState<'references' | 'build' | 'shot' | 'blender' | 'review'>('build');
  const [panel, setPanel] = useState<'design' | 'inspect'>(snapshot?.scene?.room ? 'design' : 'inspect');
  const [designTool, setDesignTool] = useState<DesignTool>('scene');
  const [surface, setSurface] = useState<SurfaceName>('walls');
  const [roomDraft, setRoomDraft] = useState<Room | null>(null);
  const [cameraOverride, setCameraOverride] = useState<SceneCamera | undefined>();
  const [ownedPending, setOwnedPending] = useState<Record<string, string>>({});
  const ownedCommands = useRef(new Map<string, string>());
  const failureBaseline = useRef<string | null>(null);
  const [direction, setDirection] = useState('');
  const [motionPreview, setMotionPreview] = useState<{ scene: SceneRevision; camera: SceneCamera } | null>(null);
  const [locationComparisonId, setLocationComparisonId] = useState<string | null>(null);
  const [geometryPreview, setGeometryPreview] = useState<{ reconstructionId: string; scene: SceneRevision; entityId: string; camera?: SceneCamera } | null>(null);
  const [compactReview, setCompactReview] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const pendingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const shownAsyncFailures = useRef(new Set<string>());
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [newResult, setNewResult] = useState<Artifact | null>(null);
  const previousArtifactIds = useRef<Set<string> | null>(null);
  const [palette, setPalette] = useState(false);
  const [search, setSearch] = useState('');
  const [drawer, setDrawer] = useState(false);
  const [cameraDraft, setCameraDraft] = useState<CameraDraft | null>(null);
  const [lightDirty, setLightDirty] = useState(false);
  const [entityDraft, setEntityDraft] = useState<Entity | null>(null);
  const [cameraId, setCameraId] = useState<string | undefined>();
  const [renderStillOpen, setRenderStillOpen] = useState(false);
  const [saveView, setSaveView] = useState(false);
  const [takeDecision, setTakeDecision] = useState<{ operation: 'accept_take' | 'reject_take' | 'repair_take'; take: Take } | null>(null);
  const [decisionReason, setDecisionReason] = useState('');
  const [promptDrafts, setPromptDrafts] = useState<Record<string, string>>({});
  const [replayPicker, setReplayPicker] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [imagePromptOpen, setImagePromptOpen] = useState(false);
  const [seek, setSeek] = useState<{ takeId: string; timeMs: number; nonce: number } | null>(null);
  const previousSceneId = useRef<string | null>(null);
  const sceneHistory = useRef(new Map<string, SceneRevision>());
  const scene = snapshot?.scene ?? null;
  const shots = snapshot?.shots ?? [];
  const defaultShot = shots.find((shot) => shot.scene_revision_id === scene?.id) ?? shots[0];
  const activeShot = shots.find((shot) => shot.id === workspace.active_shot_id) ?? defaultShot;
  const referenceFirst = Boolean(activeShot?.motion || activeShot?.reference_pins?.length);
  const activeTake = snapshot?.takes?.find((take) => take.id === workspace.active_take_id) ?? snapshot?.takes?.filter((take) => take.shot_id === activeShot?.id).at(-1);
  const focus = inspectorTarget(workspace);
  const inspectedScene = focus?.scene_revision_id === scene?.id || !focus ? scene : sceneHistory.current.get(focus.scene_revision_id) ?? null;
  const replay = Boolean(replayRunId ?? workspace.replay_run_id);
  const historical = Boolean(scene && workspace.viewed_scene_revision_id && workspace.viewed_scene_revision_id !== scene.id);
  const operatorReadOnly = replay || historical || compactReview || connection !== 'connected';
  const readOnly = operatorReadOnly || Boolean(geometryPreview);
  const paused = Boolean(snapshot?.production.paused);
  const activeVideoJob = snapshot?.jobs?.find((job) => !job.dispatch_retired && ['reserved', 'submitting', 'queued', 'pending', 'submitted', 'running', 'submission_unknown'].includes(job.status) && isVideoJob(job, snapshot));
  const revision = scene?.id ?? null;
  const baselineRoom = useMemo(() => snapshot ? designRoom(snapshot) : null, [scene, snapshot?.design_defaults]);
  const editRoom = roomDraft ?? baselineRoom;
  const designDirty = Boolean(roomDraft && JSON.stringify(roomDraft) !== JSON.stringify(baselineRoom));
  const selectedCamera = cameraOverride ?? scene?.cameras.find((camera) => camera.id === (cameraId ?? activeShot?.camera_id)) ?? scene?.cameras[0];
  const activeOperations = [...(pending ? [pending] : []), ...(snapshot?.commands?.filter((command) => ['accepted', 'running', 'in_progress'].includes(command.status)).map((command) => String(command.operation)) ?? []), ...Object.values(ownedPending)].filter((operation) => !['pause', 'resume', 'retire_unknown', 'cancel_job'].includes(operation));
  const busy = activeOperations[0] ?? null;
  const editBusy = activeOperations.find((operation) => ['apply_design', 'apply_scene', 'update_camera', 'save_shot', 'undo', 'restore_revision', 'accept_take', 'reject_take'].includes(operation)) ?? null;
  const renderBusy = activeOperations.includes('render_preview');
  const cameraMismatch = Boolean(activeShot && selectedCamera && activeShot.camera_id !== selectedCamera.id);
  const latestStill = snapshot ? findLatestStill(snapshot, selectedCamera?.id) : undefined;
  const currentView = cameraOverride ?? (cameraDraft && selectedCamera ? { ...selectedCamera, ...cameraDraft } : selectedCamera);
  const openTool = (tool: DesignTool) => { setLocationComparisonId(null); if (workMode === 'references' || workMode === 'blender') setWorkMode('build'); setDesignTool(tool); setPanel('design'); setDrawer(true); if (tool === 'camera') dispatch({ type: 'mode', mode: 'camera' }); };

  useEffect(() => { setMotionPreview(null); if (workMode !== 'build') setLocationComparisonId(null); }, [workMode, activeShot?.id]);
  useEffect(() => { setGeometryPreview(null); setLocationComparisonId(null); }, [scene?.id]);
  useEffect(() => { if (workMode === 'references') { setGeometryPreview(null); setLocationComparisonId(null); } }, [workMode]);
  const showReconstruction = useCallback((reconstruction: Record<string, any>) => {
    const mesh = snapshot?.artifacts?.find((item) => item.id === reconstruction.artifact_id);
    if (!scene || !mesh || !Array.isArray(reconstruction.dimensions)) return;
    const asset = snapshot?.reference_assets?.find((item) => item.id === reconstruction.asset_id);
    const entity: Entity = { id: `preview-${reconstruction.id}`, name: `${asset?.name ?? 'Reference asset'} · unplaced preview`, kind: asset?.category === 'character' ? 'actor' : 'prop', position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1], dimensions: reconstruction.dimensions as [number, number, number], asset_id: mesh.id, asset_url: mesh.url, visible: true, state: {} };
    const height = entity.dimensions[2];
    const distance = Math.max(...entity.dimensions) * 3.5;
    const previewCamera = scene.cameras[0] ? { ...scene.cameras[0], focal_length_mm: 35, position: [distance * .7, -distance, height * .7] as [number, number, number], target: [0, 0, height * .5] as [number, number, number] } : undefined;
    setLocationComparisonId(null);
    setGeometryPreview({ reconstructionId: reconstruction.id, scene: { ...scene, entities: [...scene.entities.filter((item) => item.kind === 'light'), entity] }, entityId: entity.id, camera: previewCamera });
    setWorkMode('build'); dispatch({ type: 'mode', mode: 'orbit' });
  }, [scene, snapshot?.artifacts, snapshot?.reference_assets]);

  const staleDecisionSource = Boolean(takeDecision && takeDecision.operation !== 'reject_take' && takeSourceDiffers(takeDecision.take, revision));
  const decisionAlreadyRecorded = Boolean(takeDecision && takeDecision.operation !== 'repair_take' && ['accepted', 'rejected'].includes(snapshot?.takes?.find((take) => take.id === takeDecision.take.id)?.status ?? takeDecision.take.status));
  const viewportScene = useMemo(() => {
    if (!scene) return null;
    // Only shading is previewed immediately. Architecture remains the saved GLB until rebuild.
    const room = roomDraft?.design && !roomError(roomDraft) && scene.room && baselineRoom?.design ? { ...scene.room, design: { ...baselineRoom.design, surfaces: roomDraft.design.surfaces, exposure: roomDraft.design.exposure } } : scene.room;
    return { ...scene, room, entities: entityDraft ? scene.entities.map((item) => item.id === entityDraft.id ? entityDraft : item) : scene.entities };
  }, [scene, entityDraft, roomDraft, baselineRoom]);
  const inspect = useCallback((next: InspectionFocus) => { setLocationComparisonId(null); setWorkMode((current) => current === 'references' || current === 'blender' ? 'build' : current); dispatch({ type: 'inspect', focus: next }); setPanel('inspect'); setDrawer(true); }, []);
  const selectShot = useCallback((id: string) => {
    const shot = snapshot?.shots?.find((item) => item.id === id);
    if (!shot) return;
    const take = snapshot?.takes?.filter((item) => item.shot_id === id).at(-1);
    dispatch({ type: 'select_shot', id, take_id: take?.id ?? null, focus: { kind: 'shot', id, scene_revision_id: shot.scene_revision_id } });
    setCameraId(shot.camera_id); setCameraOverride(undefined); setCameraDraft(null);
  }, [snapshot?.shots, snapshot?.takes]);
  const selectTake = useCallback((take: Take) => { dispatch({ type: 'select_take', id: take.id, focus: { kind: 'take', id: take.id, scene_revision_id: take.scene_revision_id, run_id: take.run_id ?? undefined, artifact_id: take.artifact_id } }); setPanel('inspect'); setDrawer(true); }, []);

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia('(max-width: 1023px)');
    const changed = () => setCompactReview(query.matches);
    changed(); query.addEventListener('change', changed);
    return () => query.removeEventListener('change', changed);
  }, []);

  useEffect(() => {
    const artifacts = snapshot?.artifacts ?? [];
    if (previousArtifactIds.current && workspace.pinned_inspector_target) {
      const added = [...artifacts].reverse().find((item) => !previousArtifactIds.current!.has(item.id) && ['image', 'video', 'response', 'evidence'].includes(item.kind));
      if (added) setNewResult(added);
    }
    previousArtifactIds.current = new Set(artifacts.map((item) => item.id));
    if (!workspace.pinned_inspector_target) setNewResult(null);
  }, [snapshot?.artifacts, workspace.pinned_inspector_target]);

  useEffect(() => {
    if (!snapshot) return;
    const context = replay ? String(replayRunId ?? workspace.replay_run_id) : 'live';
    const failures = snapshot.events?.filter((event) => event.type === 'command.failed') ?? [];
    if (failureBaseline.current !== context) {
      failureBaseline.current = context;
      failures.forEach((failure) => shownAsyncFailures.current.add(failure.id));
      return;
    }
    const failure = [...failures].reverse().find((event) => !shownAsyncFailures.current.has(event.id));
    failures.forEach((event) => shownAsyncFailures.current.add(event.id));
    if (!failure || replay) return;
    const detail = failure.payload?.error ?? failure.payload?.detail ?? failure.payload?.message;
    setError(`Command failed${typeof detail === 'string' ? `: ${detail}` : '. Inspect the recorded failure for details.'}`);
  }, [snapshot?.events, replay, replayRunId]);

  useEffect(() => {
    for (const command of snapshot?.commands ?? []) {
      if (!ownedCommands.current.has(command.command_id) || !['completed', 'failed'].includes(command.status)) continue;
      const shotId = command.result?.shot_id ?? command.result?.shot?.id ?? (command.operation === 'save_shot' ? command.result?.id : undefined);
      if (command.status === 'completed' && ['direction', 'save_shot', 'update_shot_plan'].includes(command.operation) && shotId && !snapshot?.shots?.some((shot) => shot.id === shotId)) continue;
      ownedCommands.current.delete(command.command_id);
      setOwnedPending((current) => { const next = { ...current }; delete next[command.command_id]; return next; });
      if (command.status === 'failed') setError(String(command.error ?? 'Command failed. Inspect the recorded receipt.'));
      else if (shotId && ['direction', 'save_shot', 'update_shot_plan'].includes(command.operation)) { selectShot(shotId); setWorkMode('shot'); }
      else if (command.operation === 'apply_design') setRoomDraft(null);
    }
  }, [snapshot?.commands, snapshot?.shots, selectShot]);

  useEffect(() => {
    if (!scene) return;
    if (!previousSceneId.current && scene.room) setPanel('design');
    const previous = previousSceneId.current ? sceneHistory.current.get(previousSceneId.current) : undefined;
    sceneHistory.current.set(scene.id, scene);
    if (sceneHistory.current.size > 10) sceneHistory.current.delete(sceneHistory.current.keys().next().value!);
    if (previous && previous.id !== scene.id) {
      const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
      if (roomDraft && !same(previous.room, scene.room)) {
        if (same(roomDraft, scene.room)) setRoomDraft(null);
        else setError('The saved room changed while your design draft was open. Your draft is preserved; review it before applying or discard it.');
      }
      if (cameraOverride) {
        const oldCamera = previous.cameras.find((item) => item.id === cameraOverride.id);
        const newCamera = scene.cameras.find((item) => item.id === cameraOverride.id);
        if (!same(oldCamera, newCamera)) {
          if (same(cameraOverride, newCamera)) { setCameraOverride(undefined); setCameraDraft(null); }
          else if (!newCamera) { setCameraOverride(undefined); setCameraDraft(null); setError('The edited camera was removed from the saved scene. Its draft is no longer active.'); }
          else setError('The saved camera changed while your camera draft was open. Your draft is preserved; review it before applying.');
        }
      } else setCameraDraft(null);
      if (entityDraft) {
        const oldEntity = previous.entities.find((item) => item.id === entityDraft.id);
        const newEntity = scene.entities.find((item) => item.id === entityDraft.id);
        if (!same(oldEntity, newEntity)) {
          setEntityDraft(null);
          if (!same(entityDraft, newEntity)) setError('The saved object changed while it was being edited. Its preview was reset to the saved state.');
        }
      }
    }
    dispatch({ type: 'bootstrap', shot_id: defaultShot?.id, take_id: snapshot?.takes?.filter((take) => take.shot_id === defaultShot?.id).at(-1)?.id, scene_revision_id: scene.id });
    if (previousSceneId.current && workspace.viewed_scene_revision_id === previousSceneId.current) dispatch({ type: 'view_revision', id: scene.id });
    previousSceneId.current = scene.id;
  }, [scene?.id, defaultShot?.id]);

  const submit = useCallback(async (operation: Command['operation'], payload: Record<string, unknown> = {}): Promise<boolean> => {
    const administrative = ['pause', 'resume', 'cancel_job', 'retire_unknown'].includes(operation);
    const needsExclusiveWorkflow = ['generate', 'repair_take', 'direction', 'generate_image', 'generate_package'].includes(operation);
    const canReviewPreview = operation === 'review_reconstruction' && Boolean(geometryPreview) && !operatorReadOnly;
    if (readOnly && !canReviewPreview || pendingRef.current || !administrative && (needsExclusiveWorkflow ? busy : editBusy || operation === 'render_preview' && renderBusy)) return false;
    pendingRef.current = true; setPending(operation); setError(null);
    const id = crypto.randomUUID();
    try {
      ownedCommands.current.set(id, operation);
      const receipt = await onCommand({ id, operation, expected_revision: revision, payload });
      if (receipt && typeof receipt === 'object' && 'status' in receipt && receipt.status === 'accepted' && ownedCommands.current.has(id)) setOwnedPending((current) => ({ ...current, [id]: operation }));
      else ownedCommands.current.delete(id);
      if (receipt && typeof receipt === 'object' && 'status' in receipt && receipt.status === 'failed') throw new Error('error' in receipt ? String(receipt.error) : 'Command failed. Inspect the recorded event for details.');
      return true;
    } catch (cause) {
      ownedCommands.current.delete(id);
      setOwnedPending((current) => { const next = { ...current }; delete next[id]; return next; });
      setError(cause instanceof Error ? cause.message : 'Command could not be confirmed. Refresh the recorded state before trying again.');
      return false;
    } finally { pendingRef.current = false; setPending(null); }
  }, [onCommand, readOnly, revision, busy, editBusy, renderBusy, geometryPreview, operatorReadOnly]);

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPalette((value) => !value); return; }
      if (isTypingTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^[123]$/.test(event.key)) { const shot = shots[Number(event.key) - 1]; if (shot) { event.preventDefault(); selectShot(shot.id); } }
      if (event.key.toLowerCase() === 'f') { event.preventDefault(); window.dispatchEvent(new CustomEvent('ghost-director:frame-selection')); }
      if (event.key === 'Escape') { if (document.pointerLockElement) document.exitPointerLock(); else if (workspace.viewport_mode === 'walk') dispatch({ type: 'mode', mode: 'orbit' }); else setDrawer(false); }
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [shots, selectShot, workspace.viewport_mode]);

  const modelBindings = snapshot?.models ?? [];
  const previousVideoModels = modelBindings.filter((model) => model.category === 'video' && model.active === false);
  const categories = Object.values(snapshot?.budget?.categories ?? {}) as Array<Record<string, number>>;
  const budgetKnown = snapshot?.budget?.status !== 'historical_unavailable' && categories.length > 0;
  const reported = categories.reduce((sum, category) => sum + (category.reported_usd ?? 0), 0);
  const estimated = categories.reduce((sum, category) => sum + (category.estimated_usd ?? 0), 0);
  const reserved = categories.reduce((sum, category) => sum + (category.reserved_usd ?? 0), 0);
  const results = useMemo(() => {
    if (!snapshot) return [];
    const revision = snapshot.scene?.id ?? '';
    return [
      ...(snapshot.scene?.entities ?? []).map((item) => ({ label: item.name, focus: { kind: 'entity', id: item.id, scene_revision_id: revision } as InspectionFocus })),
      ...(snapshot.scene?.cameras ?? []).map((item) => ({ label: item.name, focus: { kind: 'camera', id: item.id, scene_revision_id: revision } as InspectionFocus })),
      ...(snapshot.shots ?? []).map((item) => ({ label: item.name, focus: { kind: 'shot', id: item.id, scene_revision_id: item.scene_revision_id } as InspectionFocus })),
      ...(snapshot.tasks ?? []).map((item) => ({ label: `${item.role_label}: ${item.goal}`, focus: { kind: 'task', id: item.id, task_id: item.id, scene_revision_id: item.scene_revision_id ?? revision, run_id: item.run_id } as InspectionFocus })),
      ...(snapshot.takes ?? []).map((item) => ({ label: `Take ${item.id}`, focus: { kind: 'take', id: item.id, scene_revision_id: item.scene_revision_id } as InspectionFocus })),
    ].filter((item) => `${item.label} ${item.focus.id}`.toLowerCase().includes(search.toLowerCase())).slice(0, 30);
  }, [snapshot, search]);

  const changeReplay = async (runId: string | null) => {
    try {
      if (!onReplay) { setError('Recorded replay is unavailable until the runtime provides a saved run.'); return; }
      await onReplay(runId);
      dispatch({ type: 'replay', run_id: runId });
      setReplayPicker(false);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load the recorded run.'); }
  };

  return <div className="terminal designer-terminal" data-work-mode={workMode} data-mode={replay ? 'replay' : 'live'}>
    <header className="terminal-header"><div className="brand"><span className="brand-mark"><Aperture size={17} strokeWidth={1.5} /></span><span className="brand-name">GHOST DIRECTOR</span></div><div className="header-divider" /><h1 className="production-title">{snapshot?.scene?.name ?? snapshot?.production.name ?? 'Production terminal'}</h1><span className="revision"><Layers3 size={11} />r/{shortId(revision, 8)}</span><nav className="workspace-modes" aria-label="Workspace mode">{([{id:'references',label:'References'},{id:'build',label:'Build set'},{id:'shot',label:'Compose shots'},{id:'blender',label:'Blender'},{id:'review',label:'Review takes'}] as const).map(({id,label}) => <button key={id} aria-pressed={workMode === id} className={workMode === id ? 'selected' : ''} onClick={() => { if (id === 'build') openTool('scene'); else if (id === 'shot') openTool('camera'); else if (id === 'blender') { setGeometryPreview(null); setLocationComparisonId(null); setPanel('design'); setDrawer(true); } else if (id === 'review') { setPanel('inspect'); if (activeTake) selectTake(activeTake); } setWorkMode(id); }}>{label}</button>)}</nav><div className="header-actions">{snapshot?.production.fixture && <span className="mode-badge dev">DEV FIXTURE</span>}<span className={`mode-badge ${replay ? 'replay' : 'live'}`}>{replay ? <History size={11} /> : <span className="dot" />}{replay ? 'RECORDED REPLAY' : connection === 'connected' ? 'LIVE' : connection.toUpperCase()}</span>{replay ? <button className="button" onClick={() => changeReplay(null)}>Return to live</button> : <button className="button pause-button" disabled={readOnly || Boolean(pending)} onClick={() => submit(paused ? 'resume' : 'pause')}>{paused ? <Play size={12} /> : <Pause size={12} />}{paused ? 'Resume dispatch' : 'Pause dispatch'}</button>}<button className="button search-shortcut" onClick={() => setPalette(true)}><Search size={12} /><span className="keyboard">⌘ K</span></button><IconButton label="Open inspector" className="inspector-mobile-button" onClick={() => setDrawer(!drawer)}><PanelRight size={15} /></IconButton></div></header>
    <div className="model-strip" aria-label="Model bindings"><span className="model-strip-label">MODEL DESK</span>{modelBindings.length ? modelBindings.map((model, index) => {
      const name = String(model.model ?? model.model_id ?? model.name ?? model.id ?? 'Unspecified model');
      const latest = snapshot?.jobs?.filter((job) => job.model === name).at(-1);
      return <div className={`model-binding ${model.category === 'video' && model.active === true ? 'active-video-model' : ''} ${model.category === 'video' && model.active === false ? 'previous-video-model' : ''}`} key={String(model.id ?? index)}><button title={`${modelLabel(model)} · ${providerLabel(model)}`} onClick={() => latest ? inspect({ kind: 'model_call', id: latest.id, scene_revision_id: revision ?? '' }) : setBudgetOpen(true)}><span className="dot" style={{ color: latest?.status === 'failed' ? 'var(--red)' : latest?.status === 'submission_unknown' ? 'var(--amber)' : latest && ['running', 'queued', 'submitted', 'submitting'].includes(latest.status) ? 'var(--accent)' : latest && ['succeeded', 'completed'].includes(latest.status) ? 'var(--green)' : '#919889' }} /><span className="model-name">{modelLabel(model)}</span><span className="model-state">{latest ? humanize(latest.status) : humanize(String(model.status ?? 'Configured'))}</span></button></div>;
    }) : <span className="model-state">No model bindings recorded</span>}{previousVideoModels.length > 0 && <button className="text-button other-models-button" onClick={() => inspect({ kind: 'models', id: 'previous-video-models', scene_revision_id: revision ?? '' })}>Video history <span className="inspector-id">{previousVideoModels.length}</span></button>}<button className="budget-summary button ghost" onClick={() => setBudgetOpen(true)} aria-label="Inspect budget receipts"><span><span className="budget-label">reported</span><b>{budgetKnown ? money(reported) : '—'}</b></span><span><span className="budget-label">est.</span><b>{budgetKnown ? money(estimated) : '—'}</b></span><span className="budget-reserved"><span className="budget-label">held</span><b>{budgetKnown ? money(reserved) : '—'}</b></span><ChevronDown size={10} /></button></div>
    {snapshot ? <main className="workspace"><Sidebar tool={panel === 'design' ? designTool : undefined} onTool={openTool} snapshot={snapshot} focus={workspace.inspection_focus} onInspect={inspect} onCamera={(id) => { setCameraId(id); setCameraOverride(undefined); setCameraDraft(null); openTool('camera'); }} />
      <div className="center-column"><div className="creative-stage"><ReferenceManager snapshot={snapshot} readOnly={readOnly} busy={busy} onCommand={submit} onOpen={setArtifact} onRefresh={onRefresh} onPreviewReconstruction={showReconstruction} onCompareLocation={(entityId) => { setLocationComparisonId(entityId); setGeometryPreview(null); setWorkMode('build'); setDrawer(true); }} /><section className="stage" aria-label="3D production world"><div className="stage-toolbar"><div className="stage-title"><Box size={13} />{scene?.name ?? 'Production world'}<span>{designDirty ? 'DESIGN DRAFT' : 'REALTIME PREVIEW'}</span></div><div className="stage-tools"><IconButton label="Undo last scene edit" disabled={readOnly || Boolean(editBusy) || !snapshot.undo_target_revision_id} onClick={() => submit('undo')}><Undo2 size={14} /></IconButton><div className="segmented" aria-label="Viewport navigation">{([{ mode: 'orbit', icon: Orbit, label: 'Orbit' }, { mode: 'walk', icon: Footprints, label: 'Walk' }, { mode: 'camera', icon: Camera, label: 'Camera' }] as const).map(({ mode, icon: Icon, label }) => <button type="button" key={mode} disabled={Boolean(motionPreview) || Boolean(geometryPreview)} className={workspace.viewport_mode === mode ? 'selected' : ''} aria-pressed={workspace.viewport_mode === mode} onClick={() => dispatch({ type: 'mode', mode })}><Icon size={12} />{label}</button>)}</div><IconButton label="Frame selection (F)" onClick={() => window.dispatchEvent(new CustomEvent('ghost-director:frame-selection'))}><Focus size={14} /></IconButton><IconButton label="Render still" onClick={() => setRenderStillOpen(true)}><ImageIcon size={14} /></IconButton><button className="button" disabled={readOnly || Boolean(editBusy) || !currentView || !validCamera(currentView)} onClick={() => setSaveView(true)}><Camera size={12} />Save view</button></div></div><div className="viewport-slot">{geometryPreview && <div className="stage-historical">Isolated 3D reconstruction · inspect appearance and scale<button className="text-button" onClick={() => { setGeometryPreview(null); setWorkMode('references'); }}>Return to references</button></div>}{motionPreview && <div className="motion-preview-label">BLOCKING PREVIEW · unsaved playback</div>}{historical && <div className="stage-historical"><History size={13} />Historical selection · commands are read-only<button className="text-button" onClick={() => scene && dispatch({ type: 'view_revision', id: scene.id })}>Return to current</button></div>}{renderViewport({ scene: motionPreview?.scene ?? geometryPreview?.scene ?? viewportScene, selectedEntityId: geometryPreview?.entityId ?? (workspace.inspection_focus?.kind === 'entity' ? workspace.inspection_focus.id : undefined), activeCameraId: cameraId ?? activeShot?.camera_id, mode: motionPreview ? 'camera' : geometryPreview ? 'orbit' : workspace.viewport_mode, cameraOverride: motionPreview?.camera ?? geometryPreview?.camera ?? (cameraOverride && validCamera(cameraOverride) ? cameraOverride : undefined), onSelectSurface: (selectedSurface) => { setSurface(selectedSurface); openTool('materials'); }, onSelectEntity: (id) => inspect({ kind: 'entity', id, scene_revision_id: revision ?? '' }), onCameraDraft: motionPreview || geometryPreview ? () => undefined : setCameraDraft, onModeChange: (mode) => dispatch({ type: 'mode', mode }), readOnly: replay || historical || Boolean(motionPreview) || Boolean(geometryPreview) })}</div><div className="stage-footer"><span>{scene?.units.toUpperCase() ?? 'METERS'} · Z-UP</span><span>{workspace.viewport_mode === 'camera' ? `${currentView?.focal_length_mm ?? '—'} MM` : 'FREE VIEW'}</span><span className="stage-help">{workspace.viewport_mode === 'walk' ? 'Click world to enter · WASD move · Esc release' : 'Drag to orbit · Shift drag to pan · Scroll to dolly'}</span></div></section><CreativeFlow snapshot={snapshot} focus={workspace.inspection_focus} onInspect={inspect} onOpen={setArtifact} onReplay={() => replay ? changeReplay(null) : setReplayPicker(true)} replay={replay} /></div>
      <ShotSequence snapshot={snapshot} activeShotId={activeShot?.id} onShot={selectShot} onCreate={() => { setWorkMode('shot'); document.getElementById('direction-input')?.focus(); }} />
      <OutputShelf focusedTaskId={focus?.kind === 'task' ? focus.id : undefined} snapshot={snapshot} activeShotId={activeShot?.id ?? null} take={activeTake} onShot={selectShot} onInspectTake={selectTake} onOpen={setArtifact} seek={seek} pausePlayback={Boolean(artifact?.mime_type.startsWith('video/'))} />
      <form className="composer" onSubmit={async (event) => { event.preventDefault(); if (!direction.trim()) return; if (await submit('direction', { text: direction, shot_id: null })) setDirection(''); }}>
        <div className="new-shot-composer"><CommandIcon size={16} className="composer-icon" /><div className="composer-field"><label className="composer-scope" htmlFor="direction-input">{replay ? 'READ-ONLY REPLAY' : 'NEW SHOT BRIEF'} <span>·</span> Meta Producer → Director of Photography</label><input id="direction-input" value={direction} onChange={(event) => setDirection(event.target.value)} placeholder={replay ? 'Return to live to create a shot' : 'Describe the next shot, action, and framing…'} disabled={readOnly} autoComplete="off" /></div><IconButton label="Generate Meta image" disabled={readOnly || Boolean(busy) || paused} onClick={() => setImagePromptOpen(true)}><ImageIcon size={15} /></IconButton><button className="button" type="submit" disabled={readOnly || !direction.trim() || Boolean(busy) || paused}><Send size={12} />Create shot</button></div>
        <div className="generate-bar"><span><strong>{activeShot?.name ?? 'No shot selected'}</strong><small>{busy ? `${humanize(busy)} · in progress` : cameraMismatch ? 'Select a matching shot or save this view as a new shot' : designDirty || cameraOverride || entityDraft || lightDirty ? 'Apply your design / camera / object edits before generating' : direction.trim() ? 'Create the new shot first, or clear its brief' : `${activeShot?.duration ?? '—'}s · saved scene · ${promptDrafts[activeShot?.id ?? ''] ? 'edited prompt' : 'recorded shot action'}`}{referenceFirst && ' · original images + clay motion'}{paused && ' · dispatch paused'}</small></span><div className="generation-actions">{!referenceFirst && <span className="legacy-generation"><small>Legacy workflow</small><button type="button" className="button" disabled={readOnly || !activeShot || Boolean(busy) || Boolean(activeVideoJob) || paused || cameraMismatch || designDirty || lightDirty || Boolean(entityDraft) || Boolean(cameraOverride) || Boolean(direction.trim())} title="Legacy paid generation uses rendered frames for appearance. Use the shared production request for approved photographic references and clay motion." onClick={() => activeShot && submit('generate', { shot_id: activeShot.id, ...(promptDrafts[activeShot.id] ? { prompt_override: promptDrafts[activeShot.id] } : {}) })}>Generate from rendered frames</button></span>}<button type="button" className="button primary" disabled={!activeShot || Boolean(geometryPreview)} onClick={() => { setPanel('design'); setWorkMode('shot'); setDrawer(true); }}><Video size={13} />Review production request</button></div></div>
        {error && <div className="composer-error" role="alert"><AlertCircle size={14} /><span>{displayText(error)}</span><IconButton label="Dismiss error" onClick={() => setError(null)}><X size={12} /></IconButton></div>}
      </form>
      </div>{geometryPreview ? <ReconstructionReview reconstructionId={geometryPreview.reconstructionId} snapshot={snapshot} disabled={operatorReadOnly || Boolean(busy)} onCommand={submit} onInspect={setArtifact} onClose={() => { setGeometryPreview(null); setWorkMode('references'); }} /> : locationComparisonId ? <LocationComparison snapshot={snapshot} entityId={locationComparisonId} onOpen={setArtifact} onClose={() => { setLocationComparisonId(null); setWorkMode('references'); }} onDesign={(tool) => { setLocationComparisonId(null); openTool(tool); }} /> : workMode === 'blender' ? <aside className={`inspector blender-panel ${drawer ? 'drawer-open' : ''}`} aria-label="Blender production controls"><div className="inspector-heading"><h2>Blender production</h2><IconButton label="Close Blender panel" onClick={() => { setWorkMode('build'); setPanel('design'); setDrawer(false); }}><X size={14} /></IconButton></div><BlenderWorkflow snapshot={snapshot} shot={activeShot} disabled={readOnly || Boolean(busy) || designDirty || lightDirty || Boolean(entityDraft) || Boolean(cameraOverride)} onCommand={submit} onOpen={setArtifact} onPlan={() => { setWorkMode('shot'); setPanel('design'); setDrawer(true); }} /></aside> : panel === 'design' && workMode === 'shot' ? <aside className={`inspector shot-plan-panel ${drawer ? 'drawer-open' : ''}`} aria-label="Shot plan controls"><div className="inspector-heading"><h2>Direct the shot</h2><IconButton label="Close shot plan" onClick={() => setDrawer(false)}><X size={14} /></IconButton></div><ShotPlanner snapshot={snapshot} shot={activeShot} readOnly={readOnly} onCommand={submit} onOpen={setArtifact} onPreview={setMotionPreview} /></aside> : panel === 'design' ? <DesignControls snapshot={snapshot} room={editRoom} baseline={baselineRoom} dirty={designDirty} tool={designTool} surface={surface} camera={currentView} cameraDirty={Boolean(cameraOverride)} lightDirty={lightDirty || Boolean(entityDraft)} readOnly={readOnly} busy={editBusy} renderBusy={renderBusy} drawerOpen={drawer} onRoom={setRoomDraft} onSurface={setSurface} onCancel={() => setRoomDraft(null)} onApply={() => editRoom && submit('apply_design', { room: editRoom })} onCamera={(camera) => { setCameraOverride(camera); setCameraDraft(camera); }} onApplyCamera={() => { if (!currentView) return; const { id, name: _name, ...camera } = currentView; submit('update_camera', { camera_id: id, camera }); }} onSaveCamera={() => setSaveView(true)} onCancelCamera={() => { setCameraOverride(undefined); setCameraDraft(null); }} onRender={(quality) => selectedCamera && submit('render_preview', { camera_id: selectedCamera.id, quality })} onOpen={setArtifact} onLightDirty={setLightDirty} onLightDraft={setEntityDraft} onLightApply={(patch) => submit('apply_scene', { patches: [patch] })} onClose={() => setDrawer(false)} onInspect={() => setPanel('inspect')} /> : <Inspector inspectionScene={inspectedScene} onReturnCurrent={() => { dispatch({ type: 'unpin' }); if (focus && revision) inspect({ ...focus, scene_revision_id: revision }); }} newResult={newResult} onOpenNewResult={() => { setArtifact(newResult); setNewResult(null); }} snapshot={snapshot} focus={focus} pinned={Boolean(workspace.pinned_inspector_target)} drawerOpen={drawer} readOnly={readOnly || Boolean(editBusy)} canGoBack={workspace.history.length > 0} onPin={() => dispatch({ type: workspace.pinned_inspector_target ? 'unpin' : 'pin' })} onBack={() => dispatch({ type: 'back' })} onClose={() => setDrawer(false)} onInspect={inspect} onOpen={setArtifact} onTakeAction={(operation, take) => { setTakeDecision({ operation, take }); setDecisionReason(''); }} onCommand={submit} onDraftPrompt={(id, text) => setPromptDrafts((drafts) => ({ ...drafts, [id]: text }))} promptDrafts={promptDrafts} onEntityDraft={setEntityDraft} onSeek={(takeId, timeMs) => { const take = snapshot.takes?.find((item) => item.id === takeId); if (take) selectTake(take); setSeek({ takeId, timeMs, nonce: Date.now() }); }} />}
    </main> : <div className="viewport-empty"><EmptyState icon={<Aperture size={30} strokeWidth={1} />} title={connection === 'disconnected' ? 'Production is offline' : 'Opening the production'} detail={connection === 'disconnected' ? 'Reconnect to the local runtime to load saved scenes and results.' : 'Loading the saved scene, tasks, and artifact receipts.'} /></div>}
    {connection !== 'connected' && <div className="connection-banner" role="status"><AlertCircle size={13} />{connection === 'connecting' ? 'Connecting to the production runtime…' : `Disconnected · remote work may continue${lastEventAt ? ` · last event ${localTime(lastEventAt)}` : ''}`}{onRefresh && <button className="text-button" onClick={onRefresh}>Reconnect</button>}</div>}
    <div className="sr-only" role="status" aria-live="polite">{pending ? `${humanize(pending)} command in progress` : error ?? (replay ? 'Recorded replay. All production commands are disabled.' : paused ? 'New dispatch is paused. Existing remote jobs may continue.' : '')}</div>
    {renderStillOpen && <Dialog title="Render the saved scene" onClose={() => setRenderStillOpen(false)} footer={<><button className="button" disabled={readOnly || Boolean(editBusy) || renderBusy || designDirty || lightDirty || Boolean(entityDraft) || Boolean(cameraOverride) || !selectedCamera} onClick={() => selectedCamera && submit('render_preview', { camera_id: selectedCamera.id, quality: 'draft' })}>Draft still</button><button className="button primary" disabled={readOnly || Boolean(editBusy) || renderBusy || designDirty || lightDirty || Boolean(entityDraft) || Boolean(cameraOverride) || !selectedCamera} onClick={() => selectedCamera && submit('render_preview', { camera_id: selectedCamera.id, quality: 'final' })}>Final still</button></>}><p>Camera: <strong>{selectedCamera?.name ?? 'No saved camera'}</strong> · current scene r/{shortId(revision, 8)}</p><p className="inspector-subtitle">Local Blender render. Draft is faster; final uses Cycles for lighting, shadows, and camera depth of field. Neither action calls a paid model.</p>{(designDirty || cameraOverride || entityDraft || lightDirty) && <div className="notice-box">Apply design, camera, and object / light edits before rendering.</div>}{busy === 'render_preview' && <div className="notice-box" role="status">Rendering the saved scene. The result will appear here when the local renderer completes.</div>}{latestStill && <button className="render-preview-card" onClick={() => { setRenderStillOpen(false); setArtifact(latestStill); }}><img src={latestStill.url} alt="Latest saved render" /><span><strong>{humanize(latestStill.metadata?.quality ?? 'Saved')} render · inspect full image</strong><small>{latestStill.metadata?.scene_revision_id === revision ? 'Current scene' : 'Earlier scene · not current'} · {selectedCamera?.name}</small></span></button>}</Dialog>}
    {artifact && <ArtifactDialog artifact={artifact} onClose={() => setArtifact(null)} />}
    {palette && <Dialog title="Find in this production" className="command-palette" onClose={() => { setPalette(false); setSearch(''); }}><div className="search-input-wrap"><Search size={17} /><input autoFocus aria-label="Search production" placeholder="Objects, cameras, shots, tasks, takes…" value={search} onChange={(event) => setSearch(event.target.value)} /></div><div className="command-results">{results.map((result) => <button key={`${result.focus.kind}:${result.focus.id}`} className="command-result" onClick={() => { result.focus.kind === 'shot' ? selectShot(result.focus.id) : inspect(result.focus); setPalette(false); setSearch(''); }}><Search size={12} /><span>{displayText(result.label)}</span><span className="command-kind">{result.focus.kind}</span></button>)}{!results.length && <EmptyState compact title="No matching records" />}</div></Dialog>}
    {saveView && currentView && <SaveViewDialog camera={currentView} currentName={activeShot?.name} defaultDuration={activeShot?.duration ?? 8} onClose={() => setSaveView(false)} onSave={async (name, duration, action) => { if (await submit('save_shot', { workflow: 'reference-motion-v1', camera: currentView ? (({ id: _id, name: _name, ...draft }) => draft)(currentView) : null, name, duration, action })) setSaveView(false); }} pending={Boolean(pending)} />}
    {takeDecision && <Dialog title={`${humanize(takeDecision.operation)} · ${shortId(takeDecision.take.id, 12)}`} onClose={() => !pending && setTakeDecision(null)} footer={<><button className="button" disabled={Boolean(pending)} onClick={() => setTakeDecision(null)}>Cancel</button><button className={`button ${takeDecision.operation === 'reject_take' ? 'danger' : 'primary'}`} disabled={readOnly || staleDecisionSource || decisionAlreadyRecorded || Boolean(pending) || takeDecision.operation === 'reject_take' && !decisionReason.trim()} onClick={async () => {
      const take = takeDecision.take;
      if (staleDecisionSource || decisionAlreadyRecorded) return;
      if (await submit(takeDecision.operation, { take_id: take.id, package_id: take.package_id, source_scene_revision_id: take.scene_revision_id, reason: decisionReason.trim() })) setTakeDecision(null);
    }}>{pending ? 'Recording…' : humanize(takeDecision.operation)}</button></>}><dl className="detail-grid"><dt>Exact take</dt><dd>{takeDecision.take.id}</dd><dt>Source package</dt><dd>{takeDecision.take.package_id}</dd><dt>Expected accepted head</dt><dd>{revision}</dd></dl>{staleDecisionSource && <div className="notice-box" role="alert">Source scene differs; inspect or restore before applying.</div>}{decisionAlreadyRecorded && <div className="notice-box" role="alert">This take already has a recorded creative decision.</div>}<div className="notice-box">{takeDecision.operation === 'accept_take' ? 'Record your creative acceptance and apply only the proposed bounded scene changes shown below. Future shots inherit the resulting scene revision.' : takeDecision.operation === 'reject_take' ? 'Record your decision. This preserves the take and current scene, and does not submit another generation.' : 'Request one bounded repair of the recorded continuity failures. The runtime checks the repair limit and reserves a separate cost before dispatch.'}</div>{takeDecision.operation === 'accept_take' && <pre>{JSON.stringify(takeDecision.take.proposed_patch ?? [], null, 2)}</pre>}{takeDecision.operation === 'repair_take' && <div style={{ marginBottom: 12 }}>{snapshot?.observations?.filter((item) => item.take_id === takeDecision.take.id && item.status !== 'pass').map((item) => <p key={item.id}>{humanize(item.status)}: {displayText(item.assertion)} — {displayText(item.detail)}</p>)}</div>}<label className="dialog-label" htmlFor="decision-reason">{takeDecision.operation === 'reject_take' ? 'Decision reason (required)' : 'Decision note'}</label><textarea id="decision-reason" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value)} placeholder="Describe the decision or the specific correction." /></Dialog>}
    {replayPicker && <Dialog title="Replay a recorded run" onClose={() => setReplayPicker(false)}><p style={{ marginBottom: 12, color: 'var(--muted)' }}>Inspect saved scenes, events, and outputs. Replay cannot submit commands or call a provider.</p>{snapshot?.runs?.length ? snapshot.runs.map((run) => <button className="artifact-row" style={{ marginBottom: 7 }} key={run.id} onClick={() => changeReplay(run.id)}><History size={15} /><span className="artifact-row-copy"><span className="artifact-name">{displayText(run.name ?? run.direction ?? run.id)}</span><span className="artifact-hash">{localTime(run.created_at)} · {run.status ?? 'recorded'}</span></span><ArrowRight size={13} /></button>) : <EmptyState title="No saved run yet" detail="A run becomes available after real production work is recorded." />}</Dialog>}
    {budgetOpen && <Dialog title="Budget & model receipts" onClose={() => setBudgetOpen(false)}><dl className="detail-grid"><dt>Total ceiling</dt><dd>{money(snapshot?.budget?.ceiling_usd)}</dd><dt>Provider reported</dt><dd>{budgetKnown ? money(reported) : '—'}</dd><dt>Estimated charges</dt><dd>{budgetKnown ? money(estimated) : '—'}</dd><dt>Reserved for unresolved work</dt><dd>{budgetKnown ? money(reserved) : '—'}</dd><dt>Available</dt><dd>{money(snapshot?.budget?.remaining_usd)}</dd></dl><div className="notice-box">{budgetKnown ? 'Reported and estimated charges are separate accounting bases. Reservations stay held while submission or billing is uncertain.' : 'Historical balance unavailable. This recorded snapshot does not contain a budget ledger.'}</div>{Object.entries(snapshot?.budget?.categories ?? {}).map(([name, value]) => { const category = value as Record<string, number>; return <div className="context-row" key={name}><strong>{humanize(name)}</strong><span>{money(category.remaining_usd)} available / {money(category.ceiling_usd)}</span></div>; })}<h3 style={{ fontSize: 12, margin: '18px 0 8px' }}>Configured model bindings</h3>{modelBindings.map((model, index) => <div className="context-row" key={index}><span>{modelLabel(model)}</span><code>{providerLabel(model)}</code></div>)}</Dialog>}
    {imagePromptOpen && <ImagePromptDialog pending={Boolean(pending)} onClose={() => setImagePromptOpen(false)} onGenerate={async (prompt) => { if (await submit('generate_image', { prompt })) setImagePromptOpen(false); }} />}
  </div>;
}

function SaveViewDialog({ camera, currentName, defaultDuration, onClose, onSave, pending }: { camera: CameraDraft; currentName?: string; defaultDuration: number; onClose: () => void; onSave: (name: string, duration: number, action: string) => Promise<void>; pending: boolean }) {
  const [name, setName] = useState(currentName ? `${currentName} · new view` : 'New shot');
  const [duration, setDuration] = useState(defaultDuration);
  const [action, setAction] = useState('');
  return <Dialog title="Save camera as a shot" onClose={onClose} footer={<><button className="button" disabled={pending} onClick={onClose}>Cancel</button><button className="button primary" disabled={pending || !name.trim() || !action.trim() || !Number.isInteger(duration) || duration < 4 || duration > 30} onClick={() => onSave(name.trim(), duration, action.trim())}>{pending ? 'Saving…' : 'Save shot'}</button></>}><label className="dialog-label" htmlFor="shot-name">Shot name</label><input id="shot-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} /><div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '14px 0' }}><label className="dialog-label" htmlFor="shot-duration" style={{ margin: 0 }}>Duration (seconds)</label><input id="shot-duration" type="number" min={4} max={30} step={1} style={{ width: 70 }} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /><span className="inspector-id">{camera.focal_length_mm.toFixed(1)} mm · shared motion plan</span></div><label className="dialog-label" htmlFor="shot-action">What happens in this shot?</label><textarea id="shot-action" value={action} onChange={(event) => setAction(event.target.value)} placeholder="Describe the visible action and the state that must remain consistent." /></Dialog>;
}

function ImagePromptDialog({ pending, onClose, onGenerate }: { pending: boolean; onClose: () => void; onGenerate: (prompt: string) => Promise<void> }) {
  const [prompt, setPrompt] = useState('');
  return <Dialog title="Generate a Meta image" onClose={onClose} footer={<><button className="button" disabled={pending} onClick={onClose}>Cancel</button><button className="button primary" disabled={pending || !prompt.trim()} onClick={() => onGenerate(prompt.trim())}>{pending ? 'Submitting…' : 'Generate image'}</button></>}><label className="dialog-label" htmlFor="image-prompt">Application prompt</label><textarea autoFocus id="image-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Describe the image and its intended role in this production." /><p className="inspector-subtitle">Uses the configured Meta image model. The runtime records the prompt, output, and cost receipt.</p></Dialog>;
}

export default TerminalApp;
