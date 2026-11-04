import { describe, expect, it } from 'vitest';
import { BoxGeometry, Color, Group, Mesh, MeshStandardMaterial, Texture } from 'three';
import type { Entity, RoomDesign } from '../api/contracts';
import { applyMaterials, prepareMaterials, surfaceOf } from './materials';

const entity: Entity = { id: 'room', name: 'room', kind: 'room', position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1], dimensions: [10, 6, 3], asset_id: null, asset_url: null, visible: true };
const palette = (surfaces: RoomDesign['surfaces']): RoomDesign => ({ version: 1, style: 'warm_modern', exposure: 0, surfaces });

describe('non-destructive native material previews', () => {
  it('restores exact color and all PBR factors/maps after canceling an entity override', () => {
    const maps = { map: new Texture(), normalMap: new Texture(), roughnessMap: new Texture(), metalnessMap: new Texture() };
    const source = new MeshStandardMaterial({ color: '#875d32', roughness: .72, metalness: .04, ...maps });
    const mesh = new Mesh(new BoxGeometry(), source); const root = new Group(); root.add(mesh);
    prepareMaterials(root, 8); const material = mesh.material;
    applyMaterials(root, { ...entity, state: { material_override: true }, material: { color: '#ff0000', roughness: .05, metalness: .8 } });
    expect(material.color.getHexString()).toBe('ff0000');
    applyMaterials(root, entity);
    expect(material.color.toArray()).toEqual(source.color.toArray());
    expect(material.roughness).toBe(.72); expect(material.metalness).toBe(.04);
    for (const key of Object.keys(maps) as (keyof typeof maps)[]) expect(material[key]).toBe(maps[key]);
  });

  it('isolates shared surfaces, avoids double tint, and restores a committed black surface', () => {
    const source = new MeshStandardMaterial({ color: '#000000', roughness: .4, metalness: 0, map: new Texture() });
    const floor = new Mesh(new BoxGeometry(), source); const walls = new Mesh(new BoxGeometry(), source);
    const committed = { color: '#000000', roughness: .4, metalness: 0 };
    floor.userData = { gdr_surface: 'floor', gdr_surface_material: committed };
    walls.userData = { gdr_surface: 'walls', gdr_surface_material: committed };
    const root = new Group(); root.add(floor, walls); prepareMaterials(root, 4);
    const design = palette({ floor: { color: '#e0c0a0', roughness: .7, metalness: .1 }, walls: committed });
    applyMaterials(root, entity, design);
    expect(floor.material.color.toArray()).toEqual(new Color('#e0c0a0').toArray());
    expect(walls.material.color.getHexString()).toBe('000000');
    expect(floor.material).not.toBe(walls.material);
    applyMaterials(root, entity, palette({ floor: committed, walls: committed }));
    expect(floor.material.color.toArray()).toEqual(source.color.toArray());
    expect(floor.material.roughness).toBe(.4); expect(floor.material.metalness).toBe(0);
  });

  it('applies a draft relative to compiled factors without accumulating changes', () => {
    const committed = { color: '#bca987', roughness: .6, metalness: .2 };
    const source = new MeshStandardMaterial({ color: committed.color, roughness: .6, metalness: .2 });
    const mesh = new Mesh(new BoxGeometry(), source); mesh.userData = { gdr_surface: 'floor', gdr_surface_material: committed };
    prepareMaterials(mesh, 4);
    const selected = { color: '#607850', roughness: .8, metalness: .5 };
    for (let i = 0; i < 3; i++) applyMaterials(mesh, entity, palette({ floor: selected }));
    mesh.material.color.toArray().forEach((v, i) => expect(v).toBeCloseTo(new Color(selected.color).toArray()[i], 12));
    expect(mesh.material.roughness).toBeCloseTo(.8); expect(mesh.material.metalness).toBeCloseTo(.5);
    applyMaterials(mesh, entity, palette({ floor: committed }));
    expect(mesh.material.color.toArray()).toEqual(source.color.toArray());
  });

  it('selects explicit surface tags and conservative legacy names without tagging actors', () => {
    const mesh = new Mesh(); mesh.name = 'window mullion.004'; expect(surfaceOf(mesh)).toBe('trim');
    mesh.name = 'window_mullion004'; expect(surfaceOf(mesh)).toBe('trim');
    mesh.name = 'Eren chair'; expect(surfaceOf(mesh)).toBeUndefined();
    mesh.userData.gdr_surface = 'floor'; expect(surfaceOf(mesh)).toBe('floor');
  });
});
