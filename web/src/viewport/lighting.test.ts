import { describe, expect, it } from 'vitest';
import { PointLight, SpotLight } from 'three';
import type { Entity } from '../api/contracts';
import { PreviewLights } from './lighting';

const record = (id: string, type: string, intensity: number): Entity => ({ id, name: id, kind: 'light', position: [1, 2, 3], rotation: [0, 0, 0], scale: [1, 1, 1], dimensions: [.1, .1, .1], asset_id: null, asset_url: null, visible: true, state: { light_type: type, intensity } });

describe('authored preview lights', () => {
  it('retains independent lights of the same type and removes stale lights', () => {
    const rig = new PreviewLights(); const room = { width: 10, depth: 6 };
    rig.synchronize([record('one', 'point', 100), record('two', 'point', 40)], room);
    const points = rig.root.children.filter((light): light is PointLight => light instanceof PointLight);
    expect(points.map((light) => light.intensity)).toEqual([32, 12.8]);
    expect(points[0].position.toArray()).toEqual([1, 3, -2]);
    rig.synchronize([record('two', 'area', 40)], room);
    const lights = rig.root.children.filter((light) => light instanceof PointLight || light instanceof SpotLight);
    expect(lights).toHaveLength(1); expect(lights[0]).toBeInstanceOf(SpotLight);
    rig.dispose(); expect(rig.root.children).toHaveLength(0);
  });
});
