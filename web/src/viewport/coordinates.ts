import { Euler, Matrix4, Quaternion, Vector3 } from 'three';
import type { Camera, Entity } from '../api/contracts';

export type Vec3 = [number, number, number];
// C maps the canonical right-handed Z-up frame into glTF/Three Y-up.
const C = new Matrix4().makeRotationX(-Math.PI / 2);
const inverseC = C.clone().invert();
export function toThreePoint([x, y, z]: Vec3): Vector3 {
  return new Vector3(x, z, -y);
}
export function toCanonicalPoint(v: Vector3): Vec3 {
  return [v.x, -v.z, v.y];
}
export function entityMatrix(entity: Entity): Matrix4 {
  const canonical = new Matrix4().compose(
    new Vector3(...entity.position),
    // Blender's XYZ applies fixed-axis X, then Y, then Z: Rz * Ry * Rx.
    // Three names the equivalent intrinsic convention ZYX. Equal names here
    // disagree for combined rotations; the Blender numeric fixture guards this.
    new Quaternion().setFromEuler(new Euler(...entity.rotation, 'ZYX')),
    new Vector3(...entity.scale),
  );
  // GLB geometry is already Y-up. Conjugate the canonical entity transform once.
  return C.clone().multiply(canonical).multiply(inverseC);
}
export function verticalFov(camera: Pick<Camera, 'sensor_width_mm' | 'focal_length_mm' | 'aspect_ratio'>): number {
  return 2 * Math.atan(camera.sensor_width_mm / (2 * camera.focal_length_mm * camera.aspect_ratio)) * 180 / Math.PI;
}
export function fittedFrame(width: number, height: number, aspect: number) {
  const frameWidth = Math.min(width, height * aspect);
  const frameHeight = frameWidth / aspect;
  return { x: (width - frameWidth) / 2, y: (height - frameHeight) / 2, width: frameWidth, height: frameHeight };
}
