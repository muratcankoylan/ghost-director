import { Box3, Vector3 } from 'three';

export type RoomBounds = { width: number; depth: number; height: number };
const WALL_MARGIN = .22;

export function canStandAt(position: Vector3, room: RoomBounds, obstacles: Box3[]) {
  if (Math.abs(position.x) > room.width / 2 - WALL_MARGIN || Math.abs(position.z) > room.depth / 2 - WALL_MARGIN) return false;
  return !obstacles.some((b) => position.x > b.min.x && position.x < b.max.x && position.z > b.min.z && position.z < b.max.z);
}

export function findWalkSpawn(position: Vector3, room: RoomBounds, obstacles: Box3[]): Vector3 | null {
  if (room.width <= WALL_MARGIN * 2 || room.depth <= WALL_MARGIN * 2 || room.height < 1.2) return null;
  const clamp = (p: Vector3) => p.set(
    Math.max(-room.width / 2 + WALL_MARGIN, Math.min(room.width / 2 - WALL_MARGIN, p.x)),
    Math.min(1.62, room.height - WALL_MARGIN),
    Math.max(-room.depth / 2 + WALL_MARGIN, Math.min(room.depth / 2 - WALL_MARGIN, p.z)),
  );
  const origin = clamp(position.clone());
  if (canStandAt(origin, room, obstacles)) return origin;
  // An orbit/camera view may be inside furniture. Find the nearest tested free
  // start instead of trapping the walker in a collision volume.
  for (const radius of [.25, .5, .75, 1, 1.5, 2, 3, 4]) {
    for (let step = 0; step < 24; step++) {
      const angle = step * Math.PI / 12;
      const candidate = clamp(origin.clone().add(new Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius)));
      if (canStandAt(candidate, room, obstacles)) return candidate;
    }
  }
  return null;
}
