import { describe, expect, it } from 'vitest';
import { defaultMotion, sampleMotion } from '../../src/state/motion';
import { terminalFixture } from './fixtures';

describe('shared motion preview', () => {
  it('interpolates physical state without mutating scene truth', () => {
    const s = terminalFixture(); const scene = s.scene!; const shot = s.shots![0]; const motion = defaultMotion(scene,shot)!;
    const original = JSON.stringify(scene); const camera = scene.cameras[0];
    motion.camera_keyframes[1].position = [camera.position[0] + 2, camera.position[1], camera.position[2]];
    const sample = sampleMotion(scene,shot,motion,shot.duration / 2)!;
    expect(sample.camera.position[0]).toBe(camera.position[0] + 1);
    expect(JSON.stringify(scene)).toBe(original);
    expect(sampleMotion(scene,shot,motion,NaN)).toBeNull();
  });
  it('holds endpoints and reproduces proposed object destinations', () => {
    const s = terminalFixture(); const scene = s.scene!; const shot = s.shots![0]; const entity = scene.entities[0];
    shot.proposed_patch = [{entity_id:entity.id,position:[1,2,3],rotation:null,scale:null,material:null,state:null}];
    const motion = defaultMotion(scene,shot)!;
    expect(sampleMotion(scene,shot,motion,999)!.scene.entities[0].position).toEqual([1,2,3]);
    expect(sampleMotion(scene,shot,motion,-1)!.scene.entities[0].position).toEqual(entity.position);
  });
});
