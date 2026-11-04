// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { TerminalApp } from '../../src/app/TerminalApp';
import type { Snapshot } from '../../src/api/contracts';
import type { TerminalViewportProps } from '../../src/terminal/types';
import { terminalFixture } from './fixtures';
import { latestStill } from '../../src/state/design';

afterEach(cleanup);
function fixture(): Snapshot {
  const snapshot = terminalFixture();
  snapshot.design_defaults = { version: 1, style: 'warm_modern', exposure: 0, windows: [{ id: 'left', center_x: -2.5, width: 1.8, sill: .65, height: 1.8 }, { id: 'right', center_x: 2.7, width: 2.6, sill: .65, height: 1.9 }], door: { wall: 'west', center_y: .28, width: .96, height: 2.5 }, details: { curtains: true, sideboard: true, chandelier: true }, surfaces: Object.fromEntries(['walls', 'ceiling', 'floor', 'trim', 'curtains'].map((key) => [key, { color: '#ffffff', roughness: .8, metalness: 0 }])) };
  snapshot.scene!.room = { width: 10, depth: 8, height: 3.2, design: null };
  snapshot.undo_target_revision_id = 'previous-scene';
  return snapshot;
}
function harness(snapshot = fixture(), onCommand = vi.fn().mockResolvedValue({ status: 'completed' })) {
  let viewport!: TerminalViewportProps;
  const renderViewport = (props: TerminalViewportProps) => { viewport = props; return <div data-testid="world">World</div>; };
  const props = { snapshot, connection: 'connected' as const, onCommand, renderViewport };
  const view = render(<TerminalApp {...props} />);
  return { ...view, onCommand, viewport: () => viewport, update: (next: Snapshot) => view.rerender(<TerminalApp {...props} snapshot={next} />) };
}

