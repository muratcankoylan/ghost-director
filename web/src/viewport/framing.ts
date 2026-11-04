import { Box3, MathUtils, PerspectiveCamera, Vector3 } from 'three';

/** Fit every bounds corner at the current orientation without changing the lens or clipping planes. */
export function fitPerspectiveBounds(bounds: Box3, camera: PerspectiveCamera, margin = 1.25) {
  if (bounds.isEmpty() || ![...bounds.min, ...bounds.max, camera.fov, camera.aspect, camera.zoom, camera.near, camera.far, margin].every(Number.isFinite)) return null;
  if (camera.aspect <= 0 || camera.zoom <= 0 || camera.near <= 0 || camera.far <= camera.near || margin < 1) return null;
  const tanVertical = Math.tan(MathUtils.degToRad(camera.getEffectiveFOV()) / 2);
  const tanHorizontal = tanVertical * camera.aspect;
  if (tanVertical <= 0 || !Number.isFinite(tanVertical)) return null;
  const center = bounds.getCenter(new Vector3());
  const right = new Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  const up = new Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
  const backward = new Vector3(0, 0, 1).applyQuaternion(camera.quaternion);
  let distance = 0;
  let farLimit = Infinity;
  for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
    const offset = new Vector3(x, y, z).sub(center);
    const depthOffset = offset.dot(backward);
    // A corner closer to the camera needs additional distance, even when its
    // projected width and height are small. The diagonal alone misses this.
    distance = Math.max(distance, depthOffset + Math.max(
      margin * Math.abs(offset.dot(right)) / tanHorizontal,
      margin * Math.abs(offset.dot(up)) / tanVertical,
      camera.near * 1.01,
    ));
    farLimit = Math.min(farLimit, depthOffset + camera.far / 1.01);
  }
  if (!Number.isFinite(distance) || distance > farLimit) return null;
  return { center, position: center.clone().addScaledVector(backward, distance), distance };
}
