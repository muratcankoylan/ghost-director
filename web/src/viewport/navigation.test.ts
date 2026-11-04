import { describe, expect, it } from 'vitest';
import { Box3, Vector3 } from 'three';
import { canStandAt, findWalkSpawn } from './navigation';

const room = { width: 10, depth: 6.2, height: 3.15 };
describe('walking from production camera views', () => {
  it('clamps the south camera inside the wall margin before the first step', () => {
    const spawn = findWalkSpawn(new Vector3(1.9, 1.65, 2.9), room, []);
    expect(spawn?.toArray()).toEqual([1.9, 1.62, 2.88]);
    expect(canStandAt(spawn!, room, [])).toBe(true);
    expect(canStandAt(spawn!.clone().add(new Vector3(0, 0, .01)), room, [])).toBe(false);
  });
  it('moves an orbit view out of furniture and rejects subsequent penetration', () => {
    const table = new Box3(new Vector3(1, 0, -1), new Vector3(3, 1, 1));
    const blocked = new Vector3(2, 1.62, 0);
    expect(canStandAt(blocked, room, [table])).toBe(false);
    const spawn = findWalkSpawn(blocked, room, [table]);
    expect(spawn).not.toBeNull();
    expect(canStandAt(spawn!, room, [table])).toBe(true);
    expect(spawn!.y).toBe(1.62);
  });
  it('reports no spawn when the room is fully obstructed', () => {
    const blocked = new Box3(new Vector3(-10, 0, -10), new Vector3(10, 4, 10));
    expect(findWalkSpawn(new Vector3(), room, [blocked])).toBeNull();
  });
});
