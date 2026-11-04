import type { Camera, MotionPlan, SceneRevision, ShotPlan } from '../api/contracts';

type Vec = [number, number, number];
const mix = (a: Vec, b: Vec, f: number): Vec => a.map((v, i) => v + (b[i] - v) * f) as Vec;
function interval<T extends { time_seconds: number }>(keys: T[], seconds: number): [T, T, number] {
  const last = keys.length - 1;
  if (seconds <= keys[0].time_seconds) return [keys[0], keys[0], 0];
  for (let i = 1; i <= last; i++) if (seconds <= keys[i].time_seconds) return [keys[i - 1], keys[i], (seconds - keys[i - 1].time_seconds) / (keys[i].time_seconds - keys[i - 1].time_seconds)];
  return [keys[last], keys[last], 0];
}
export function defaultMotion(scene: SceneRevision, shot: ShotPlan): MotionPlan | null {
  const camera = scene.cameras.find((c) => c.id === shot.camera_id);
  if (!camera) return null;
  const key = { position: camera.position, target: camera.target, focal_length_mm: camera.focal_length_mm };
  return { version: 1, interpolation: 'linear', camera_keyframes: [{ ...key, time_seconds: 0 }, { ...key, time_seconds: shot.duration }], tracks: (shot.proposed_patch ?? []).flatMap((patch) => {
    const entity = scene.entities.find((e) => e.id === patch.entity_id);
    if (!entity || (!patch.position && !patch.rotation)) return [];
    return [{ entity_id: entity.id, keyframes: [{ time_seconds: 0, position: entity.position, rotation: entity.rotation }, { time_seconds: shot.duration, position: patch.position ?? entity.position, rotation: patch.rotation ?? entity.rotation }] }];
  }) };
}
export function sampleMotion(scene: SceneRevision, shot: ShotPlan, motion: MotionPlan, seconds: number): { scene: SceneRevision; camera: Camera } | null {
  const source = scene.cameras.find((camera) => camera.id === shot.camera_id);
  if (!source || motion.camera_keyframes.length < 2 || !Number.isFinite(seconds)) return null;
  const [a, b, f] = interval(motion.camera_keyframes, seconds);
  const camera = { ...source, position: mix(a.position, b.position, f), target: mix(a.target, b.target, f), focal_length_mm: a.focal_length_mm + (b.focal_length_mm - a.focal_length_mm) * f };
  const entities = scene.entities.map((entity) => {
    const track = motion.tracks?.find((item) => item.entity_id === entity.id);
    if (!track || track.keyframes.length < 2) return entity;
    const [a, b, f] = interval(track.keyframes, seconds);
    return { ...entity, position: mix(a.position, b.position, f), rotation: mix(a.rotation, b.rotation, f) };
  });
  if ([...camera.position, ...camera.target, camera.focal_length_mm, ...entities.flatMap(e => [...e.position, ...e.rotation])].some(value => !Number.isFinite(value))) return null;
  return { scene: { ...scene, entities }, camera };
}
