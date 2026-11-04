// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LocationComparison } from '../../src/terminal/LocationComparison';
import { ReferenceManager } from '../../src/terminal/ReferenceManager';
import { terminalFixture } from './fixtures';
import type { Entity } from '../../src/api/contracts';

afterEach(cleanup);
function fixture() {
  const snapshot = terminalFixture();
  const room: Entity = { id: 'room-new', name: 'Authored studio', kind: 'room', dimensions: [5, 6, 3], position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1], visible: true, state: {} };
  snapshot.scene!.entities.push(room);
  room.state.reference_pin = { asset_id: 'LOC-new', revision_id: 'ref-master', state: 'default' };
  room.state.reference_binding_note = 'Window recess and plaster texture remain approximate.';
  snapshot.reference_assets = [{ id: 'LOC-new', category: 'location', name: 'Sunroom', revision_id: 'ref-master', approved_revision_id: 'ref-master', approval_status: 'approved', version: 1 }];
  snapshot.reference_versions = [{ id: 'ref-master', asset_id: 'LOC-new', version: 1, references: [{ slot: 'wide', state: 'default', artifact_id: 'master', width: 1536, height: 1024 }] }];
  snapshot.reference_decisions = [{ asset_id: 'LOC-new', revision_id: 'ref-master', decision: 'approved' }];
  snapshot.artifacts.push({ id: 'master', kind: 'image', mime_type: 'image/png', url: '/new-master.png', sha256: 'a'.repeat(64), bytes: 42, relative_path: 'new-master.png' });
  return { snapshot, room };
}

it('shows the pinned approved location and actual adaptation notes, without certifying reconstruction', () => {
  const { snapshot, room } = fixture(); const onDesign = vi.fn(); const onOpen = vi.fn();
  render(<LocationComparison snapshot={snapshot} entityId={room.id} onOpen={onOpen} onClose={vi.fn()} onDesign={onDesign} />);
  expect(screen.getByRole('img', { name: /Approved location wide/ })).toHaveAttribute('src', '/new-master.png');
  expect(screen.getByText('Window recess and plaster texture remain approximate.')).toBeVisible();
  expect(screen.getByText(/Whole-room image-to-3D is not connected/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Edit architecture' }));
  expect(onDesign).toHaveBeenCalledWith('scene');
  fireEvent.click(screen.getByRole('img', { name: /Approved location wide/ }));
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'master' }));
});

it('does not present a revoked reference as approved', () => {
  const { snapshot, room } = fixture();
  snapshot.reference_decisions!.push({ asset_id: 'LOC-new', revision_id: 'ref-master', decision: 'rejected' });
  render(<LocationComparison snapshot={snapshot} entityId={room.id} onOpen={vi.fn()} onClose={vi.fn()} onDesign={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('no currently approved location binding');
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('opens comparison only for the exact bound location version', () => {
  const { snapshot, room } = fixture(); const onCompareLocation = vi.fn();
  const props = { snapshot, readOnly: false, busy: null, onCommand: vi.fn(), onOpen: vi.fn(), onCompareLocation };
  const view = render(<ReferenceManager {...props} />);
  fireEvent.click(screen.getByRole('button', { name: /Locations/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Compare location in world' }));
  expect(onCompareLocation).toHaveBeenCalledWith(room.id);
  room.state.reference_pin.revision_id = 'another-version';
  view.rerender(<ReferenceManager {...props} snapshot={{ ...snapshot }} />);
  expect(screen.queryByRole('button', { name: 'Compare location in world' })).not.toBeInTheDocument();
});
