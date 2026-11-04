import { Box3, PerspectiveCamera, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { verticalFov } from './coordinates';
import { fitPerspectiveBounds } from './framing';

function cameraAt35mm(aspect: number) {
  const camera = new PerspectiveCamera(verticalFov({ focal_length_mm: 35, sensor_width_mm: 36, aspect_ratio: aspect }), aspect, .1, 100);
  camera.position.set(4, 1.4, 6); camera.lookAt(0, .86, 0);
  camera.updateMatrixWorld();
  return camera;
}

function corners(bounds: Box3) {
  return [bounds.min.x, bounds.max.x].flatMap(x => [bounds.min.y, bounds.max.y].flatMap(y => [bounds.min.z, bounds.max.z].map(z => new Vector3(x, y, z))));
}

describe('Perspective selection framing', () => {
  it('reproduces cropped tall-character framing with the former diagonal distance at 35 mm', () => {
    const bounds = new Box3(new Vector3(-.22, 0, -.12), new Vector3(.22, 1.72, .12));
    const camera = cameraAt35mm(885 / 565);
    const center = bounds.getCenter(new Vector3());
    const away = camera.position.clone().sub(new Vector3(0, .86, 0)).normalize();
    camera.position.copy(center).addScaledVector(away, bounds.getSize(new Vector3()).length() * 1.35);
    camera.lookAt(center); camera.updateMatrixWorld();
    expect(corners(bounds).some(corner => Math.abs(corner.project(camera).y) > 1)).toBe(true);
  });

  for (const [name, aspect, min, max] of [
    ['tall character in wide viewport', 885 / 565, [-.22, 0, -.12], [.22, 1.72, .12]],
    ['tall character in portrait viewport', 9 / 16, [-.22, 0, -.12], [.22, 1.72, .12]],
    ['wide prop in portrait viewport', 9 / 16, [-2, 0, -.3], [2, .4, .3]],
    ['deep rotated-view set', 16 / 9, [-2, -1, -4], [2, 3, 4]],
  ] as const) {
    it(`fits all corners with margin: ${name}`, () => {
      const bounds = new Box3(new Vector3(...min), new Vector3(...max));
      const camera = cameraAt35mm(aspect);
      const before = { position: camera.position.clone(), quaternion: camera.quaternion.clone(), fov: camera.fov, near: camera.near, far: camera.far, aspect: camera.aspect };
      const fit = fitPerspectiveBounds(bounds, camera)!;
      expect(fit).not.toBeNull();
      expect(camera.position).toEqual(before.position);
      camera.position.copy(fit.position); camera.lookAt(fit.center); camera.updateMatrixWorld();
      for (const corner of corners(bounds)) {
        const projected = corner.project(camera);
        expect(Math.abs(projected.x)).toBeLessThanOrEqual(1 / 1.25 + 1e-10);
        expect(Math.abs(projected.y)).toBeLessThanOrEqual(1 / 1.25 + 1e-10);
        expect(projected.z).toBeGreaterThan(-1);
        expect(projected.z).toBeLessThan(1);
      }
      expect(camera.quaternion.angleTo(before.quaternion)).toBeLessThan(1e-7);
      expect({ fov: camera.fov, near: camera.near, far: camera.far, aspect: camera.aspect }).toEqual({ fov: before.fov, near: before.near, far: before.far, aspect: before.aspect });
    });
  }

  it('reports impossible clipping constraints without changing camera settings', () => {
    const camera = cameraAt35mm(16 / 9); camera.far = .2; camera.updateProjectionMatrix();
    expect(fitPerspectiveBounds(new Box3(new Vector3(-1, -1, -1), new Vector3(1, 1, 1)), camera)).toBeNull();
    expect(camera.near).toBe(.1); expect(camera.far).toBe(.2);
    expect(fitPerspectiveBounds(new Box3(), camera)).toBeNull();
  });
});
