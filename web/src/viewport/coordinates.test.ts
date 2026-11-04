import { describe, expect, it } from 'vitest';
import { PerspectiveCamera, Vector3 } from 'three';
import { entityMatrix, fittedFrame, toCanonicalPoint, toThreePoint, verticalFov } from './coordinates';
import type { Camera, Entity } from '../api/contracts';

describe('canonical camera and entity coordinates', () => {
  it('maps asymmetric points and a rotated/scaled local point exactly once', () => {
    expect(toCanonicalPoint(toThreePoint([1.25, -2.75, .47]))).toEqual([1.25, -2.75, .47]);
    const entity: Entity = { id: 'test', name: 'test', kind: 'prop', position: [2, 3, 1], rotation: [0, 0, Math.PI / 2], scale: [2, 1, 1], dimensions: [1, 1, 1], visible: true, asset_id: null, asset_url: null };
    const projected = toCanonicalPoint(toThreePoint([1, 0, 0]).applyMatrix4(entityMatrix(entity)));
    expect(projected[0]).toBeCloseTo(2); expect(projected[1]).toBeCloseTo(5); expect(projected[2]).toBeCloseTo(1);
  });
  it('matches an independent pinhole projection with horizontal sensor fit', () => {
    const record: Camera = { id: 'cam', name: 'cam', position: [1, -3, 1.5], target: [1, 0, 1.5], focal_length_mm: 50, sensor_width_mm: 36, aspect_ratio: 16 / 9, near: .05, far: 100, depth_of_field: false, aperture_fstop: 4, focus_distance_m: null };
    const camera = new PerspectiveCamera(verticalFov(record), record.aspect_ratio, record.near, record.far);
    camera.position.copy(toThreePoint(record.position)); camera.lookAt(toThreePoint(record.target)); camera.updateMatrixWorld(true);
    const screen = new Vector3(...toThreePoint([1.2, 0, 1.6]).toArray()).project(camera);
    const x = (screen.x + 1) * 960; const y = (1 - screen.y) * 540;
    expect(x).toBeCloseTo(960 + (1920 * 50 / 36) * .2 / 3, 6);
    expect(y).toBeCloseTo(540 - (1920 * 50 / 36) * .1 / 3, 6);
  });
  it('matches actual Blender XYZ for combined rotations, beyond Z-only staging', () => {
    // Blender5.2.3: Euler((.3,-.5,.8),'XYZ').to_matrix() @ Vector((.37,-.82,1.13)).
    const measured = [.7480944395065308, -.8334405422210693, .9121028184890747];
    const entity: Entity = { id: 'combined', name: 'combined', kind: 'prop', position: [2, -1, .5], rotation: [.3, -.5, .8], scale: [1, 1, 1], dimensions: [1, 1, 1], visible: true, asset_id: null, asset_url: null };
    const actual = toCanonicalPoint(toThreePoint([.37, -.82, 1.13]).applyMatrix4(entityMatrix(entity)));
    measured.forEach((value, axis) => expect(actual[axis]).toBeCloseTo(value + entity.position[axis], 6));
  });
  it('letterboxes without changing the camera aspect ratio', () => {
    const frame = fittedFrame(800, 600, 16 / 9);
    expect(frame).toEqual({ x: 0, y: 75, width: 800, height: 450 });
  });
});
