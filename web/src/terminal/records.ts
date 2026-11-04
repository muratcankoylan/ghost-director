import type { Snapshot, Take } from '../api/contracts';
import { modelLabel } from './modelPresentation';

export function takeSourceDiffers(take: Pick<Take, 'scene_revision_id'>, currentSceneId: string | null | undefined): boolean {
  return take.scene_revision_id !== currentSceneId;
}

// The runtime currently emits from/to/input_refs; the versioned design also
// accepts explicit sender/receiver/artifact_refs. Normalize only these known fields.
export function handoffDetails(row: Record<string, unknown>, tasks: NonNullable<Snapshot['tasks']> = []) {
  const sender = row.from_task_id ?? row.sender_task_id;
  const receiver = row.to_task_id ?? row.receiver_task_id;
  const references = row.input_refs ?? row.artifact_refs;
  const state = row.status ?? row.state;
  const summary = row.summary ?? row.reason;
  const source = tasks.find((task) => task.id === sender);
  const destination = tasks.find((task) => task.id === receiver);
  const outputRefs = source?.result?.output_refs ?? source?.output_refs;
  const inputsAttached = Array.isArray(outputRefs) && Array.isArray(destination?.input_refs) && outputRefs.some((id) => typeof id === 'string' && destination.input_refs.includes(id));
  return {
    senderId: typeof sender === 'string' ? sender : null,
    receiverId: typeof receiver === 'string' ? receiver : null,
    references: Array.isArray(references) ? references.filter((item): item is string => typeof item === 'string') : [],
    state: typeof state === 'string' ? state : inputsAttached ? 'inputs_attached' : 'consumption_unrecorded',
    summary: typeof summary === 'string' ? summary : '',
  };
}

export function applicationPromptPreview(text: string): string {
  try {
    const value: unknown = JSON.parse(text);
    if (value && typeof value === 'object' && 'messages' in value && Array.isArray(value.messages)) {
      const messages = value.messages.filter((message): message is { role: string; content: string } => message && typeof message === 'object' && typeof message.role === 'string' && typeof message.content === 'string');
      const userMessage = [...messages].reverse().find((message) => message.role === 'user');
      if (userMessage) return userMessage.content;
    }
  } catch { /* Plain text is also a valid prompt artifact. */ }
  return text;
}
export function configuredVideoModel(snapshot: Snapshot): string | undefined {
  const binding = snapshot.models?.find((model) => model.category === 'video' && model.active === true);
  return binding ? String(binding.model ?? binding.model_id ?? binding.id) : undefined;
}

export function videoModelLabel(snapshot: Snapshot, modelId?: string): string {
  if (!modelId) return 'Video';
  const binding = snapshot.models?.find((model) => (model.model ?? model.model_id ?? model.id) === modelId);
  return modelLabel(binding ?? { model: modelId, category: 'video' });
}

export function isVideoJob(job: Record<string, unknown>, snapshot: Snapshot): boolean {
  return job.category === 'video' || snapshot.models?.some((model) => model.category === 'video' && (model.model ?? model.model_id ?? model.id) === job.model) === true;
}
