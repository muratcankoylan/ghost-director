import { X } from 'lucide-react';
import type { Artifact, Snapshot } from '../api/contracts';
import { displayText } from './modelPresentation';
import './reference-review.css';

/** Inspect the exact location pin alongside the existing world, without a second canvas. */
export function LocationComparison({ snapshot, entityId, onOpen, onClose, onDesign }: {
  snapshot: Snapshot; entityId: string;
  onOpen: (artifact: Artifact) => void; onClose: () => void;
  onDesign: (tool: 'scene' | 'materials') => void;
}) {
  const entity = snapshot.scene?.entities.find(item => item.id === entityId && item.kind === 'room');
  const pin = entity?.state?.reference_pin;
  const asset = snapshot.reference_assets?.find(item => item.id === pin?.asset_id);
  const version = snapshot.reference_versions?.find(item => item.id === pin?.revision_id && item.asset_id === pin?.asset_id);
  const decision = snapshot.reference_decisions?.filter(item => item.asset_id === pin?.asset_id && item.revision_id === pin?.revision_id).at(-1);
  const refs = version?.references?.filter((item: Record<string, any>) => item.state === pin?.state) ?? [];
  const room = snapshot.scene?.room;
  return <aside className="reconstruction-review" aria-label="Location reference comparison">
    <header><div><span className="eyebrow">Location fidelity</span><h3>Compare the authored set</h3></div><button className="icon-button" aria-label="Close location comparison" onClick={onClose}><X size={15} /></button></header>
    <p>{displayText(asset?.name ?? 'Location reference')}</p>
    <p className="inspector-subtitle">Inspect the navigable world beside its pinned master photograph. The room is authored Blender architecture, adapted using this image as guidance.</p>
    {!entity || !version || decision?.decision !== 'approved' ? <p role="alert">This room has no currently approved location binding. Return to References to inspect its approval.</p> : <>
      <div className="reconstruction-reference-images">{refs.map((reference: Record<string, any>) => {
        const image = snapshot.artifacts?.find(item => item.id === reference.artifact_id);
        return image ? <button key={image.id} onClick={() => onOpen(image)}><img src={image.url} alt={`Approved location ${reference.slot} · ${asset?.name}`} /><span>{reference.slot} · approved v{version.version}</span></button> : <p role="alert" key={reference.slot}>Pinned image unavailable.</p>;
      })}</div>
      <p className="inspector-subtitle">Reference revision: {pin.revision_id}<br />Current scene: {snapshot.scene?.id}</p>
      <p className="reconstruction-review-record">{displayText(entity.state?.reference_binding_note ?? 'Reference-guided architecture; visual correspondence has not been certified.')}</p>
    </>}
    {room && <p>Authored dimensions: {room.width} × {room.depth} × {room.height} m.<br />{room.design?.windows?.length ?? 0} window openings. Dimensions are chosen for planning, not inferred from the image.</p>}
    <p className="inspector-subtitle">Whole-room image-to-3D is not connected. Compare window placement, proportions, finishes and lighting; use the design controls to correct differences. Changing the set does not alter the approved photograph sent to video generation.</p>
    <div className="reconstruction-review-actions"><button className="button" onClick={() => onDesign('scene')}>Edit architecture</button><button className="button" onClick={() => onDesign('materials')}>Edit finishes</button></div>
  </aside>;
}
