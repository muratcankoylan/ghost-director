// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ReconstructionReview } from '../../src/terminal/ReconstructionReview';
import { ReferenceManager } from '../../src/terminal/ReferenceManager';
import { terminalFixture } from './fixtures';
import type { Snapshot } from '../../src/api/contracts';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function fixture(): Snapshot {
  return { ...terminalFixture(), reference_assets: [{ id: 'PROP-bottle', category: 'prop', name: 'Amber bottle', revision_id: 'ref-one', approved_revision_id: 'ref-one', approval_status: 'approved', version: 1 }],
    reference_versions: [{ id: 'ref-one', asset_id: 'PROP-bottle', version: 1, references: [{ slot: 'primary', state: 'default', artifact_id: 'photo', original_artifact_id: 'photo', width: 800, height: 800, provenance: { source: 'upload' } }] }],
    reference_decisions: [{ asset_id: 'PROP-bottle', revision_id: 'ref-one', decision: 'approved' }],
    artifacts: [{ id: 'photo', kind: 'image', mime_type: 'image/png', url: '/bottle.png', sha256: 'a'.repeat(64), bytes: 20, relative_path: 'bottle.png' }],
    reconstructions: [{ id: 'recon', asset_id: 'PROP-bottle', artifact_id: 'mesh', status: 'ready_for_review', reference_pin: { asset_id: 'PROP-bottle', revision_id: 'ref-one', state: 'default' }, height_m: 0.26 }], reconstruction_reviews: [] };
}

it('shows approved photographs beside the mesh review and records limitations against its exact identity', async () => {
  const onCommand = vi.fn().mockResolvedValue(true);
  render(<ReconstructionReview reconstructionId="recon" snapshot={fixture()} disabled={false} onCommand={onCommand} onInspect={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('img', { name: /Approved primary photograph/ })).toHaveAttribute('src', '/bottle.png');
  expect(screen.getByRole('button', { name: 'Accept for planning' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Compare materials'), { target: { value: 'mismatch' } });
  fireEvent.change(screen.getByLabelText('Asset comparison notes'), { target: { value: 'The reconstructed glass is opaque. Correct transparency before placement.' } });
  expect(screen.getByRole('button', { name: 'Accept for planning' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Request asset changes' }));
  await waitFor(() => expect(onCommand).toHaveBeenCalledWith('review_reconstruction', expect.objectContaining({ reconstruction_id: 'recon', decision: 'changes_requested', criteria: expect.objectContaining({ materials: 'mismatch' }) })));
});

it('keeps review-only and replay interactions nonmutating', () => {
  render(<ReconstructionReview reconstructionId="recon" snapshot={fixture()} disabled onCommand={vi.fn()} onInspect={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByLabelText('Compare geometry')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Request asset changes' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Accept for planning' })).toBeDisabled();
});

it('requires a recorded accepted comparison before enabling new placement', () => {
  const snapshot = fixture(); const props = { snapshot, readOnly: false, busy: null, onCommand: vi.fn(), onOpen: vi.fn(), onPreviewReconstruction: vi.fn() };
  const view = render(<ReferenceManager {...props} />);
  fireEvent.click(screen.getByRole('button', { name: /Props/ }));
  expect(screen.getByRole('button', { name: 'Place in set' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Generate reference sheet' })).toBeDisabled();
  view.rerender(<ReferenceManager {...props} snapshot={{ ...snapshot, reconstruction_reviews: [{ id: 'r1', reconstruction_id: 'recon', artifact_id: 'mesh', decision: 'accepted', notes: 'Unknown underside documented.' }] }} />);
  expect(screen.getByRole('button', { name: 'Place in set' })).toBeEnabled();
  view.rerender(<ReferenceManager {...props} snapshot={{ ...snapshot, reconstruction_reviews: [{ id: 'r1', reconstruction_id: 'recon', artifact_id: 'mesh', decision: 'accepted' }, { id: 'r2', reconstruction_id: 'recon', artifact_id: 'mesh', decision: 'changes_requested', notes: 'Fix glass.' }] }} />);
  expect(screen.getByRole('button', { name: 'Place in set' })).toBeDisabled();
});

it('uploads a named state under the same prop identity rather than creating another asset', async () => {
  const request = vi.fn().mockResolvedValue({ ok: true }); vi.stubGlobal('fetch', request);
  const onCommand = vi.fn(); render(<ReferenceManager snapshot={fixture()} readOnly={false} busy={null} onCommand={onCommand} onOpen={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /Props/ }));
  fireEvent.change(screen.getByLabelText('New reference state'), { target: { value: 'partially-empty' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add visual state' }));
  fireEvent.click(screen.getByRole('button', { name: 'Upload image' }));
  const file = new File(['png'], 'empty.png', { type: 'image/png' });
  fireEvent.change(screen.getByLabelText('Upload reference image'), { target: { files: [file] } });
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining('PROP-bottle/images?expected_revision_id=ref-one&slot=primary&state=partially-empty'), expect.objectContaining({ body: file })));
  expect(onCommand).not.toHaveBeenCalled();
});

it('allows one master location reference and makes complementary views optional', () => {
  const snapshot = fixture(); snapshot.reference_assets = [{ id: 'LOC-room', category: 'location', name: 'Room', revision_id: 'room-v1', version: 1, approval_status: 'draft', missing_slots: ['opposite'] }];
  snapshot.reference_versions = [{ id: 'room-v1', asset_id: 'LOC-room', version: 1, references: [{ slot: 'wide', state: 'default', artifact_id: 'photo', provenance: { source: 'upload' } }] }]; snapshot.reference_decisions = [];
  render(<ReferenceManager snapshot={snapshot} readOnly={false} busy={null} onCommand={vi.fn()} onOpen={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: /Locations/ }));
  expect(screen.getByRole('button', { name: 'Approve references' })).toBeEnabled();
  expect(screen.getByText('Master location reference')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Additional reference view'), { target: { value: 'opposite' } });
  expect(screen.getByText('Opposite angle · optional')).toBeVisible();
});
