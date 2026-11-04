// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CreativeFlow } from '../../src/terminal/CreativeFlow';
import { ReferenceManager } from '../../src/terminal/ReferenceManager';
import { TerminalApp } from '../../src/app/TerminalApp';
import { terminalFixture } from './fixtures';
import type { Snapshot } from '../../src/api/contracts';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function referenceFixture(): Snapshot {
  return { ...terminalFixture(), reference_assets: [{ id: 'CHAR-001', category: 'character', name: 'Leyla', description: 'Ivory linen shirt, short dark hair.', revision_id: 'ref-v2', version: 2, approved_revision_id: 'ref-v1', approval_status: 'draft', missing_slots: [] }], reference_versions: [
    { id: 'ref-v1', asset_id: 'CHAR-001', version: 1, parent_revision_id: null, references: ['front', 'back', 'face'].map((slot) => ({ slot, state: 'default', artifact_id: `ref-${slot}`, original_artifact_id: 'ref-sheet', width: 512, height: 768, provenance: { source: 'generated' } })), visual_characteristics: '' },
    { id: 'ref-v2', asset_id: 'CHAR-001', version: 2, parent_revision_id: 'ref-v1', references: ['front', 'back', 'face'].map((slot) => ({ slot, state: 'default', artifact_id: `ref-${slot}`, original_artifact_id: 'ref-sheet', width: 512, height: 768, provenance: { source: 'generated' } })), visual_characteristics: '' },
  ], reference_decisions: [], artifacts: [...terminalFixture().artifacts!, ...['front', 'back', 'face', 'sheet'].map((slot) => ({ id: `ref-${slot}`, kind: 'image', mime_type: 'image/png', name: slot, url: `/${slot}.png`, sha256: 'c'.repeat(64), bytes: 100, relative_path: `${slot}.png` }))] };
}

const referenceProps = (snapshot = referenceFixture()) => ({ snapshot, readOnly: false, busy: null, onCommand: vi.fn().mockResolvedValue(true), onOpen: vi.fn(), onRefresh: vi.fn() });

describe('Creative crew', () => {
  it('follows real active work and never invents tasks or handoff edges', () => {
    const snapshot = terminalFixture();
    snapshot.tasks = [{ id: 'active', role_label: 'Set Designer', run_id: 'live-run', status: 'running', goal: 'Prepare the approved room.' }, { id: 'old', role_label: 'Historical Director', run_id: 'old-run', status: 'completed' }];
    const props = { snapshot, focus: null, onInspect: vi.fn(), onOpen: vi.fn(), onReplay: vi.fn(), replay: false };
    const view = render(<CreativeFlow {...props} />);
    expect(screen.getByText('Set Designer')).toBeVisible();
    expect(screen.queryByText('Historical Director')).not.toBeInTheDocument();
    expect(screen.getByText('WORKING')).toBeVisible();
    expect(document.querySelectorAll('.crew-handoff')).toHaveLength(0);
    view.rerender(<CreativeFlow {...props} snapshot={{ ...snapshot, tasks: [] }} />);
    expect(document.querySelectorAll('.crew-task-card')).toHaveLength(0);
    expect(screen.getByText('READY')).toBeVisible();
  });

  it('exposes a returned output only after the runtime records it', () => {
    const snapshot = terminalFixture();
    snapshot.tasks = [{ id: 'image-task', role_label: 'Reference artist', run_id: 'image-run', model: 'muse-image-1.0', status: 'running', output_refs: [] }];
    const props = { snapshot, focus: null, onInspect: vi.fn(), onOpen: vi.fn(), onReplay: vi.fn(), replay: false };
    const view = render(<CreativeFlow {...props} />);
    expect(screen.queryByText('Reference sheet')).not.toBeInTheDocument();
    const result = { id: 'new-image', kind: 'image', mime_type: 'image/png', name: 'Reference sheet', url: '/sheet.png', sha256: 'd'.repeat(64), bytes: 100, relative_path: 'sheet.png' };
    view.rerender(<CreativeFlow {...props} snapshot={{ ...snapshot, tasks: [{ ...snapshot.tasks[0], status: 'completed', output_refs: [result.id] }], artifacts: [result] }} />);
    fireEvent.click(screen.getByRole('button', { name: /Reference sheet/ }));
    expect(props.onOpen).toHaveBeenCalledWith(result);
    expect(screen.queryByText('WORKING')).not.toBeInTheDocument();
  });
});

