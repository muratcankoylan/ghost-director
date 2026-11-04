// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Artifact, ShotPlan, Snapshot } from '../../src/api/contracts';
import { defaultMotion } from '../../src/state/motion';
import { ShotPlanner } from '../../src/terminal/ShotPlanner';
import { terminalFixture } from './fixtures';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function fixture(): { snapshot: Snapshot; shot: ShotPlan } {
  const snapshot = terminalFixture();
  const shot: ShotPlan = { ...snapshot.shots![0], duration: 8, action: 'The cup slides forward.', reference_pins: [{ asset_id: 'PROP-cup', revision_id: 'cup-approved-1', state: 'default' }] };
  shot.motion = defaultMotion(snapshot.scene!, shot)!;
  shot.motion.camera_keyframes[1].position = [6, -4, 2];
  shot.motion.tracks = [{ entity_id: 'cup', keyframes: [
    { time_seconds: 0, position: [0, 0, 1], rotation: [0, 0, 0] },
    { time_seconds: 8, position: [2, 0, 1], rotation: [0, 0, 1] },
  ] }];
  snapshot.shots = [shot];
  snapshot.reference_assets = [
    { id: 'PROP-cup', category: 'prop', name: 'Hero cup', revision_id: 'cup-draft-3', approved_revision_id: 'cup-approved-2', approval_status: 'draft' },
    { id: 'CHAR-actor', category: 'character', name: 'Mira', revision_id: 'actor-approved-1', approved_revision_id: 'actor-approved-1', approval_status: 'approved' },
    { id: 'PROP-draft', category: 'prop', name: 'Unapproved plate', revision_id: 'plate-draft-1', approved_revision_id: null, approval_status: 'draft' },
  ];
  const receipt: Artifact = { id: 'motion-receipt', kind: 'motion_receipt', name: 'Blender timeline receipt', url: '/receipt.json', sha256: 'c'.repeat(64), bytes: 100, relative_path: 'receipt.json', mime_type: 'application/json' };
  const video: Artifact = { id: 'motion-video', kind: 'video', name: 'Motion preview', url: '/motion.mp4', sha256: 'd'.repeat(64), bytes: 100, relative_path: 'motion.mp4', mime_type: 'video/mp4' };
  const prompt: Artifact = { id: 'compiled-prompt', kind: 'prompt', name: 'Exact prompt', url: '/prompt.txt', sha256: 'e'.repeat(64), bytes: 100, relative_path: 'prompt.txt', mime_type: 'text/plain' };
  const reference: Artifact = { id: 'image-1', kind: 'reference', name: 'Approved cup', url: '/cup.png', sha256: 'f'.repeat(64), bytes: 100, relative_path: 'cup.png', mime_type: 'image/png' };
  snapshot.artifacts = [...snapshot.artifacts!, receipt, video, prompt, reference];
  snapshot.motion_previews = [{ id: 'preview-1', shot_id: shot.id, video_id: video.id, receipt_id: receipt.id }];
  snapshot.packages = [{ id: 'submission-1', shot_id: shot.id, workflow: 'reference-motion-v1', run_id: 'submission-run', reference_ids: ['image-1'], reference_pins: shot.reference_pins, reference_mapping: [{ artifact_id: 'image-1', asset_id: 'PROP-cup', revision_id: 'cup-approved-1', name: 'Hero cup', slot: 'primary' }], prompt_ref: prompt.id, prompt: 'Use @Image1 for the cup and @Video1 for motion.', motion_video_id: video.id, motion_receipt_id: receipt.id, duration: 8, review_sha256: 'a'.repeat(64), pricing_estimate: { estimated_usd: .42, reserve_usd: .51 } }];
  return { snapshot, shot };
}

function mount(options: Partial<{ snapshot: Snapshot; shot: ShotPlan; readOnly: boolean }> = {}) {
  const data = { ...fixture(), ...options };
  const props = { ...data, readOnly: data.readOnly ?? false, onCommand: vi.fn(), onOpen: vi.fn(), onPreview: vi.fn() };
  const view = render(<ShotPlanner {...props} />);
  return { ...view, ...props, update: (updates: Partial<typeof props>) => view.rerender(<ShotPlanner {...props} {...updates} />) };
}

function approveRequest() {
  fireEvent.change(screen.getByLabelText('Generation approval note'), { target: { value: 'Approved framing and cup identity.' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'I approve this exact request and authorize its API cost.' }));
}

