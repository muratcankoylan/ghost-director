/** UI names only. Recorded model IDs remain authoritative for routing and evidence. */
const META_MODELS: Record<string, string> = {
  'muse-wonder-1.0': 'Meta Wonder 1.0',
  'muse-image-1.0': 'Meta Image 1.0',
  'sam-3.1': 'Meta SAM 3.1',
  'fal-ai/sam-3/3d-objects': 'Meta SAM 3D',
  'fal-ai/sam-3/3d-body': 'Meta SAM 3D Body',
};

function modelId(record?: Record<string, unknown>): string {
  return String(record?.model ?? record?.requested_model ?? record?.model_id ?? record?.name ?? record?.id ?? '').toLowerCase();
}

export function modelLabel(record?: Record<string, unknown>): string {
  const id = modelId(record);
  if (META_MODELS[id]) return META_MODELS[id];
  if (record?.provider === 'meta') return String(record.label ?? record.model ?? 'Meta model');
  if (id === 'bytedance/seedance-2.5/reference-to-video') return 'Video · motion reference';
  if (id.includes('first-last-frame')) return 'Video · endpoints';
  if (id.includes('reference-to-video')) return 'Video · references';
  if (id.includes('image-to-video')) return 'Video · first frame';
  if (record?.category === 'video' || /veo|seedance/.test(id)) return 'Video generation';
  if (/meshy|image-to-3d/.test(id) || /geometry|assets_3d/.test(String(record?.category ?? ''))) return '3D reconstruction';
  if (record?.provider === 'blender') return 'Local render';
  return 'Generation service';
}

export function providerLabel(record?: Record<string, unknown>): string {
  if (record?.provider === 'meta') return 'Meta';
  if (record?.provider === 'blender') return 'Local renderer';
  return record?.provider ? 'Hosted service' : 'Not recorded';
}

/** Read-only presentation aliases; never feed this text back into a command. */
export function displayText(value: unknown): string {
  return String(value ?? '')
    .replace(/\bfal-ai\/sam-3\/3d-objects\b/gi, 'Meta SAM 3D')
    .replace(/\bfal-ai\/sam-3\/3d-body\b/gi, 'Meta SAM 3D Body')
    .replace(/\b(?:fal-ai\/|google[\/ ]+)?veo(?:[ -]?\d+(?:\.\d+)*)?(?:\/[a-z0-9-]+)?\b/gi, 'Video generation')
    .replace(/\b(?:bytedance[\/ ]+)?seedance(?:[ -]?\d+(?:\.\d+)*)?\b/gi, 'Video generation')
    .replace(/\bmeshy(?:\/v\d+(?:\.\d+)*(?:\/[a-z0-9-]+)?|[ -]+\d+(?:\.\d+)*)?\b/gi, '3D reconstruction')
    .replace(/\bopenrouter\b/gi, 'Hosted service')
    .replace(/\bfal(?:-ai|\.ai| ai)?\b/gi, 'Hosted service');
}
