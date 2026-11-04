import type { Camera, Room, RoomDesign, Snapshot } from '../api/contracts';

export function validCamera(camera: Camera): boolean {
  return [...camera.position, ...camera.target, camera.focal_length_mm, camera.sensor_width_mm, camera.aperture_fstop, camera.near, camera.far].every(Number.isFinite) && camera.focal_length_mm >= 12 && camera.focal_length_mm <= 300 && camera.sensor_width_mm > 0 && camera.sensor_width_mm <= 100 && camera.aperture_fstop >= 1 && camera.aperture_fstop <= 32 && camera.near > 0 && camera.far > camera.near && camera.position.some((value, index) => value !== camera.target[index]) && (camera.focus_distance_m == null || Number.isFinite(camera.focus_distance_m) && camera.focus_distance_m > 0);
}

export function designRoom(snapshot: Snapshot): Room | null {
  const room = snapshot.scene?.room;
  const defaults = snapshot.design_defaults;
  if (!room || !(room.design ?? defaults)) return null;
  const design = room.design;
  return { ...room, design: { ...defaults, ...design, surfaces: { ...defaults?.surfaces, ...design?.surfaces }, details: design?.details ?? defaults?.details, windows: design?.windows ?? defaults?.windows, door: design?.door ?? defaults?.door } as RoomDesign };
}

export function geometryChanged(base: Room, draft: Room): boolean {
  const geometry = (room: Room) => [room.width, room.depth, room.height, room.design?.style, room.design?.windows, room.design?.door, room.design?.details];
  return JSON.stringify(geometry(base)) !== JSON.stringify(geometry(draft));
}

export function nextWindowPlacement(room: Room): { center_x: number; width: number; sill: number; height: number } | null {
  const windows = [...(room.design?.windows ?? [])].sort((a, b) => a.center_x - b.center_x);
  if (windows.length >= 4 || !Number.isFinite(room.width) || room.height < 1) return null;
  let left = -room.width / 2 + .1;
  const gaps: Array<[number, number]> = [];
  for (const opening of windows) { gaps.push([left, opening.center_x - opening.width / 2 - .1]); left = opening.center_x + opening.width / 2 + .1; }
  gaps.push([left, room.width / 2 - .1]);
  const gap = gaps.sort((a, b) => (b[1] - b[0]) - (a[1] - a[0]))[0];
  if (!gap || gap[1] - gap[0] < .6) return null;
  return { center_x: (gap[0] + gap[1]) / 2, width: Math.min(1.2, gap[1] - gap[0]), sill: .4, height: Math.min(1.8, room.height - .6) };
}

export function latestStill(snapshot: Snapshot, cameraId?: string) {
  return (snapshot.artifacts ?? []).filter((item) => item.kind === 'image' && item.mime_type.startsWith('image/') && item.metadata?.render_kind === 'preview' && (!cameraId || item.metadata?.camera_id === cameraId)).sort((a, b) => String(a.created_at ?? '').localeCompare(String(b.created_at ?? ''))).at(-1);
}

// Validate the visible edit before dispatch; the domain remains authoritative.
export function roomError(room: Room): string | null {
  if (![room.width, room.depth, room.height].every(Number.isFinite) || room.width <= 0 || room.depth <= 0 || room.height <= 0 || room.width > 100 || room.depth > 100 || room.height > 30) return 'Enter positive room dimensions (width/depth ≤100 m; height ≤30 m).';
  const design = room.design;
  if (!design) return 'A complete design is required.';
  if (!Number.isFinite(design.exposure) || Math.abs(design.exposure) > 4) return 'Exposure must be between −4 and +4 stops.';
  const windows = [...(design.windows ?? [])].sort((a, b) => a.center_x - b.center_x);
  for (const [index, window] of windows.entries()) {
    if (![window.center_x, window.width, window.sill, window.height].every(Number.isFinite) || window.width <= .2 || window.width > 30 || window.height <= .2 || window.height > 20 || window.sill < .1) return 'Windows need positive dimensions and a sill of at least 0.1 m.';
    if (Math.abs(window.center_x) + window.width / 2 > room.width / 2 - .1 || window.sill + window.height > room.height - .1) return 'Every window must fit inside the room with a 0.1 m wall margin.';
    if (index && windows[index - 1].center_x + windows[index - 1].width / 2 + .1 > window.center_x - window.width / 2) return 'Leave at least 0.1 m of wall between windows.';
  }
  const door = design.door;
  if (door && (![door.center_y, door.width, door.height].every(Number.isFinite) || door.width < .6 || door.width > 3 || door.height < 1.8 || door.height > 5 || Math.abs(door.center_y) + door.width / 2 > room.depth / 2 - .1 || door.height > room.height - .1)) return `The door must fit the ${door.wall ?? 'west'} wall (width 0.6–3 m, height 1.8–5 m).`;
  if (design.details?.sideboard && (room.width < 2.2 || room.depth < 1.2)) return 'The sideboard needs a room at least 2.2 × 1.2 m.';
  for (const material of Object.values(design.surfaces ?? {})) {
    if (!/^#[0-9a-f]{6}$/i.test(material.color) || ![material.roughness, material.metalness].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) return 'Use a six-digit hex color and finishes between 0 and 1.';
  }
  return null;
}

export const DESIGN_PRESETS = [
  { id: 'warm_modern', name: 'Warm modern', note: 'Layered trim · warm neutrals', colors: ['#f1e6d5', '#ffffff', '#ffffff', '#c8b99f', '#d4cbb5'] },
  { id: 'minimal', name: 'Quiet minimal', note: 'Simple lines · cool neutrals', colors: ['#e2e5df', '#ffffff', '#e9e9e9', '#d2d5ce', '#dce0d9'] },
  { id: 'noir', name: 'Evening noir', note: 'Dark paneling · brass accents', colors: ['#5b625e', '#8b9089', '#c3b7a7', '#ad9874', '#696b60'] },
] as const;