describe('Shared shot planner', () => {
  it('saves exact edited motion and approved pins without silently upgrading an existing identity', () => {
    const { onCommand, shot } = mount();
    expect(screen.queryByRole('checkbox', { name: /Unapproved plate/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Performance direction'), { target: { value: 'Mira gently slides the cup.' } });
    fireEvent.change(screen.getByLabelText('Camera 2 position X'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('cup end Y'), { target: { value: '1.25' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Mira/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    expect(onCommand).toHaveBeenCalledTimes(1);
    const [operation, payload] = onCommand.mock.calls[0];
    expect(operation).toBe('update_shot_plan');
    expect(payload).toEqual(expect.objectContaining({ shot_id: shot.id, duration: 8, action: 'Mira gently slides the cup.', reference_pins: [
      { asset_id: 'PROP-cup', revision_id: 'cup-approved-1', state: 'default' },
      { asset_id: 'CHAR-actor', revision_id: 'actor-approved-1', state: 'default' },
    ] }));
    expect(payload.motion.camera_keyframes[1].position).toEqual([7, -4, 2]);
    expect(payload.motion.tracks[0].keyframes[1].position).toEqual([2, 1.25, 1]);
    expect(shot.motion!.camera_keyframes[1].position).toEqual([6, -4, 2]);
    expect(shot.motion!.tracks![0].keyframes[1].position).toEqual([2, 0, 1]);
  });

  it('scrubs the shared camera and object motion without issuing commands or changing scene truth', () => {
    const data = fixture();
    const original = JSON.stringify(data);
    const { onCommand, onPreview } = mount(data);
    onPreview.mockClear();
    fireEvent.change(screen.getByRole('slider', { name: 'Motion playhead' }), { target: { value: '4' } });
    const preview = onPreview.mock.calls.at(-1)![0];
    expect(preview.camera.position).toEqual([5, -4, 2]);
    expect(preview.scene.entities[0].position).toEqual([1, 0, 1]);
    expect(preview.scene.entities[0].rotation).toEqual([0, 0, .5]);
    expect(onCommand).not.toHaveBeenCalled();
    expect(JSON.stringify(data)).toBe(original);
    fireEvent.click(screen.getByRole('button', { name: 'Exit preview' }));
    expect(onPreview).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole('button', { name: 'Prepare submission' })).toBeEnabled();
  });

  it('keeps saved starts unchanged until the operator explicitly rebases a stale shot', () => {
    const data = fixture();
    data.snapshot.scene!.id = 'scene-new';
    data.snapshot.scene!.cameras[0].position = [1, -3, 2.5];
    data.snapshot.scene!.entities[0].position = [.5, .25, 1.1];
    const { onCommand } = mount(data);
    expect(screen.getByText(/The set changed/)).toBeVisible();
    expect(screen.getByLabelText('Camera 1 position X')).toHaveValue(4);
    expect(screen.getByLabelText('Camera 1 position X')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Render motion' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare submission' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Use current starting positions' }));
    expect(screen.getByLabelText('Camera 1 position X')).toHaveValue(1);
    expect(onCommand).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    const payload = onCommand.mock.calls[0][1];
    expect(payload.motion.camera_keyframes[0].position).toEqual([1, -3, 2.5]);
    expect(payload.motion.camera_keyframes[1].position).toEqual([6, -4, 2]);
    expect(payload.motion.tracks[0].keyframes[0].position).toEqual([.5, .25, 1.1]);
    expect(payload.motion.tracks[0].keyframes[1].position).toEqual([2, 0, 1]);
    expect(data.shot.motion!.camera_keyframes[0].position).toEqual([4, -4, 2]);
  });

  it('blocks rendering, compilation and generation while edits have not been saved', () => {
    const { onCommand } = mount();
    const renderButton = screen.getByRole('button', { name: 'Render motion' });
    const compileButton = screen.getByRole('button', { name: 'Prepare submission' });
    const generateButton = screen.getByRole('button', { name: 'Generate movie take' });
    expect(renderButton).toBeEnabled();
    expect(compileButton).toBeEnabled();
    expect(generateButton).toBeDisabled();
    approveRequest();
    expect(generateButton).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Performance direction'), { target: { value: 'A changed action' } });
    expect(renderButton).toBeDisabled();
    expect(compileButton).toBeDisabled();
    expect(generateButton).toBeDisabled();
    for (const button of [renderButton, compileButton, generateButton]) fireEvent.click(button);
    expect(onCommand).not.toHaveBeenCalled();
  });

  it('requires a persisted shared motion plan before rendering or compiling default blocking', () => {
    const data = fixture(); data.shot.motion = null;
    mount(data);
    expect(screen.getByLabelText('Camera 1 position X')).toHaveValue(4);
    expect(screen.getByRole('button', { name: 'Save shared plan' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Render motion' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Prepare submission' })).toBeDisabled();
  });

  it('keeps replay read-only while allowing local motion inspection and original evidence', () => {
    const { snapshot, onCommand, onPreview, onOpen } = mount({ readOnly: true });
    for (const name of ['Save shared plan', 'Render motion', 'Prepare submission', 'Generate movie take', 'Add camera beat', 'Remove movement']) {
      const button = screen.getByRole('button', { name });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(screen.getByLabelText('Performance direction')).toBeDisabled();
    expect(screen.getByLabelText('Shot duration seconds')).toBeDisabled();
    expect(screen.getByLabelText('Camera 2 position X')).toBeDisabled();
    expect(screen.getByLabelText('cup end X')).toBeDisabled();
    expect(screen.getByLabelText('Add moving object')).toBeDisabled();
    for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).toBeDisabled();
    fireEvent.change(screen.getByRole('slider', { name: 'Motion playhead' }), { target: { value: '4' } });
    expect(onPreview.mock.calls.at(-1)![0].camera.position).toEqual([5, -4, 2]);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect timeline receipt' }));
    expect(onOpen).toHaveBeenLastCalledWith(snapshot.artifacts!.find(a => a.id === 'motion-receipt'));
    fireEvent.click(screen.getByRole('button', { name: 'Review exact prompt' }));
    expect(onOpen).toHaveBeenLastCalledWith(snapshot.artifacts!.find(a => a.id === 'compiled-prompt'));
    expect(onCommand).not.toHaveBeenCalled();
  });

  it('retimes camera and object keyframes together when duration changes', () => {
    const { onCommand } = mount();
    fireEvent.change(screen.getByLabelText('Shot duration seconds'), { target: { value: '15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    const payload = onCommand.mock.calls[0][1];
    expect(payload.duration).toBe(15);
    expect(payload.motion.camera_keyframes.map((key: { time_seconds: number }) => key.time_seconds)).toEqual([0, 15]);
    expect(payload.motion.tracks[0].keyframes.map((key: { time_seconds: number }) => key.time_seconds)).toEqual([0, 15]);
  });

  it('does not accept an empty or out-of-bounds duration', () => {
    const { onCommand } = mount();
    for (const value of ['', '0', '31', 'Infinity']) {
      fireEvent.change(screen.getByLabelText('Shot duration seconds'), { target: { value } });
      expect(screen.getByLabelText('Shot duration seconds')).toHaveValue(8);
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    expect(onCommand.mock.calls[0][1].duration).toBe(8);
  });

  it('rejects fractional duration before creating a plan the server cannot accept', () => {
    const { onCommand } = mount();
    fireEvent.change(screen.getByLabelText('Shot duration seconds'), { target: { value: '5.5' } });
    expect(screen.getByLabelText('Shot duration seconds')).toHaveValue(8);
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    expect(onCommand.mock.calls[0][1].duration).toBe(8);
  });

  it('does not silently replace a cleared camera or object coordinate with zero', () => {
    const { onCommand } = mount();
    fireEvent.change(screen.getByLabelText('Camera 2 position X'), { target: { value: '' } });
    expect(screen.getByLabelText('Camera 2 position X')).toHaveValue(6);
    fireEvent.change(screen.getByLabelText('cup end X'), { target: { value: '' } });
    expect(screen.getByLabelText('cup end X')).toHaveValue(2);
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    expect(onCommand.mock.calls[0][1].motion.camera_keyframes[1].position).toEqual([6, -4, 2]);
    expect(onCommand.mock.calls[0][1].motion.tracks[0].keyframes[1].position).toEqual([2, 0, 1]);
  });

  it('uses only the selected shot evidence and never generates an existing package twice', () => {
    const data = fixture();
    data.snapshot.motion_previews!.push({ id: 'unrelated-preview', shot_id: 'other-shot', video_id: 'video-b', receipt_id: 'other-receipt' });
    data.snapshot.packages!.push({ id: 'other-package', shot_id: 'other-shot', workflow: 'reference-motion-v1', reference_ids: [], duration: 30 });
    const { onCommand, update } = mount(data);
    expect(screen.getByLabelText('Technical motion preview')).toHaveAttribute('src', '/motion.mp4');
    approveRequest();
    fireEvent.click(screen.getByRole('button', { name: 'Generate movie take' }));
    expect(onCommand).toHaveBeenCalledWith('generate_package', { package_id: 'submission-1', approval: { accepted: true, review_sha256: 'a'.repeat(64), actor: 'local-operator', note: 'Approved framing and cup identity.' } });
    update({ snapshot: { ...data.snapshot, takes: [{ ...data.snapshot.takes![0], package_id: 'submission-1' }] } });
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeDisabled();
  });

  it('clears preview on shot change and unmount without changing accepted scene data', () => {
    const { shot, onPreview, onCommand, update, unmount } = mount();
    fireEvent.change(screen.getByRole('slider', { name: 'Motion playhead' }), { target: { value: '4' } });
    onPreview.mockClear();
    update({ shot: { ...shot, id: 'new-shot', action: 'A different performance' } });
    expect(onPreview).toHaveBeenLastCalledWith(null);
    expect(screen.getByLabelText('Performance direction')).toHaveValue('A different performance');
    expect(screen.getByRole('slider', { name: 'Motion playhead' })).toHaveValue('0');
    expect(onCommand).not.toHaveBeenCalled();
    onPreview.mockClear();
    unmount();
    expect(onPreview).toHaveBeenCalledWith(null);
  });


  it('reviews only the frozen image, clay and prompt package, not later artifacts', () => {
    const data = fixture();
    data.snapshot.motion_previews!.push({ id: 'new-preview', shot_id: data.shot.id, video_id: 'video-b', receipt_id: 'motion-receipt' });
    mount(data);
    expect(screen.getByRole('img', { name: '@Image1 · Hero cup' })).toHaveAttribute('src', '/cup.png');
    expect(screen.getByLabelText('Submitted clay motion reference')).toHaveAttribute('src', '/motion.mp4');
    expect(screen.getByLabelText('Technical motion preview')).toHaveAttribute('src', '/test-media/b.mp4');
    expect(screen.getByText('Use @Image1 for the cup and @Video1 for motion.')).toBeVisible();
    expect(screen.getByText(/Estimated video cost/)).toHaveTextContent('$0.42');
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeDisabled();
  });

  it('requires a new approval whenever the compiled request changes and blocks missing evidence', () => {
    const data = fixture(); const { update, onCommand } = mount(data);
    approveRequest();
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeEnabled();
    update({ snapshot: { ...data.snapshot, packages: [{ ...data.snapshot.packages![0], id: 'submission-2', review_sha256: 'b'.repeat(64) }] } });
    expect(screen.getByRole('checkbox', { name: 'I approve this exact request and authorize its API cost.' })).not.toBeChecked();
    expect(screen.getByLabelText('Generation approval note')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeDisabled();
    update({ snapshot: { ...data.snapshot, artifacts: data.snapshot.artifacts!.filter(artifact => artifact.id !== 'image-1') } });
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeDisabled();
    expect(onCommand).not.toHaveBeenCalled();
  });

  it('never retries a submitted package whose take has not arrived yet', () => {
    const data = fixture(); data.snapshot.jobs!.push({ id: 'pending-video', run_id: 'submission-run', category: 'video', status: 'submission_unknown' });
    mount(data);
    expect(screen.getByText(/This package was already dispatched/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Generate movie take' })).toBeDisabled();
  });

  it('selects named appearance states and historical approved versions explicitly', () => {
    const data = fixture();
    data.snapshot.reference_versions = [
      { id: 'cup-approved-1', asset_id: 'PROP-cup', version: 1, references: [{ slot: 'primary', state: 'default' }, { slot: 'primary', state: 'partially-used' }] },
      { id: 'cup-approved-2', asset_id: 'PROP-cup', version: 2, references: [{ slot: 'primary', state: 'default' }] },
      { id: 'cup-draft-3', asset_id: 'PROP-cup', version: 3, references: [{ slot: 'primary', state: 'new-draft' }] },
    ];
    data.snapshot.reference_decisions = [{ id: 'approve-1', revision_id: 'cup-approved-1', decision: 'approved' }, { id: 'approve-2', revision_id: 'cup-approved-2', decision: 'approved' }];
    const { onCommand } = mount(data);
    const select = screen.getByRole('combobox', { name: 'Version and state for Hero cup' });
    expect(select).toHaveValue(JSON.stringify(['cup-approved-1', 'default']));
    expect(screen.queryByRole('option', { name: /new-draft/ })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: JSON.stringify(['cup-approved-1', 'partially-used']) } });
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    expect(onCommand).toHaveBeenCalledWith('update_shot_plan', expect.objectContaining({ reference_pins: [{ asset_id: 'PROP-cup', revision_id: 'cup-approved-1', state: 'partially-used' }] }));
  });

  it('blocks duplicate mutation controls while a shared-plan command is active', () => {
    const data = fixture();
    data.snapshot.commands = [{ command_id: 'command-render', operation: 'render_motion', status: 'accepted' }];
    const { onCommand } = mount(data);
    for (const name of ['Save shared plan', 'Render motion', 'Prepare submission', 'Generate movie take']) {
      const button = screen.getByRole('button', { name });
      expect(button).toBeDisabled();
      fireEvent.click(button);
    }
    expect(onCommand).not.toHaveBeenCalled();
  });
});