describe('Reference identity workflow', () => {
  it('approves the exact complete version with a recorded director note', async () => {
    const props = referenceProps(); render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve references' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Record approval' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('What visual details are approved?'), { target: { value: 'Identity and linen wardrobe match in all three views.' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record approval' }));
    await waitFor(() => expect(props.onCommand).toHaveBeenCalledWith('approve_reference', { asset_id: 'CHAR-001', revision_id: 'ref-v2', decision: 'approved', reason: 'Identity and linen wardrobe match in all three views.' }));
  });

  it('keeps incomplete and replay references from being approved', () => {
    const props = referenceProps({ ...referenceFixture(), reference_assets: [{ ...referenceFixture().reference_assets![0], missing_slots: ['face'] }], reference_versions: referenceFixture().reference_versions!.map((version) => ({ ...version, references: version.references.filter((reference: Record<string, any>) => reference.slot !== 'face') })) });
    const view = render(<ReferenceManager {...props} />);
    expect(screen.getByRole('button', { name: 'Approve references' })).toBeDisabled();
    expect(screen.getByText('1 required view missing')).toBeVisible();
    view.rerender(<ReferenceManager {...props} snapshot={referenceFixture()} readOnly />);
    expect(screen.getByRole('button', { name: 'Approve references' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate reference sheet' })).toBeDisabled();
    expect(screen.getByLabelText('Upload reference image')).toBeDisabled();
  });

  it('shows historical identity versions without replacement or approval actions', () => {
    render(<ReferenceManager {...referenceProps()} />);
    fireEvent.change(screen.getByLabelText('Reference version'), { target: { value: 'ref-v1' } });
    expect(screen.getByText('Historical version · read only')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Approve references' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Generate reference sheet' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload image' })).not.toBeInTheDocument();
  });

  it('sends raw image bytes with the expected revision and exposes stale-upload failures', async () => {
    const request = vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: 'The reference version changed. Refresh before uploading.' }) });
    vi.stubGlobal('fetch', request);
    const props = referenceProps(); render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Replace image' })[0]);
    const file = new File(['image-bytes'], 'front.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Upload reference image'), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The reference version changed'));
    expect(request).toHaveBeenCalledWith('/api/productions/test-production/references/CHAR-001/images?expected_revision_id=ref-v2&slot=front&state=default', expect.objectContaining({ method: 'POST', body: file, headers: { 'Content-Type': 'image/png', 'X-Ghost-View-Mode': 'live' } }));
    expect(props.onCommand).not.toHaveBeenCalled();
  });

  it('preserves the original generated sheet as a distinct inspectable artifact', () => {
    const props = referenceProps(); render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Full sheet' })[0]);
    expect(props.onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'ref-sheet' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open Front · full body for Leyla' }));
    expect(props.onOpen).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'ref-front' }));
  });

  it('reconstructs the approved revision while a newer identity draft exists', async () => {
    const props = referenceProps(); render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reconstruct in 3D' }));
    fireEvent.change(screen.getByLabelText('Authored height (meters)'), { target: { value: '1.68' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reconstruct approved reference' }));
    await waitFor(() => expect(props.onCommand).toHaveBeenCalledWith('reconstruct_reference', { asset_id: 'CHAR-001', revision_id: 'ref-v1', state: 'default', height_m: 1.68, yaw_degrees: 0 }));
  });

  it('does not steal the selected identity when another session creates an asset', () => {
    const props = referenceProps(); const view = render(<ReferenceManager {...props} />);
    view.rerender(<ReferenceManager {...props} snapshot={{ ...props.snapshot, reference_assets: [...props.snapshot.reference_assets!, { ...props.snapshot.reference_assets![0], id: 'PROP-001', name: 'Background prop', category: 'prop' }] }} />);
    expect(screen.getByRole('heading', { name: 'Leyla' })).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'Background prop' })).not.toBeInTheDocument();
  });

  it('edits a bounded crop on the original sheet and sends normalized coordinates', async () => {
    const props = referenceProps(); render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Adjust crop' })[1]);
    const original = screen.getByRole('img', { name: 'Original reference image with crop bounds' });
    Object.defineProperty(original, 'naturalWidth', { value: 1920 }); Object.defineProperty(original, 'naturalHeight', { value: 1280 });
    fireEvent.load(original);
    fireEvent.change(screen.getByLabelText('Crop right percent'), { target: { value: '20' } });
    expect(screen.getByRole('button', { name: 'Save crop as new version' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Crop right percent'), { target: { value: '65' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save crop as new version' }));
    await waitFor(() => expect(props.onCommand).toHaveBeenCalledWith('crop_reference', { asset_id: 'CHAR-001', expected_revision_id: 'ref-v2', slot: 'back', state: 'default', box: [expect.closeTo(1 / 3, 12), 0, 0.65, 1] }));
  });

  it('keeps 3D review separate from placement and converts authored degrees to radians', async () => {
    const reconstruction = { id: 'recon-1', asset_id: 'CHAR-001', artifact_id: 'mesh-1', reference_pin: { asset_id: 'CHAR-001', revision_id: 'ref-v1', state: 'default' }, status: 'ready_for_review', height_m: 1.68, quality_status: 'Visual review required' };
    const props = { ...referenceProps({ ...referenceFixture(), reconstructions: [reconstruction], reconstruction_reviews: [{ id: 'review-1', reconstruction_id: 'recon-1', artifact_id: 'mesh-1', decision: 'accepted', notes: 'Accepted after comparison.' }] }), onPreviewReconstruction: vi.fn() };
    render(<ReferenceManager {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Compare in world' }));
    expect(props.onPreviewReconstruction).toHaveBeenCalledWith(reconstruction);
    expect(props.onCommand).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Place in set' }));
    fireEvent.change(screen.getByLabelText('Placement yaw degrees'), { target: { value: '90' } });
    fireEvent.change(screen.getByLabelText('Placement X meters'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Place reviewed asset' }));
    await waitFor(() => expect(props.onCommand).toHaveBeenCalledWith('place_reference', { reconstruction_id: 'recon-1', position: [2, 0, 0], rotation: [0, 0, Math.PI / 2] }));
  });
});

describe('Shared-plan integration', () => {
  it('selects the new immutable shot after a saved-plan receipt arrives', async () => {
    const snapshot = terminalFixture();
    const onCommand = vi.fn().mockResolvedValue({ status: 'accepted' });
    const props = { snapshot, connection: 'connected' as const, onCommand, renderViewport: () => <canvas aria-label="Test world" /> };
    const view = render(<TerminalApp {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Compose shots' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save shared plan' }));
    await waitFor(() => expect(onCommand).toHaveBeenCalledWith(expect.objectContaining({ operation: 'update_shot_plan', payload: expect.objectContaining({ shot_id: 'shot-a' }) })));
    const command = onCommand.mock.calls[0][0];
    const revised = { ...snapshot.shots![0], id: 'shot-revised', parent_id: 'shot-a', name: 'Revised blocking' };
    view.rerender(<TerminalApp {...props} snapshot={{ ...snapshot, shots: [...snapshot.shots!, revised], commands: [{ command_id: command.id, operation: 'update_shot_plan', status: 'completed', result: { shot_id: revised.id } }] }} />);
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Shared shot plan' })).getByRole('heading', { name: 'Revised blocking' })).toBeVisible());
    expect(onCommand).toHaveBeenCalledTimes(1);
    expect(document.querySelectorAll('canvas')).toHaveLength(1);
  });
});