describe('production design workflow', () => {
  it('selects the newest render image by timestamp rather than artifact order or a newer JSON receipt', () => {
    const snapshot = fixture();
    const base = { kind: 'image', mime_type: 'image/png', sha256: 'f'.repeat(64), bytes: 10, name: 'Still', relative_path: 'still.png', url: '/still.png', metadata: { render_kind: 'preview', camera_id: 'camera-wide' } };
    snapshot.artifacts = [{ ...base, id: 'new-image', created_at: '2026-11-03T12:00:00Z' }, { ...base, id: 'old-image', created_at: '2026-11-03T10:00:00Z' }, { ...base, id: 'receipt', kind: 'evidence', mime_type: 'application/json', created_at: '2026-11-03T13:00:00Z' }];
    expect(latestStill(snapshot, 'camera-wide')?.id).toBe('new-image');
  });
  it('previews exact surface color, keeps geometry saved until rebuild, and discards both drafts', () => {
    const app = harness();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Room width' }), { target: { value: '12' } });
    expect(app.viewport().scene!.room!.width).toBe(10);
    expect(screen.getByText('Architecture updates after Apply & rebuild.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /MaterialsColor/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Use color #789096' }));
    expect(app.viewport().scene!.room!.design!.surfaces!.walls!.color).toBe('#789096');
    expect(app.viewport().scene!.room!.design!.surfaces!.floor!.color).toBe('#ffffff');
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }));
    expect(app.viewport().scene!.room!.design).toBeNull();
    expect(app.onCommand).not.toHaveBeenCalled();
  });

  it('routes a direct surface selection to its material controls without replacing the world', () => {
    let calls = 0;
    const snapshot = fixture();
    render(<TerminalApp snapshot={snapshot} connection="connected" onCommand={vi.fn()} renderViewport={(props) => <button onClick={() => { calls++; props.onSelectSurface?.('floor'); }}>Select floor mesh</button>} />);
    fireEvent.click(screen.getByRole('button', { name: 'Select floor mesh' }));
    expect(screen.getByRole('button', { name: 'Floor', exact: true })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Floor color')).toBeVisible();
    expect(calls).toBe(1);
  });

  it('rejects windows outside new dimensions and applies the complete room through one command', async () => {
    const app = harness();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Room width' }), { target: { value: '5' } });
    expect(screen.getByRole('button', { name: 'Apply design' })).toBeDisabled();
    expect(screen.getByText(/Every window must fit/)).toBeVisible();
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Room width' }), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply design' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledTimes(1));
    expect(app.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'apply_design', expected_revision: 'scene-current', payload: { room: expect.objectContaining({ width: 12, depth: 8, design: expect.objectContaining({ version: 1, windows: expect.any(Array) }) }) } }));
  });

  it('selects an east doorway and submits it with the unchanged opening dimensions', async () => {
    const app = harness();
    fireEvent.click(screen.getByText('Door', { exact: true }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Door wall' }), { target: { value: 'east' } });
    expect(screen.getByText('EAST WALL')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Apply design' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledTimes(1));
    expect(app.onCommand.mock.calls[0][0]).toEqual(expect.objectContaining({ operation: 'apply_design', payload: { room: expect.objectContaining({ design: expect.objectContaining({ door: { wall: 'east', center_y: .28, width: .96, height: 2.5 } }) }) } }));
  });

  it('applies lens and focus to the existing camera without submitting identity or creating a shot', async () => {
    const app = harness();
    fireEvent.click(screen.getByRole('button', { name: /CameraFraming/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Focal length' }), { target: { value: '85' } });
    expect(app.viewport().cameraOverride!.focal_length_mm).toBe(85);
    expect(screen.getByRole('button', { name: 'Final still' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply camera' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledTimes(1));
    const command = app.onCommand.mock.calls[0][0];
    expect(command.operation).toBe('update_camera');
    expect(command.payload.camera_id).toBe('camera-wide');
    expect(command.payload.camera.focal_length_mm).toBe(85);
    expect(command.payload.camera).not.toHaveProperty('id');
    expect(command.payload.camera).not.toHaveProperty('name');
  });

  it('keeps preparation locked after HTTP acceptance and selects only its own completed new shot', async () => {
    const snapshot = fixture();
    const onCommand = vi.fn().mockResolvedValue({ status: 'accepted' });
    const app = harness(snapshot, onCommand);
    fireEvent.change(screen.getByRole('textbox', { name: /NEW SHOT BRIEF/ }), { target: { value: 'A close-up of the green cup.' } });
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Create shot', exact: true }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    expect(onCommand.mock.calls[0][0].operation).toBe('direction');
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    const next = structuredClone(snapshot);
    next.shots!.push({ ...next.shots![0], id: 'fourth', name: 'The cup close-up' }, { ...next.shots![0], id: 'fifth', name: 'Extra saved shot' });
    next.commands = [{ command_id: onCommand.mock.calls[0][0].id, status: 'completed', operation: 'direction', result: { shot_id: 'fourth' } }];
    app.update(next);
    await waitFor(() => expect(screen.getByRole('button', { name: /Shot 04: The cup close-up/ })).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.getByRole('button', { name: /Shot 05: Extra saved shot/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeEnabled();
  });

  it('blocks concurrent generation during a saved command before any video job exists', () => {
    const snapshot = fixture();
    snapshot.commands = [{ command_id: 'preparing', operation: 'generate', status: 'accepted' }];
    const app = harness(snapshot);
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    expect(screen.getByText('Generate · in progress')).toBeVisible();
    expect(app.onCommand).not.toHaveBeenCalled();
  });

  it('allows room edits while a generation uses its frozen scene, but blocks another generation', async () => {
    const snapshot = fixture();
    snapshot.commands = [{ command_id: 'video-work', operation: 'generate', status: 'accepted' }];
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /MaterialsColor/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Use color #789096' }));
    expect(screen.getByRole('button', { name: 'Apply design' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply design' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'apply_design' })));
  });

  it('does not generate shot A when the operator is viewing camera B', () => {
    const snapshot = fixture();
    snapshot.scene!.cameras.push({ ...snapshot.scene!.cameras[0], id: 'camera-close', name: 'Close camera' });
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /Close camera35mm/ }));
    expect(app.viewport().activeCameraId).toBe('camera-close');
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    expect(screen.getByText('Select a matching shot or save this view as a new shot')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /Shot 01: The arrival/ }));
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeEnabled();
  });

  it('preserves an unrelated material draft when a camera edit publishes a new head', async () => {
    const snapshot = fixture();
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /MaterialsColor/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Use color #789096' }));
    fireEvent.click(screen.getByRole('button', { name: /CameraFraming/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Focal length' }), { target: { value: '85' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply camera' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledTimes(1));
    const next = structuredClone(snapshot);
    next.scene!.id = 'scene-camera-updated';
    next.scene!.parent_id = snapshot.scene!.id;
    next.scene!.cameras[0].focal_length_mm = 85;
    app.update(next);
    fireEvent.click(screen.getByRole('button', { name: /MaterialsColor/ }));
    expect(screen.getByLabelText('Walls color')).toHaveValue('#789096');
    expect(app.viewport().scene!.room!.design!.surfaces!.walls!.color).toBe('#789096');
    expect(screen.getByRole('button', { name: 'Apply design' })).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not announce historical failures on initial load', () => {
    const snapshot = fixture();
    snapshot.events = [{ id: 'old', seq: 1, type: 'command.failed', recorded_at: '2026-11-03T10:00:00Z', task_id: null, run_id: null, job_id: null, payload: { error: 'Old conflict' } }];
    harness(snapshot);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('submits local final render for the saved camera and marks earlier render evidence stale', async () => {
    const snapshot = fixture();
    snapshot.artifacts!.push({ id: 'still', kind: 'image', mime_type: 'image/png', sha256: 'f'.repeat(64), bytes: 10, name: 'Final still', relative_path: 'still.png', url: '/still.png', metadata: { render_kind: 'preview', camera_id: 'camera-wide', quality: 'final', scene_revision_id: 'earlier' } });
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: 'Render still', exact: true }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/Earlier scene · not current/)).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Final still' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'render_preview', payload: { camera_id: 'camera-wide', quality: 'final' } })));
  });

  it('saves only authored light state and leaves asset provenance untouched', async () => {
    const snapshot = fixture();
    snapshot.scene!.entities.push({ ...snapshot.scene!.entities[0], id: 'key-light', name: 'Window key', kind: 'light', state: { intensity: 200, artifact_id: 'asset-secret', type: 'area' } });
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /LightingExposure/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Light intensity' }), { target: { value: '400' } });
    expect(app.viewport().scene!.entities.find((entity) => entity.id === 'key-light')!.state!.intensity).toBe(400);
    expect(screen.getByRole('button', { name: 'Final still' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply light' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledTimes(1));
    expect(app.onCommand.mock.calls[0][0].payload.patches[0].state).toEqual({ intensity: 400, material_override: true });
  });

  it('uses the durable undo availability and revision guard', async () => {
    const app = harness();
    fireEvent.click(screen.getByRole('button', { name: 'Undo last scene edit' }));
    await waitFor(() => expect(app.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'undo', expected_revision: 'scene-current' })));
  });

  it('selects a manually saved shot from the direct ShotPlan completion result', async () => {
    const snapshot = fixture();
    const onCommand = vi.fn().mockResolvedValue({ status: 'accepted' });
    const app = harness(snapshot, onCommand);
    fireEvent.click(screen.getByRole('button', { name: 'Save view', exact: true }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Shot name' }), { target: { value: 'New close-up' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'What happens in this shot?' }), { target: { value: 'Hold the cup.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save shot' }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledTimes(1));
    expect(onCommand.mock.calls[0][0].payload.camera).not.toHaveProperty('id');
    const next = structuredClone(snapshot);
    const shot = { ...next.shots![0], id: 'manual-shot', name: 'New close-up' };
    next.shots!.push(shot);
    next.commands = [{ command_id: onCommand.mock.calls[0][0].id, operation: 'save_shot', status: 'completed', result: shot }];
    app.update(next);
    await waitFor(() => expect(screen.getByRole('button', { name: /Shot 04: New close-up/ })).toHaveAttribute('aria-pressed', 'true'));
  });

  it('adds nonoverlapping windows up to four and supports removing them', () => {
    const app = harness();
    fireEvent.click(screen.getByText('Windows'));
    fireEvent.click(screen.getByRole('button', { name: 'Add window' }));
    expect(screen.getByRole('spinbutton', { name: 'Window 3 width' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Add window' }));
    expect(screen.getByRole('button', { name: 'Add window' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply design' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove window 4' }));
    expect(screen.getByRole('button', { name: 'Add window' })).toBeEnabled();
    expect(app.onCommand).not.toHaveBeenCalled();
  });

  it('keeps invalid camera and exposure fields out of the renderer while preserving form validation', () => {
    const snapshot = fixture();
    snapshot.scene!.room!.design = structuredClone(snapshot.design_defaults!);
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /CameraFraming/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Focal length' }), { target: { value: '' } });
    expect(app.viewport().cameraOverride).toBeUndefined();
    expect(screen.getByRole('spinbutton', { name: 'Focal length' })).toHaveValue(null);
    expect(screen.getByRole('button', { name: 'Apply camera' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Reset lens' }));
    fireEvent.click(screen.getByRole('button', { name: /LightingExposure/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Exposure' }), { target: { value: '' } });
    expect(app.viewport().scene!.room!.design!.exposure).toBe(0);
    expect(screen.getByRole('button', { name: 'Apply design' })).toBeDisabled();
    expect(app.onCommand).not.toHaveBeenCalled();
  });

  it('a blank light intensity blocks final render even without a valid light preview', () => {
    const snapshot = fixture();
    snapshot.scene!.entities.push({ ...snapshot.scene!.entities[0], id: 'light', name: 'Key light', kind: 'light', state: { intensity: 200 } });
    const app = harness(snapshot);
    fireEvent.click(screen.getByRole('button', { name: /LightingExposure/ }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Light intensity' }), { target: { value: '' } });
    expect(app.viewport().scene!.entities.find((entity) => entity.id === 'light')!.state!.intensity).toBe(200);
    expect(screen.getByRole('button', { name: 'Final still' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply light' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate from rendered frames' })).toBeDisabled();
    expect(app.onCommand).not.toHaveBeenCalled();
  });
});
