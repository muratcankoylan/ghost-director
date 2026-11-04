export type InspectionKind = 'entity' | 'camera' | 'shot' | 'task' | 'handoff' | 'model_call' | 'models' | 'take' | 'observation';
export interface InspectionFocus {
  kind: InspectionKind;
  id: string;
  scene_revision_id: string;
  run_id?: string;
  task_id?: string;
  artifact_id?: string;
  time_ms?: number;
}

export interface WorkspaceContext {
  active_shot_id: string | null;
  active_take_id: string | null;
  inspection_focus: InspectionFocus | null;
  pinned_inspector_target: InspectionFocus | null;
  viewed_scene_revision_id: string | null;
  history: InspectionFocus[];
  viewport_mode: 'orbit' | 'walk' | 'camera';
  replay_run_id: string | null;
}

export const initialWorkspace: WorkspaceContext = {
  active_shot_id: null, active_take_id: null, inspection_focus: null,
  pinned_inspector_target: null, viewed_scene_revision_id: null,
  history: [], viewport_mode: 'orbit', replay_run_id: null,
};

export type WorkspaceAction =
  | { type: 'inspect'; focus: InspectionFocus }
  | { type: 'select_shot'; id: string; take_id?: string | null; focus: InspectionFocus }
  | { type: 'select_take'; id: string; focus: InspectionFocus }
  | { type: 'pin' }
  | { type: 'unpin' }
  | { type: 'back' }
  | { type: 'mode'; mode: WorkspaceContext['viewport_mode'] }
  | { type: 'view_revision'; id: string }
  | { type: 'replay'; run_id: string | null }
  | { type: 'bootstrap'; shot_id?: string; take_id?: string; scene_revision_id: string };

function inspect(state: WorkspaceContext, focus: InspectionFocus): WorkspaceContext {
  const same = state.inspection_focus?.kind === focus.kind && state.inspection_focus?.id === focus.id;
  return { ...state, inspection_focus: focus, history: !same && state.inspection_focus ? [...state.history.slice(-19), state.inspection_focus] : state.history };
}

export function workspaceReducer(state: WorkspaceContext, action: WorkspaceAction): WorkspaceContext {
  switch (action.type) {
    case 'inspect': return inspect(state, action.focus);
    case 'select_shot': return inspect({ ...state, active_shot_id: action.id, active_take_id: action.take_id ?? null }, action.focus);
    case 'select_take': return inspect({ ...state, active_take_id: action.id }, action.focus);
    case 'pin': return { ...state, pinned_inspector_target: state.inspection_focus };
    case 'unpin': return { ...state, pinned_inspector_target: null };
    case 'back': return state.history.length ? { ...state, inspection_focus: state.history.at(-1)!, history: state.history.slice(0, -1) } : state;
    case 'mode': return { ...state, viewport_mode: action.mode };
    case 'view_revision': return { ...state, viewed_scene_revision_id: action.id };
    case 'replay': return { ...state, replay_run_id: action.run_id };
    case 'bootstrap': return { ...state, active_shot_id: state.active_shot_id ?? action.shot_id ?? null, active_take_id: state.active_take_id ?? action.take_id ?? null, viewed_scene_revision_id: state.viewed_scene_revision_id ?? action.scene_revision_id };
  }
}

export function inspectorTarget(state: WorkspaceContext): InspectionFocus | null {
  return state.pinned_inspector_target ?? state.inspection_focus;
}

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"], video, audio, [role="dialog"]'));
}
