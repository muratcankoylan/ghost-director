// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { TerminalApp, type TerminalAppProps } from '../../src/app/TerminalApp';
import { terminalFixture } from './fixtures';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { vi.restoreAllMocks(); });

function props(overrides: Partial<TerminalAppProps> = {}): TerminalAppProps {
  return { snapshot: terminalFixture(), connection: 'connected', onCommand: vi.fn().mockResolvedValue({ status: 'completed' }), renderViewport: () => <div data-testid="scene-canvas">Test viewport integration slot</div>, ...overrides };
}

describe('terminal command and navigation boundaries', () => {
  it('starts on a shot for the current scene without overriding deliberate historical selection', () => {
    const fixture = terminalFixture();
    fixture.shots![0].scene_revision_id = 'scene-earlier';
    fixture.shots![1].scene_revision_id = 'scene-earlier';
    const input = props({ snapshot: fixture });
    const view = render(<TerminalApp {...input} />);
    expect(screen.getByRole('button', { name: /Shot 03: The response/ })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: /Shot 01: The arrival/ }));
    view.rerender(<TerminalApp {...input} snapshot={{ ...fixture, last_seq: 1 }} />);
    expect(screen.getByRole('button', { name: /Shot 01: The arrival/ })).toHaveAttribute('aria-pressed', 'true');
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('pin take A, select shot B, accept the explicitly displayed take A', async () => {
    const input = props();
    input.snapshot!.takes![0].scene_revision_id = 'scene-current';
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    fireEvent.click(screen.getByRole('button', { name: 'Pin inspector' }));
    fireEvent.click(screen.getByRole('button', { name: /Shot 02: The exchange/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Accept', exact: true }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('take-a')).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept take' }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'accept_take', expected_revision: 'scene-current', payload: expect.objectContaining({ take_id: 'take-a', package_id: 'package-a', source_scene_revision_id: 'scene-current' }) }));
  });

  it('requires a rejection reason and issues only a disposition command', async () => {
    const input = props(); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject', exact: true }));
    const dialog = screen.getByRole('dialog');
    const reject = within(dialog).getByRole('button', { name: 'Reject take' });
    expect(reject).toBeDisabled();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Decision reason (required)' }), { target: { value: 'The cup changes identity.' } });
    fireEvent.click(reject);
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'reject_take', payload: expect.objectContaining({ reason: 'The cup changes identity.', take_id: 'take-a' }) }));
  });

  it('disables all mutation controls in recorded replay', () => {
    const input = props({ replayRunId: 'run-a' }); render(<TerminalApp {...input} />);
    expect(screen.getByText('RECORDED REPLAY')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(screen.getByRole('button', { name: 'Accept', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Repair', exact: true })).toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });


  it('routes a shared shot into complete request review without calling legacy generation', () => {
    const fixture = terminalFixture();
    fixture.shots![0].reference_pins = [{ asset_id: 'PROP-cup', revision_id: 'cup-approved', state: 'default' }];
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    expect(screen.queryByRole('button', { name: 'Generate from rendered frames', exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review production request' }));
    expect(screen.getByRole('region', { name: 'Shared shot plan' })).toBeVisible();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('shows the submitted clay input and prevents legacy prompt repair for a shared take', async () => {
    const fixture = terminalFixture(); fixture.takes![0].scene_revision_id = 'scene-current';
    fixture.packages = [{ id: 'package-a', shot_id: 'shot-a', workflow: 'reference-motion-v1', reference_ids: [], motion_video_id: 'video-b', prompt_ref: 'prompt-ref' }];
    fixture.artifacts!.push({ id: 'prompt-ref', name: 'Compiled prompt', mime_type: 'text/plain', kind: 'prompt', sha256: 'c'.repeat(64), bytes: 20, url: '/prompt.txt', relative_path: 'prompt.txt' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => 'Frozen shot instructions.' }));
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(screen.getByText('@Video1 · Blender motion only')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Repair', exact: true })).toBeDisabled();
    await screen.findByText('Frozen shot instructions.');
    expect(screen.queryByRole('button', { name: 'Fork an editable prompt draft' })).not.toBeInTheDocument();
    expect(input.onCommand).not.toHaveBeenCalled();
  });


  it('keeps the comparison next to the existing world and permits only its local review while previewing', async () => {
    const fixture = terminalFixture();
    fixture.reference_assets = [{ id: 'PROP-cup', category: 'prop', name: 'Hero cup', revision_id: 'cup-v1', approved_revision_id: 'cup-v1', version: 1, approval_status: 'approved' }];
    fixture.reference_versions = [{ id: 'cup-v1', asset_id: 'PROP-cup', version: 1, references: [{ slot: 'primary', state: 'default', artifact_id: 'cup-photo' }] }];
    fixture.reference_decisions = [{ id: 'approve-cup', asset_id: 'PROP-cup', revision_id: 'cup-v1', decision: 'approved' }];
    fixture.reconstructions = [{ id: 'reconstruction-cup', asset_id: 'PROP-cup', reference_pin: { asset_id: 'PROP-cup', revision_id: 'cup-v1', state: 'default' }, status: 'ready_for_review', artifact_id: 'cup-mesh', dimensions: [.1,.1,.2], height_m: .2 }];
    fixture.artifacts!.push({ id: 'cup-photo', name: 'Approved cup', mime_type: 'image/png', kind: 'reference', sha256: 'c'.repeat(64), bytes: 20, url: '/cup.png', relative_path: 'cup.png' }, { id: 'cup-mesh', name: 'Cup reconstruction', mime_type: 'model/gltf-binary', kind: 'model', sha256: 'd'.repeat(64), bytes: 20, url: '/cup.glb', relative_path: 'cup.glb' });
    fixture.scene!.entities.push({ ...fixture.scene!.entities[0], id: 'room-blocking-view', kind: 'room' });
    const viewport = vi.fn((_: Parameters<TerminalAppProps['renderViewport']>[0]) => <div data-testid="scene-canvas" />);
    const input = props({ snapshot: fixture, renderViewport: viewport }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'References', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: /Props/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Compare in world' }));
    expect(screen.getByRole('complementary', { name: 'Reference to 3D comparison' })).toBeVisible();
    expect(screen.getByTestId('scene-canvas')).toBeVisible();
    expect(viewport.mock.calls.at(-1)![0].scene?.entities.map((entity) => entity.id)).toEqual(['preview-reconstruction-cup']);
    expect(fixture.scene!.entities.map((entity) => entity.id)).toEqual(['cup', 'room-blocking-view']);
    expect(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true })).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Asset comparison notes' }), { target: { value: 'Surface geometry needs correction.' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Compare geometry' }), { target: { value: 'mismatch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Request asset changes' }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'review_reconstruction', payload: expect.objectContaining({ reconstruction_id: 'reconstruction-cup', decision: 'changes_requested', criteria: expect.objectContaining({ geometry: 'mismatch' }) }) }));
  });

  it('locks duplicate dispatch before the first command receipt arrives', async () => {
    let complete!: (value: unknown) => void;
    const input = props({ onCommand: vi.fn(() => new Promise((resolve) => { complete = resolve; })) });
    render(<TerminalApp {...input} />);
    const generate = screen.getByRole('button', { name: 'Generate from rendered frames', exact: true });
    fireEvent.click(generate); fireEvent.click(generate);
    expect(input.onCommand).toHaveBeenCalledTimes(1);
    complete({ status: 'completed' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true })).not.toBeDisabled());
  });

  it('preserves pinned inspection across 100 incoming recorded events and bounds rows', () => {
    const input = props(); const view = render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    fireEvent.click(screen.getByRole('button', { name: 'Pin inspector' }));
    const next = terminalFixture();
    next.events = Array.from({ length: 1000 }, (_, index) => ({ id: `event-${index}`, seq: index + 1, type: 'task.completed', recorded_at: '2026-11-03T12:00:00Z', run_id: 'run-a', task_id: null, job_id: null, payload: {}, artifact_refs: [] }));
    next.last_seq = 1000;
    view.rerender(<TerminalApp {...input} snapshot={next} />);
    expect(screen.getByText('Pinned context')).toBeVisible();
    expect(screen.getByText('Target: take-a', { exact: false })).toBeVisible();
    expect(document.querySelectorAll('.handoff-row')).toHaveLength(80);
    expect(screen.getByTestId('scene-canvas')).toBeVisible();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('uses shot shortcuts outside the composer only', () => {
    const input = props(); render(<TerminalApp {...input} />);
    const direction = document.getElementById('direction-input')!;
    direction.focus(); fireEvent.keyDown(direction, { key: '2' });
    expect(screen.getByRole('button', { name: /Shot 01: The arrival/ })).toHaveAttribute('aria-pressed', 'true');
    direction.blur(); fireEvent.keyDown(window, { key: '2' });
    expect(screen.getByRole('button', { name: /Shot 02: The exchange/ })).toHaveAttribute('aria-pressed', 'true');
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('forks a prompt draft without changing the submitted artifact', async () => {
    const fixture = terminalFixture();
    fixture.artifacts!.push({ id: 'prompt-a', kind: 'prompt', mime_type: 'text/plain', sha256: 'submitted-hash-original', bytes: 30, name: 'Submitted Seedance prompt', relative_path: 'prompt.txt', metadata: { shot_id: 'shot-a' }, url: '/test-media/prompt.txt' });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => 'Original immutable prompt.' }));
    const input = props({ snapshot: fixture });
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: /Shot 01: The arrival/ }));
    await screen.findByText('Original immutable prompt.');
    fireEvent.click(screen.getByRole('button', { name: 'Fork an editable prompt draft' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Prompt draft' }), { target: { value: 'Future draft with a tighter framing.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Keep draft' }));
    expect(screen.getByText('Original immutable prompt.')).toBeVisible();
    expect(screen.getByText(/Original SHA-256 submitted-ha/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'generate', payload: { shot_id: 'shot-a', prompt_override: 'Future draft with a tighter framing.' } })));
    vi.unstubAllGlobals();
  });

  it('previews and cancels a transform without writing creative state', async () => {
    const renderViewport = vi.fn(() => <div data-testid="scene-canvas" />);
    const input = props({ renderViewport });
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Ceramic cup', exact: true }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Position X' }), { target: { value: '0.45' } });
    await waitFor(() => expect(renderViewport.mock.lastCall?.[0].scene?.entities[0].position[0]).toBe(0.45));
    expect(input.onCommand).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel edit' }));
    await waitFor(() => expect(renderViewport.mock.lastCall?.[0].scene?.entities[0].position[0]).toBe(0));
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('inspects a take using its immutable package rather than the latest shot package', async () => {
    const fixture = terminalFixture();
    fixture.shots![0].duration = 8;
    fixture.packages = [{ id: 'package-a', shot_id: 'shot-a', prompt_ref: 'prompt-original', reference_ids: [], duration: 4, camera_id: 'camera-original', scene_revision_id: 'scene-source-a', assertions: ['Original framing remains fixed.'] }, { id: 'package-newer', shot_id: 'shot-a', prompt_ref: 'prompt-newer', reference_ids: [], duration: 8 }];
    fixture.artifacts!.push(...['original', 'newer'].map((version) => ({ id: `prompt-${version}`, kind: 'prompt', mime_type: 'text/plain', sha256: `hash-${version}`, bytes: 30, name: `Prompt ${version}`, relative_path: `${version}.txt`, metadata: {}, url: `/test-media/${version}.txt` })));
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, text: async () => url.includes('original') ? 'Original package prompt.' : 'Newer package prompt.' })));
    render(<TerminalApp {...props({ snapshot: fixture })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(await screen.findByText('Original package prompt.')).toBeVisible();
    expect(screen.queryByText('Newer package prompt.')).not.toBeInTheDocument();
    const source = screen.getByRole('heading', { name: 'Generation package' }).closest('section')!;
    expect(source).toHaveTextContent('4 s');
    expect(source).not.toHaveTextContent('8 s');
    expect(source).toHaveTextContent('Original framing remains fixed.');
  });

  it('shows an asynchronously failed command until dismissed without repeated alerts', () => {
    const input = props(); const view = render(<TerminalApp {...input} />);
    const failed = terminalFixture();
    failed.events = [{ id: 'failure-1', seq: 1, type: 'command.failed', recorded_at: '2026-11-03T12:00:00Z', run_id: null, task_id: null, job_id: null, payload: { error: 'Scene revision conflicts with the accepted head.' } }];
    view.rerender(<TerminalApp {...input} snapshot={failed} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Scene revision conflicts');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss error' }));
    view.rerender(<TerminalApp {...input} snapshot={{ ...failed, events: [...failed.events!] }} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows unavailable historical accounting as unknown rather than zero spend', () => {
    const fixture = terminalFixture(); fixture.budget = { status: 'historical_unavailable' };
    render(<TerminalApp {...props({ snapshot: fixture, replayRunId: 'run-a' })} />);
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect budget receipts' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Historical balance unavailable.');
  });

  it('keeps pinned entity values on their source revision when the accepted scene advances', () => {
    const input = props(); const view = render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Ceramic cup', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Pin inspector' }));
    const next = terminalFixture(); next.scene!.id = 'scene-next'; next.scene!.entities[0].position[0] = 2;
    view.rerender(<TerminalApp {...input} snapshot={next} />);
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).toHaveValue(0);
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect current revision' }));
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).toHaveValue(2);
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).not.toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('retires an uncertain job only with an explicit reason and never resubmits it', async () => {
    const fixture = terminalFixture();
    fixture.models!.push({ id: 'seedance-video', model: 'seedance', label: 'Seedance', provider: 'openrouter' });
    fixture.jobs!.push({ id: 'job-uncertain', status: 'submission_unknown', model: 'seedance', provider: 'openrouter', shot_id: 'shot-a', category: 'video', remote_id: null, error: 'Transport ended before a remote receipt arrived.' });
    fixture.budget!.categories.video = { reported_usd: 0, estimated_usd: 0, reserved_usd: 0.63, ceiling_usd: 60, remaining_usd: 59.37 };
    const input = props({ snapshot: fixture }); const view = render(<TerminalApp {...input} />);
    expect(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true })).toBeDisabled();
    fireEvent.click(screen.getByTitle('Video generation · Hosted service'));
    expect(document.querySelector('.inspector-overview')).toHaveTextContent('Submission unknown');
    expect(document.querySelector('.inspector-overview')).not.toHaveTextContent('Ready');
    expect(screen.queryByRole('button', { name: 'Request cancellation' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retire uncertain attempt' }));
    const dialog = screen.getByRole('dialog', { name: 'Retire uncertain attempt' });
    const retire = within(dialog).getByRole('button', { name: 'Retire from dispatch' });
    expect(retire).toBeDisabled();
    expect(dialog).toHaveTextContent('The original may still complete or incur a charge');
    expect(dialog).toHaveTextContent('Remains fully held');
    expect(input.onCommand).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Operator reason (required)' }), { target: { value: 'No remote ID is recoverable; prepare a separate smaller-reference experiment.' } });
    fireEvent.click(retire);
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'retire_unknown', expected_revision: 'scene-current', payload: { job_id: 'job-uncertain', reason: 'No remote ID is recoverable; prepare a separate smaller-reference experiment.' } }));
    const retired = { ...fixture, jobs: fixture.jobs!.map((job) => job.id === 'job-uncertain' ? { ...job, dispatch_retired: true } : job) };
    view.rerender(<TerminalApp {...input} snapshot={retired} />);
    await waitFor(() => expect(screen.getByText(/Retired from dispatch exclusion/)).toBeVisible());
    expect(screen.getByRole('button', { name: 'Generate from rendered frames', exact: true })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Inspect budget receipts' })).toHaveTextContent('$0.63');
    expect(input.onCommand).toHaveBeenCalledTimes(1);
    expect(retired.jobs!.at(-1)!.status).toBe('submission_unknown');
  });

  it('explains fused child geometry and routes edits to its parent actor', () => {
    const fixture = terminalFixture();
    const base = fixture.scene!.entities[0];
    fixture.scene!.entities.push({ ...base, id: 'actor-leyla', name: 'Leyla', kind: 'actor' }, { ...base, id: 'chair-leyla', name: 'Leyla chair', kind: 'prop', visible: false, state: { included_in_entity_id: 'actor-leyla' } });
    const input = props({ snapshot: fixture });
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: /Leyla chair/ }));
    expect(screen.getByText(/Included in Leyla; moves with actor/)).toBeVisible();
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).toBeDisabled();
    expect(screen.getByRole('spinbutton', { name: 'Rotation Z' })).toBeDisabled();
    expect(screen.getByLabelText('Material color')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Leyla', exact: true }));
    expect(screen.getByRole('spinbutton', { name: 'Position X' })).not.toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('uses neutral video labels and saves a 15-second shared plan independently of the legacy video binding', async () => {
    const fixture = terminalFixture();
    fixture.models!.push({ id: 'seedance', model: 'seedance', label: 'Seedance 2.5', category: 'video', active: false, duration_seconds: 5 }, { id: 'veo', model: 'google/veo-3.1', label: 'Google Veo 3.1', category: 'video', active: true, duration_seconds: 8 });
    const { id: _id, name: _name, ...cameraDraft } = fixture.scene!.cameras[0];
    const input = props({ snapshot: fixture, renderViewport: ({ onCameraDraft }) => <button onClick={() => onCameraDraft(cameraDraft)}>Set test camera draft</button> });
    const view = render(<TerminalApp {...input} />);
    expect(document.querySelector('.media-panel .panel-label')).toHaveTextContent('VIDEO GENERATION TAKE');
    expect(document.body.textContent).not.toMatch(/seedance|veo|meshy|openrouter/i);
    expect(screen.getByTitle('Meta Wonder 1.0 · Meta')).toBeVisible();
    expect(fixture.jobs![0].model).toBe('seedance');
    expect(fixture.models!.at(-1)!.model).toBe('google/veo-3.1');
    for (const element of document.querySelectorAll('[title],[aria-label],[alt]')) {
      expect([element.getAttribute('title'), element.getAttribute('aria-label'), element.getAttribute('alt')].join(' ')).not.toMatch(/seedance|veo|meshy|openrouter/i);
    }
    view.rerender(<TerminalApp {...input} snapshot={{ ...fixture, takes: [] }} />);
    expect(document.querySelector('.media-panel .panel-label')).toHaveTextContent('VIDEO GENERATION TAKE');
    fireEvent.click(screen.getByRole('button', { name: 'Set test camera draft' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }));
    const duration = screen.getByRole('spinbutton', { name: 'Duration (seconds)' });
    expect(duration).toHaveValue(4);
    expect(duration).not.toHaveAttribute('readonly');
    fireEvent.change(duration, { target: { value: '15' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'What happens in this shot?' }), { target: { value: 'Both actors remain seated.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save shot', exact: true }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'save_shot', payload: expect.objectContaining({ duration: 15, workflow: 'reference-motion-v1' }) })));
    expect(input.onCommand).toHaveBeenCalledTimes(1);
  });

  it('applies an entity edit without resubmitting immutable asset provenance', async () => {
    const fixture = terminalFixture();
    fixture.scene!.entities[0].state = { artifact_id: 'approved-model-artifact', source_image_artifact_id: 'licensed-image-artifact' };
    const input = props({ snapshot: fixture });
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Ceramic cup', exact: true }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Position X' }), { target: { value: '0.2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply revision' }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    const command = vi.mocked(input.onCommand).mock.calls[0][0];
    expect(command.operation).toBe('apply_scene');
    expect(command.payload.patches).toEqual([expect.objectContaining({ entity_id: 'cup', position: [0.2, 0, 1], state: { material_override: false } })]);
    expect(JSON.stringify(command.payload)).not.toContain('artifact_id');
  });

  it('prevents applying a stale take while allowing its rejection', () => {
    const input = props();
    render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(screen.getByText('Source scene differs; inspect or restore before applying.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Accept', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Repair', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject', exact: true })).not.toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('locks an open acceptance dialog if the accepted scene advances', () => {
    const input = props(); input.snapshot!.takes![0].scene_revision_id = 'scene-current';
    const view = render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    fireEvent.click(screen.getByRole('button', { name: 'Accept', exact: true }));
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Accept take' })).not.toBeDisabled();
    const next = terminalFixture(); next.scene!.id = 'scene-next';
    view.rerender(<TerminalApp {...input} snapshot={next} />);
    const confirm = within(screen.getByRole('dialog')).getByRole('button', { name: 'Accept take' });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('reports attached inputs only when recorded source outputs intersect destination inputs', () => {
    const fixture = terminalFixture();
    fixture.tasks = [{ id: 'producer', role_label: 'Producer', status: 'completed', result: { output_refs: ['brief-artifact'] } }, { id: 'dp', role_label: 'Director of Photography', status: 'completed', input_refs: ['brief-artifact'] }];
    fixture.handoffs = [{ id: 'handoff-verified', from_task_id: 'producer', to_task_id: 'dp', input_refs: ['brief-artifact'], summary: 'Plan the camera.' }];
    const input = props({ snapshot: fixture }); const view = render(<TerminalApp {...input} />);
    expect(screen.getByText('Inputs attached')).toBeVisible();
    expect(screen.queryByText('Consumed')).not.toBeInTheDocument();
    view.rerender(<TerminalApp {...input} snapshot={{ ...fixture, tasks: fixture.tasks.map((task) => task.id === 'dp' ? { ...task, input_refs: [] } : task) }} />);
    expect(screen.getByText('Consumption unrecorded')).toBeVisible();
    expect(screen.queryByText('Inputs attached')).not.toBeInTheDocument();
  });

  it('previews positive scale and blocks zero or negative values before emitting a clean patch', async () => {
    const fixture = terminalFixture(); fixture.scene!.entities[0].state = { artifact_id: 'pinned-asset' };
    const renderViewport = vi.fn(() => <div data-testid="scene-canvas" />);
    const input = props({ snapshot: fixture, renderViewport }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Ceramic cup', exact: true }));
    const scale = screen.getByRole('spinbutton', { name: 'Scale X' });
    fireEvent.change(scale, { target: { value: '0' } });
    expect(screen.getByRole('button', { name: 'Apply revision' })).toBeDisabled();
    fireEvent.change(scale, { target: { value: '-1' } });
    expect(screen.getByRole('button', { name: 'Apply revision' })).toBeDisabled();
    fireEvent.change(scale, { target: { value: '1.5' } });
    await waitFor(() => expect(renderViewport.mock.lastCall?.[0].scene?.entities[0].scale).toEqual([1.5, 1, 1]));
    expect(input.onCommand).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply revision' }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    const command = vi.mocked(input.onCommand).mock.calls[0][0];
    expect(command.payload.patches).toEqual([expect.objectContaining({ scale: [1.5, 1, 1], state: { material_override: false } })]);
    expect(JSON.stringify(command.payload)).not.toContain('artifact_id');
  });

  it('bounds light intensity and sends only the creative light state', async () => {
    const fixture = terminalFixture();
    fixture.scene!.entities[0] = { ...fixture.scene!.entities[0], name: 'Practical light', kind: 'light', state: { intensity: 150, light_type: 'point', artifact_id: 'pinned-light' } };
    const renderViewport = vi.fn(() => <div data-testid="scene-canvas" />);
    const input = props({ snapshot: fixture, renderViewport }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Practical light', exact: true }));
    const intensity = screen.getByRole('spinbutton', { name: 'Light intensity' });
    for (const value of ['-1', '1001']) {
      fireEvent.change(intensity, { target: { value } });
      expect(screen.getByRole('button', { name: 'Apply revision' })).toBeDisabled();
    }
    fireEvent.change(intensity, { target: { value: '100' } });
    await waitFor(() => expect(renderViewport.mock.lastCall?.[0].scene?.entities[0].state).toEqual(expect.objectContaining({ intensity: 100, artifact_id: 'pinned-light' })));
    fireEvent.click(screen.getByRole('button', { name: 'Apply revision' }));
    await waitFor(() => expect(input.onCommand).toHaveBeenCalledTimes(1));
    const command = vi.mocked(input.onCommand).mock.calls[0][0];
    expect(command.payload.patches).toEqual([expect.objectContaining({ state: { material_override: false, intensity: 100 } })]);
    expect(JSON.stringify(command.payload)).not.toContain('artifact_id');
  });

  it('separates the one submitted first-frame input from the planner three-image context', () => {
    const fixture = terminalFixture();
    fixture.packages = [{ id: 'package-a', shot_id: 'shot-a', reference_ids: ['scene-frame'], planning_reference_ids: ['scene-frame', 'eren-ref', 'leyla-ref'], conditioning_mode: 'first_frame' }];
    fixture.artifacts!.push(...['scene-frame', 'eren-ref', 'leyla-ref'].map((id) => ({ id, name: id, kind: 'image', mime_type: 'image/jpeg', sha256: id, bytes: 10, relative_path: `${id}.jpg`, metadata: {}, url: `/test-media/${id}.jpg` })));
    fixture.tasks = [{ id: 'planner-a', role_id: 'role.generation-technical.prompt-compiler-generation-planner', role_label: 'Generation Planner', status: 'completed', shot_id: 'shot-a', input_refs: ['scene-frame', 'eren-ref', 'leyla-ref'] }];
    render(<TerminalApp {...props({ snapshot: fixture })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    const videoInputs = screen.getByRole('heading', { name: /Submitted video inputs/ }).closest('section')!;
    expect(videoInputs).toHaveTextContent('1 reference');
    expect(within(videoInputs).getAllByRole('button')).toHaveLength(1);
    expect(videoInputs).toHaveTextContent('scene-frame');
    expect(videoInputs).not.toHaveTextContent('eren-ref');
    expect(document.querySelector('.planning-context summary')).toHaveTextContent('3 references');
    fireEvent.click(within(screen.getByRole('complementary', { name: 'Production navigator' })).getByRole('button', { name: /Generation Planner/ }));
    const plannerInputs = screen.getByRole('heading', { name: /^Inputs/ }).closest('section')!;
    expect(plannerInputs).toHaveTextContent('3 references');
    expect(within(plannerInputs).getAllByRole('button')).toHaveLength(3);
    expect(screen.queryByRole('heading', { name: /Submitted video inputs/ })).not.toBeInTheDocument();
  });

  it('opens previous video model receipts without changing the active model or dispatching work', () => {
    const fixture = terminalFixture();
    fixture.models!.push({ id: 'seedance', model: 'seedance', label: 'Seedance 2.5', provider: 'openrouter', category: 'video', active: false }, { id: 'veo-frame', model: 'fal-ai/veo3.1/image-to-video', label: 'Veo frame', category: 'video', active: true });
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Video history 1' }));
    const inspector = screen.getByRole('complementary', { name: 'Inspector' });
    expect(within(inspector).getByRole('heading', { name: 'Video history' })).toBeVisible();
    fireEvent.click(within(inspector).getByRole('button', { name: /Video generation/ }));
    expect(document.querySelector('.inspector-overview')).toHaveTextContent('model_call / job-b');
    expect(input.onCommand).not.toHaveBeenCalled();
    expect(fixture.models!.at(-1)!.active).toBe(true);
  });

  it('labels ordered start and proposed end frames separately from planner-only actor context', () => {
    const fixture = terminalFixture();
    fixture.packages = [{ id: 'package-a', shot_id: 'shot-a', reference_ids: ['start', 'end'], planning_reference_ids: ['start', 'end', 'actor'], conditioning_mode: 'first_last_frame' }];
    fixture.artifacts!.push(...['start', 'end', 'actor'].map((id) => ({ id, name: `${id}.jpg`, kind: 'image', mime_type: 'image/jpeg', sha256: id, bytes: 10, relative_path: `${id}.jpg`, metadata: {}, url: `/test-media/${id}.jpg` })));
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    const videoInputs = screen.getByRole('heading', { name: /Submitted video inputs/ }).closest('section')!;
    expect(videoInputs).toHaveTextContent('2 references');
    expect(videoInputs).toHaveTextContent('not an accepted scene revision');
    const references = within(videoInputs).getAllByRole('button');
    expect(references).toHaveLength(2);
    expect(references[0]).toHaveTextContent('Start frame');
    expect(references[0]).toHaveTextContent('start.jpg');
    expect(references[1]).toHaveTextContent('Proposed end frame');
    expect(references[1]).toHaveTextContent('end.jpg');
    expect(videoInputs).not.toHaveTextContent('actor.jpg');
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it.each(['accepted', 'rejected'] as const)('shows the latest exact-take %s decision and preserves supervisor warnings', (status) => {
    const fixture = terminalFixture(); fixture.takes![0].status = status;
    const event = { id: 'decision-old', seq: 1, type: 'take.rejected', recorded_at: '2026-11-03T12:00:00Z', run_id: 'run-a', task_id: null, job_id: null, payload: { take_id: 'take-a', actor: 'earlier-actor', reason: 'Earlier decision.' } };
    fixture.events = [event, { ...event, id: 'decision-current', seq: 2, type: `take.${status}`, payload: { take_id: 'take-a', actor: 'build-reviewer', reason: `Current ${status} decision with a retained warning.` } }, { ...event, id: 'decision-other-take', seq: 3, payload: { take_id: 'take-b', actor: 'other-actor', reason: 'Unrelated decision.' } }];
    fixture.observations = [{ id: 'wardrobe-warning', take_id: 'take-a', assertion: 'Wardrobe remains consistent.', status: 'fail', detail: 'The wardrobe warning is still recorded.', time_ms: null, method: 'sampled-frames' }];
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    const decision = document.querySelector('.creative-decision')!;
    expect(decision).toHaveTextContent('build-reviewer');
    expect(decision).toHaveTextContent(`Current ${status} decision`);
    expect(decision).not.toHaveTextContent('Earlier decision');
    expect(decision).not.toHaveTextContent('Unrelated decision');
    expect(screen.queryByText('Source scene differs; inspect or restore before applying.')).not.toBeInTheDocument();
    expect(screen.getByText('The wardrobe warning is still recorded.', { exact: false })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Accept', exact: true })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject', exact: true })).toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('marks the decision actor and reason unavailable when its event is absent', () => {
    const fixture = terminalFixture(); fixture.takes![0].status = 'accepted';
    render(<TerminalApp {...props({ snapshot: fixture })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(document.querySelector('.creative-decision')).toHaveTextContent('Actor and reason are unavailable.');
  });

  it('distinguishes imported task and handoff evidence and exported prompt hashes from executed calls', async () => {
    const fixture = terminalFixture();
    fixture.tasks = [{ id: 'imported-producer', role_id: 'role.producer.producer-orchestrator', role_label: 'Producer', run_id: 'import-run', status: 'imported', evidence_origin: 'imported', source_run_id: 'original-run', source_status: 'completed', prompt_ref: 'prompt-imported', input_refs: [], output_refs: ['video-a'] }, { id: 'imported-dp', role_id: 'role.cinematography.director-of-photography', role_label: 'Director of Photography', run_id: 'import-run', status: 'imported', evidence_origin: 'imported', source_run_id: 'original-run', source_status: 'completed', input_refs: ['video-a'] }];
    fixture.handoffs = [{ id: 'imported-handoff', run_id: 'import-run', from_task_id: 'imported-producer', to_task_id: 'imported-dp', input_refs: ['video-a'], summary: 'Historical handoff', evidence_origin: 'imported', source_run_id: 'original-run' }];
    fixture.artifacts!.push({ id: 'prompt-imported', kind: 'prompt', mime_type: 'application/json', sha256: 'exported-content-hash', bytes: 100, name: 'Historical prompt', relative_path: 'prompt.json', url: '/test-media/prompt.json', metadata: { export_transformation: 'sanitized JSON; provider transport and system/context text omitted', demo_source_sha256: 'original-submitted-hash' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, text: async () => JSON.stringify({ messages: [{ role: 'user', content: 'The recorded source direction.' }] }) }));
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(within(screen.getByRole('complementary', { name: 'Production navigator' })).getByRole('button', { name: /Producer/ }));
    expect(screen.getByRole('heading', { name: 'Imported source evidence' })).toBeVisible();
    expect(screen.getByText('These records document a historical run. Importing them executed no provider calls.')).toBeVisible();
    await screen.findByText('The recorded source direction.');
    expect(screen.getByText(/Exported derivative hash/)).toHaveTextContent('exported-con');
    expect(screen.queryByText(/Submitted hash/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Exported prompt' }));
    expect(within(screen.getByRole('dialog')).getByText(/Exported derivative hash/)).toBeVisible();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close dialog' }));
    const handoffRow = screen.getByRole('button', { name: /Historical handoff/ });
    expect(handoffRow).toHaveTextContent('Imported ·');
    fireEvent.click(handoffRow);
    expect(screen.getByRole('heading', { name: 'Imported source evidence' })).toBeVisible();
    expect(screen.getAllByText('Inputs attached').length).toBeGreaterThan(0);
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('shows an imported creative decision as historical without inventing a local receipt', () => {
    const fixture = terminalFixture(); fixture.takes![0].status = 'accepted'; fixture.jobs = [];
    fixture.events = [{ id: 'imported-decision', seq: 1, type: 'demo.disposition_imported', recorded_at: '2026-11-03T12:00:00Z', run_id: 'import-run', task_id: null, job_id: null, payload: { take_id: 'take-a', disposition: 'accepted', actor: 'historical-reviewer', reason: 'Source acceptance with a recorded limitation.', evidence_origin: 'imported' } }];
    const input = props({ snapshot: fixture }); render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    expect(screen.getByRole('heading', { name: 'Historical creative decision' })).toBeVisible();
    expect(document.querySelector('.creative-decision')).toHaveTextContent('historical-reviewer');
    expect(document.querySelector('.creative-decision')).toHaveTextContent('no new acceptance or rejection was executed');
    expect(screen.getByText('Source call not imported')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Accept', exact: true })).toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });

  it('locks an open rejection dialog if another operator already disposed the take', () => {
    const input = props(); const view = render(<TerminalApp {...input} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect selected take' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject', exact: true }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Decision reason (required)' }), { target: { value: 'A bounded reason.' } });
    const next = terminalFixture(); next.takes![0].status = 'accepted';
    view.rerender(<TerminalApp {...input} snapshot={next} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('This take already has a recorded creative decision.');
    expect(within(dialog).getByRole('button', { name: 'Reject take' })).toBeDisabled();
    expect(input.onCommand).not.toHaveBeenCalled();
  });
});
