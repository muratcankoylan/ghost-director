import { describe, expect, it } from 'vitest';
import { initialWorkspace, inspectorTarget, workspaceReducer, type InspectionFocus } from '../../src/state/workspace';

describe('workspace context invariants', () => {
  const take: InspectionFocus = { kind: 'take', id: 'take-a', scene_revision_id: 'source-a' };
  it('keeps the pinned take independent of the active generation shot', () => {
    let state = workspaceReducer(initialWorkspace, { type: 'select_take', id: 'take-a', focus: take });
    state = workspaceReducer(state, { type: 'pin' });
    state = workspaceReducer(state, { type: 'select_shot', id: 'shot-b', take_id: 'take-b', focus: { kind: 'shot', id: 'shot-b', scene_revision_id: 'source-b' } });
    expect(inspectorTarget(state)).toEqual(take);
    expect(state.active_shot_id).toBe('shot-b');
    expect(state.active_take_id).toBe('take-b');
    state = workspaceReducer(state, { type: 'unpin' });
    expect(inspectorTarget(state)?.id).toBe('shot-b');
  });
  it('entity selection preserves the active take and shot', () => {
    const state = workspaceReducer({ ...initialWorkspace, active_shot_id: 'shot-a', active_take_id: 'take-a' }, { type: 'inspect', focus: { kind: 'entity', id: 'cup', scene_revision_id: 'scene-a' } });
    expect(state.active_shot_id).toBe('shot-a');
    expect(state.active_take_id).toBe('take-a');
  });
  it('bounds inspection history without changing pinned context', () => {
    let state = { ...initialWorkspace, pinned_inspector_target: take };
    for (let index = 0; index < 1000; index++) state = workspaceReducer(state, { type: 'inspect', focus: { kind: 'task', id: String(index), scene_revision_id: 'scene-a' } });
    expect(state.history).toHaveLength(20);
    expect(inspectorTarget(state)).toEqual(take);
  });
});
