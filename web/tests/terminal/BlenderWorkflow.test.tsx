// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Artifact } from '../../src/api/contracts';
import { TerminalApp } from '../../src/app/TerminalApp';
import { defaultMotion } from '../../src/state/motion';
import { BlenderWorkflow } from '../../src/terminal/BlenderWorkflow';
import { terminalFixture } from './fixtures';

afterEach(cleanup);

function fixture() {
  const snapshot = terminalFixture();
  const shot = snapshot.shots![0];
  shot.motion = defaultMotion(snapshot.scene!, shot);
  shot.reference_pins = [{ asset_id: 'PROP-new', revision_id: 'reference-v1', state: 'default' }];
  const makeArtifact = (id: string, kind: string, mime_type: string): Artifact => ({ id, kind, mime_type, name: id, url: `/artifacts/${id}`, relative_path: id, sha256: 'a'.repeat(64), bytes: 100 });
  snapshot.artifacts!.push(makeArtifact('motion-video', 'video', 'video/mp4'), makeArtifact('motion-blend', 'blender_scene', 'application/octet-stream'), makeArtifact('motion-receipt', 'motion_receipt', 'application/json'));
  snapshot.motion_previews = [{ id: 'motion-1', shot_id: shot.id, scene_revision_id: snapshot.scene!.id, video_id: 'motion-video', blend_id: 'motion-blend', receipt_id: 'motion-receipt', created_at: '2026-11-03T12:00:00Z' }];
  return { snapshot, shot, disabled: false, onCommand: vi.fn(), onOpen: vi.fn(), onPlan: vi.fn() };
}

describe('Blender workflow visibility', () => {
  it('exposes the exact saved sources and real artifacts without inventing an MCP connection', () => {
    const props = fixture(); render(<BlenderWorkflow {...props} />);
    expect(screen.getByText(/Local Blender subprocess/)).toHaveTextContent('MCP not connected');
    expect(screen.getByText(/Saved shot and current scene match/)).toBeVisible();
    expect(screen.getByLabelText('Blender motion reference')).toHaveAttribute('src', '/artifacts/motion-video');
    expect(screen.getByRole('link', { name: 'Download .blend' })).toHaveAttribute('href', '/artifacts/motion-blend');
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Blender receipt' }));
    expect(props.onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'motion-receipt' }));
    expect(props.onCommand).not.toHaveBeenCalled();
  });

  it('dispatches only existing local rendering and compile operations with exact saved IDs', () => {
    const props = fixture(); render(<BlenderWorkflow {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Render Blender motion' }));
    fireEvent.click(screen.getByRole('button', { name: 'Render Blender still' }));
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production request' }));
    expect(props.onCommand.mock.calls).toEqual([
      ['render_motion', { shot_id: props.shot.id }],
      ['render_preview', { camera_id: 'camera-wide', quality: 'draft' }],
      ['compile_shot', { shot_id: props.shot.id }],
    ]);
  });

  it('keeps historical outputs inspectable but blocks stale motion and submission', () => {
    const props = fixture(); props.snapshot.scene!.id = 'scene-new'; render(<BlenderWorkflow {...props} />);
    expect(screen.getByText(/Scene changed/)).toBeVisible();
    expect(screen.getByText(/Historical output/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Render Blender motion' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare production request' })).toBeDisabled();
    expect(screen.getByRole('link', { name: 'Download .blend' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Render Blender still' })).toBeEnabled();
  });

  it('requires saved motion and approved reference pins without blocking local stills', () => {
    const props = fixture(); props.shot.motion = null; props.shot.reference_pins = []; render(<BlenderWorkflow {...props} />);
    expect(screen.getByRole('button', { name: 'Render Blender motion' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare production request' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render Blender still' })).toBeEnabled();
  });

  it('shows actual accepted and failed commands, with no fabricated progress percentage', () => {
    const props = fixture(); props.snapshot.commands = [
      { command_id: 'render-failed', operation: 'render_motion', status: 'failed', error: 'Blender exited with code 1.' },
      { command_id: 'render-pending', operation: 'render_preview', status: 'accepted', expected_revision: 'scene-current' },
      { command_id: 'image-paid', operation: 'generate_image', status: 'accepted' },
    ]; render(<BlenderWorkflow {...props} />);
    expect(screen.getByRole('status')).toHaveTextContent('1 local command accepted or running');
    expect(screen.getByText('Blender exited with code 1.')).toBeVisible();
    expect(screen.queryByText('image-paid', { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Render Blender motion' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render Blender still' })).toBeDisabled();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('disables mutation in replay or while operator edits are pending while keeping evidence accessible', () => {
    const props = fixture(); render(<BlenderWorkflow {...props} disabled />);
    for (const name of ['Render Blender motion', 'Render Blender still', 'Prepare production request']) expect(screen.getByRole('button', { name })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect motion video' }));
    expect(props.onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'motion-video' }));
    expect(props.onCommand).not.toHaveBeenCalled();
  });

  it('shows an honest empty state with no decorative output when no shot has been rendered', () => {
    const props = fixture(); props.snapshot.motion_previews = []; render(<BlenderWorkflow {...props} />);
    expect(screen.getByText(/No Blender motion output is recorded/)).toBeVisible();
    expect(screen.queryByLabelText('Blender motion reference')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Download .blend' })).not.toBeInTheDocument();
  });

  it('opens from the actual application navigation and returns to shot planning with one world canvas', () => {
    const props = fixture(); const command = vi.fn();
    render(<TerminalApp snapshot={props.snapshot} connection="connected" onCommand={command} renderViewport={() => <div data-testid="world-canvas" />} />);
    fireEvent.click(screen.getByRole('button', { name: 'Blender', exact: true }));
    expect(screen.getByRole('region', { name: 'Blender workflow' })).toBeVisible();
    expect(screen.getAllByTestId('world-canvas')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Close Blender panel' }));
    expect(screen.queryByRole('region', { name: 'Blender workflow' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Blender', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Open shot planning' }));
    expect(screen.getByRole('region', { name: 'Shared shot plan' })).toBeVisible();
    expect(screen.queryByRole('region', { name: 'Blender workflow' })).not.toBeInTheDocument();
    expect(command).not.toHaveBeenCalled();
  });
});
