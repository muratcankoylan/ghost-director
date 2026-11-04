import { DirectionalLight, Group, PointLight, SpotLight, Vector3 } from 'three';
import type { Entity } from '../api/contracts';
import { toThreePoint } from './coordinates';

type PreviewLight = PointLight | DirectionalLight | SpotLight;
type Entry = { type: string; light: PreviewLight };

/** Authored strengths are calibrated preview units, not Cycles watts.
 * Area sources use a shadowed spotlight approximation. RectAreaLight has no
 * shadows, so using it alone would illuminate through the room's walls.
 */
export class PreviewLights {
  readonly root = new Group();
  private entries = new Map<string, Entry>();

  synchronize(entities: Entity[], room: { width: number; depth: number }) {
    const wanted = new Set(entities.filter((entity) => entity.kind === 'light').map((entity) => entity.id));
    for (const [id, entry] of this.entries) if (!wanted.has(id)) { this.remove(entry); this.entries.delete(id); }
    for (const entity of entities) {
      if (entity.kind !== 'light') continue;
      const state = entity.state ?? {};
      const type = String(state.light_type ?? 'point');
      if (!['point', 'directional', 'area'].includes(type)) continue;
      let entry = this.entries.get(entity.id);
      if (!entry || entry.type !== type) {
        if (entry) this.remove(entry);
        const light = type === 'point' ? new PointLight() : type === 'directional' ? new DirectionalLight() : new SpotLight();
        light.castShadow = true; light.shadow.mapSize.set(type === 'point' ? 1024 : 2048, type === 'point' ? 1024 : 2048);
        light.shadow.bias = -.0001; light.shadow.normalBias = .003;
        light.shadow.camera.near = .05; light.shadow.camera.far = 60;
        this.root.add(light);
        if (!(light instanceof PointLight)) this.root.add(light.target);
        entry = { type, light }; this.entries.set(entity.id, entry);
      }
      const { light } = entry;
      light.name = entity.id; light.visible = entity.visible;
      light.position.copy(toThreePoint(entity.position)); light.color.set(entity.material?.color ?? '#ffffff');
      const intensity = Number(state.intensity ?? (type === 'point' ? 35 : 1.4));
      light.intensity = intensity * (type === 'point' ? .32 : type === 'directional' ? .55 : .32);
      const size = Number(state.soft_size_m ?? (type === 'point' ? 1.3 : 2));
      light.shadow.radius = Math.max(1, Math.min(6, size * 2));
      if (light instanceof PointLight || light instanceof SpotLight) { light.distance = 60; light.decay = 2; }
      if (!(light instanceof PointLight)) {
        const raw = state.target;
        const target = Array.isArray(raw) && raw.length === 3 && raw.every(Number.isFinite)
          ? toThreePoint(raw as [number, number, number])
          : type === 'directional' ? toThreePoint([1.2, 0, .5]) : light.position.clone().add(new Vector3(0, -1, 0));
        light.target.position.copy(target);
      }
      if (light instanceof DirectionalLight) {
        const extent = Math.max(room.width, room.depth) * .65;
        light.shadow.camera.left = -extent; light.shadow.camera.right = extent;
        light.shadow.camera.top = extent; light.shadow.camera.bottom = -extent; light.shadow.camera.updateProjectionMatrix();
      }
      if (light instanceof SpotLight) { light.angle = Math.PI / 2.4; light.penumbra = .6; }
    }
  }

  private remove(entry: Entry) {
    this.root.remove(entry.light);
    if (!(entry.light instanceof PointLight)) this.root.remove(entry.light.target);
    entry.light.dispose();
  }
  dispose() { this.entries.forEach((entry) => this.remove(entry)); this.entries.clear(); }
}
