import { Color, Mesh, MeshStandardMaterial, Object3D, Texture } from 'three';
import type { Entity, Material, RoomDesign } from '../api/contracts';

export type Surface = 'walls' | 'ceiling' | 'floor' | 'trim' | 'curtains';
const surfaces = new Set<Surface>(['walls', 'ceiling', 'floor', 'trim', 'curtains']);
const originals = new WeakMap<MeshStandardMaterial, { color: Color; roughness: number; metalness: number }>();

export function componentName(object: Object3D) {
  // GLTFLoader sanitizes spaces to underscores and strips punctuation in names.
  return String(object.userData.gdr_component ?? object.name).toLowerCase().replace(/[._]?\d+$/, '').replaceAll('_', ' ');
}

export function surfaceOf(object: Object3D): Surface | undefined {
  for (let node: Object3D | null = object; node; node = node.parent) {
    if (surfaces.has(node.userData.gdr_surface)) return node.userData.gdr_surface as Surface;
    // The v2 authored room predates surface tags. Only recognize its named geometry.
    const name = componentName(node);
    if (name === 'floor' || name === 'ceiling') return name;
    if (/^(south wall|east wall|entry wall|window pier|window sill wall|window header|door lintel)$/.test(name)) return 'walls';
    if (name === 'woven curtain') return 'curtains';
    if (/^(baseboard|door casing|door casing header|window mullion|window frame|window ledge|wall panel stile|wall panel rail)$/.test(name)) return 'trim';
  }
  return undefined;
}

/** Isolate editable material factors; texture images remain shared and unmodified. */
export function prepareMaterials(root: Object3D, anisotropy: number) {
  const replaced = new Set<MeshStandardMaterial>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const clone = (material: MeshStandardMaterial) => {
      if (!(material instanceof MeshStandardMaterial)) return material;
      replaced.add(material);
      const result = material.clone();
      originals.set(result, { color: result.color.clone(), roughness: result.roughness, metalness: result.metalness });
      for (const value of Object.values(result)) if (value instanceof Texture) value.anisotropy = anisotropy;
      return result;
    };
    object.material = Array.isArray(object.material) ? object.material.map(clone) : clone(object.material);
  });
  replaced.forEach((material) => material.dispose());
}

export function applyMaterials(root: Object3D, entity: Entity, design?: RoomDesign | null) {
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const surface = entity.kind === 'room' ? surfaceOf(object) : undefined;
    const selected = surface ? design?.surfaces?.[surface] : undefined;
    const committed = object.userData.gdr_surface_material as Material | undefined;
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (!(material instanceof MeshStandardMaterial)) continue;
      const native = originals.get(material);
      if (!native) continue;
      // Always restore first, including Cancel/undo and partial surface edits.
      material.color.copy(native.color); material.roughness = native.roughness; material.metalness = native.metalness;
      if (selected && (!committed || selected.color !== committed.color || selected.roughness !== committed.roughness || selected.metalness !== committed.metalness)) {
        // design_room exports untinted source textures and standard glTF factors.
        // Set the draft factor once; multiplying would apply the palette twice.
        material.color.set(selected.color); material.roughness = selected.roughness; material.metalness = selected.metalness;
      }
      if (entity.state?.material_override && entity.material) {
        material.color.set(entity.material.color); material.roughness = entity.material.roughness; material.metalness = entity.material.metalness;
      }
    }
  });
}
