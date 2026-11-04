import { useEffect, useState } from 'react';
import type { Artifact, Camera, Command, MotionPlan, SceneRevision, ShotPlan, Snapshot } from '../api/contracts';
import { defaultMotion, sampleMotion } from '../state/motion';
import { SubmissionReview } from './SubmissionReview';
import { ApprovedReferencePicker } from './ApprovedReferencePicker';
import './shot-planner.css';

type Props = { snapshot: Snapshot; shot?: ShotPlan; readOnly: boolean; onCommand: (operation: Command['operation'], payload: Record<string, unknown>) => unknown; onOpen: (artifact: Artifact) => void; onPreview?: (preview: { scene: SceneRevision; camera: Camera } | null) => void };
export function ShotPlanner({ snapshot, shot, readOnly, onCommand, onOpen, onPreview }: Props) {
  const scene = snapshot.scene;
  const [motion, setMotion] = useState<MotionPlan | null>(null);
  const [action, setAction] = useState('');
  const [duration, setDuration] = useState(8);
  const [pins, setPins] = useState<NonNullable<ShotPlan['reference_pins']>>([]);
  const [seconds, setSeconds] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    setMotion(shot && scene ? shot.motion ?? defaultMotion(scene, shot) : null);
    setAction(shot?.action ?? ''); setDuration(shot?.duration ?? 8); setPins(shot?.reference_pins ?? []);
    setDirty(false); setSeconds(0); setPlaying(false); setPreviewing(false); onPreview?.(null);
  }, [shot?.id, scene?.id]);
  useEffect(() => {
    if (!playing) return;
    let handle = 0; let start: number | null = null; const from = seconds;
    const tick = (now: number) => { start ??= now; const next = from + (now - start) / 1000; setSeconds(Math.min(duration, next)); if (next < duration) handle = requestAnimationFrame(tick); else setPlaying(false); };
    handle = requestAnimationFrame(tick); return () => cancelAnimationFrame(handle);
  }, [playing, duration]);
  useEffect(() => { if (scene && shot && motion && previewing) onPreview?.(sampleMotion(scene, shot, motion, seconds)); }, [motion, seconds, previewing]);
  useEffect(() => () => onPreview?.(null), []);
  if (!shot || !scene || !motion) return <div className="shot-planner"><p>Select a shot, or give the crew a direction to create one.</p></div>;
  const previews = (snapshot.motion_previews ?? []).filter((p) => p.shot_id === shot.id);
  const preview = previews.at(-1);
  const packages = (snapshot.packages ?? []).filter((p) => p.shot_id === shot.id && p.workflow === 'reference-motion-v1');
  const pkg = packages.at(-1);
  const active = (snapshot.commands ?? []).some((c) => ['accepted', 'running'].includes(c.status) && ['update_shot_plan','render_motion','compile_shot','generate_package'].includes(c.operation));
  const disabled = readOnly || active;
  const stale = shot.scene_revision_id !== scene.id;
  const artifact = (id: string) => snapshot.artifacts?.find((a) => a.id === id);
  const changeMotion = (value: MotionPlan) => { setMotion(value); setDirty(true); };
  const changeDuration = (value: number) => {
    if (!Number.isInteger(value) || value < 4 || value > 30) return;
    const scale = value / duration;
    changeMotion({ ...motion, camera_keyframes: motion.camera_keyframes.map((k) => ({ ...k, time_seconds: +(k.time_seconds * scale).toFixed(3) })), tracks: (motion.tracks ?? []).map((t) => ({ ...t, keyframes: t.keyframes.map((k) => ({ ...k, time_seconds: +(k.time_seconds * scale).toFixed(3) })) })) });
    setDuration(value); setSeconds(0);
  };
  const ready = Boolean(shot.motion && !dirty && !stale);
  return <section className="shot-planner" aria-label="Shared shot plan">
    <div className="plan-heading"><span className="eyebrow">ONE PLAN · TWO WORLDS</span><h2>{shot.name}</h2><p>Appearance from approved references. Motion from this timeline.</p></div>
    {stale && <div className="plan-warning"><p>The set changed. Review starting positions and save a new plan before rendering.</p><button disabled={readOnly} onClick={() => { const current = defaultMotion(scene, shot); if (!current) return; changeMotion({ ...motion, camera_keyframes: [current.camera_keyframes[0], ...motion.camera_keyframes.slice(1)], tracks: (motion.tracks ?? []).flatMap(track => { const entity = scene.entities.find(e => e.id === track.entity_id); return entity ? [{ ...track, keyframes: [{ time_seconds: 0, position: entity.position, rotation: entity.rotation }, ...track.keyframes.slice(1)] }] : []; }) }); }}>Use current starting positions</button></div>}
    <label>Performance direction<textarea value={action} disabled={readOnly} onChange={(e) => { setAction(e.target.value); setDirty(true); }} /></label>
    <div className="plan-transport"><label>Duration <input aria-label="Shot duration seconds" type="number" min="4" max="30" value={duration} disabled={readOnly} onChange={(e) => changeDuration(Number(e.target.value))} /> s</label><span>24 fps · 16:9</span></div>
    <div className="plan-playback"><button onClick={() => { setPreviewing(true); if (seconds >= duration) setSeconds(0); setPlaying(!playing); }}>{playing ? 'Pause' : 'Play blocking'}</button><input aria-label="Motion playhead" type="range" min="0" max={duration} step="0.01" value={seconds} onChange={(e) => { setPlaying(false); setPreviewing(true); setSeconds(Number(e.target.value)); }} /><output>{seconds.toFixed(2)} s</output>{previewing && <button onClick={() => { setPreviewing(false); setPlaying(false); onPreview?.(null); }}>Exit preview</button>}</div>
    <div className="plan-timeline" aria-label="Camera timeline">{motion.camera_keyframes.map((key, i) => <span key={i} style={{ left: `${key.time_seconds / duration * 100}%` }} title={`${key.time_seconds}s · ${key.focal_length_mm} mm`}>{i + 1}</span>)}</div>
    <details open><summary>Camera path · {motion.camera_keyframes.length} keyframes</summary>{motion.camera_keyframes.map((key, i) => <div className="plan-key" key={i}><strong>{key.time_seconds.toFixed(2)}s {i === 0 ? '· saved camera' : ''}</strong>{(['position', 'target'] as const).map((field) => <div className="plan-vector" key={field}><span>{field}</span>{key[field].map((v, axis) => <input key={axis} aria-label={`Camera ${i + 1} ${field} ${'XYZ'[axis]}`} type="number" step="0.1" value={Number(v.toFixed(3))} disabled={readOnly || i === 0} onChange={(e) => { if (!e.target.value.trim()) return; const value = Number(e.target.value); if (!Number.isFinite(value)) return; changeMotion({ ...motion, camera_keyframes: motion.camera_keyframes.map((k, n) => n === i ? { ...k, [field]: k[field].map((x, a) => a === axis ? value : x) as [number,number,number] } : k) }); }} />)}</div>)}</div>)}<button disabled={disabled || motion.camera_keyframes.length >= 12} onClick={() => { const n = motion.camera_keyframes.length; const a = motion.camera_keyframes[n-2], b = motion.camera_keyframes[n-1]; changeMotion({ ...motion, camera_keyframes: [...motion.camera_keyframes.slice(0,-1), { ...b, time_seconds: (a.time_seconds + b.time_seconds) / 2 }, b] }); }}>Add camera beat</button></details>
    <details><summary>Blocking · {(motion.tracks ?? []).length} moving objects</summary><select aria-label="Add moving object" disabled={disabled} value="" onChange={(e) => { const entity = scene.entities.find((x) => x.id === e.target.value); if (!entity) return; changeMotion({ ...motion, tracks: [...(motion.tracks ?? []), { entity_id: entity.id, keyframes: [{ time_seconds: 0, position: entity.position, rotation: entity.rotation }, { time_seconds: duration, position: entity.position, rotation: entity.rotation }] }] }); }}><option value="">Add an actor or prop…</option>{scene.entities.filter((e) => e.visible && ['actor','prop','set'].includes(e.kind) && !e.state?.included_in_entity_id && !motion.tracks?.some((t) => t.entity_id === e.id)).map((e) => <option value={e.id} key={e.id}>{e.name}</option>)}</select>{motion.tracks?.map((track, index) => <div className="plan-key" key={track.entity_id}><strong>{scene.entities.find(e => e.id === track.entity_id)?.name}</strong><div className="plan-vector"><span>End XYZ</span>{track.keyframes.at(-1)!.position.map((value, axis) => <input type="number" step="0.1" value={value} disabled={readOnly} key={axis} aria-label={`${track.entity_id} end ${'XYZ'[axis]}`} onChange={(e) => { if (!e.target.value.trim()) return; const number = Number(e.target.value); if (!Number.isFinite(number)) return; changeMotion({ ...motion, tracks: motion.tracks!.map((t, i) => i === index ? { ...t, keyframes: t.keyframes.map((key, k) => k === t.keyframes.length-1 ? { ...key, position: key.position.map((v,a) => a === axis ? number : v) as [number,number,number] } : key) } : t) }); }} />)}</div><button disabled={readOnly} onClick={() => changeMotion({ ...motion, tracks: motion.tracks!.filter(t => t.entity_id !== track.entity_id) })}>Remove movement</button></div>)}<p className="plan-note">Root movement only. Hand, face and limb performances are directed in text; they are not simulated here.</p></details>
    <ApprovedReferencePicker snapshot={snapshot} pins={pins} disabled={disabled} onChange={(next) => { setPins(next); setDirty(true); }} />
    <div className="plan-actions"><button className="primary-button" disabled={disabled || !action.trim()} onClick={() => onCommand('update_shot_plan', { shot_id: shot.id, action, duration, motion, reference_pins: pins })}>Save shared plan</button><button disabled={disabled || !ready} onClick={() => onCommand('render_motion', { shot_id: shot.id })}>Render motion</button></div>
    {preview && <div className="plan-evidence"><strong>Actual Blender output</strong>{artifact(preview.video_id) && <video controls preload="metadata" src={artifact(preview.video_id)!.url} aria-label="Technical motion preview" />}<button onClick={() => { const a = artifact(preview.receipt_id); if (a) onOpen(a); }}>Inspect timeline receipt</button><button disabled={disabled || !ready || !pins.length} onClick={() => onCommand('compile_shot', { shot_id: shot.id })}>Prepare submission</button></div>}
    {pkg && <SubmissionReview snapshot={snapshot} pkg={pkg} disabled={disabled || !ready || Boolean(snapshot.production.paused)} onOpen={onOpen} onCommand={onCommand} />}
  </section>;
}
