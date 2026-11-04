import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import type { Artifact, Command, Snapshot } from '../api/contracts';
import { displayText } from './modelPresentation';
import './reference-review.css';

const CRITERIA = ['geometry', 'proportions', 'materials', 'colors', 'identity'] as const;
type Finding = 'match' | 'mismatch' | 'unknown';
type Criteria = Record<typeof CRITERIA[number], Finding>;
const EMPTY: Criteria = { geometry: 'unknown', proportions: 'unknown', materials: 'unknown', colors: 'unknown', identity: 'unknown' };

export function ReconstructionReview({ reconstructionId, snapshot, disabled, onCommand, onInspect, onClose }: {
  reconstructionId: string; snapshot: Snapshot; disabled: boolean;
  onCommand: (operation: Command['operation'], payload: Record<string, unknown>) => Promise<boolean>;
  onInspect: (artifact: Artifact) => void; onClose: () => void;
}) {
  const reconstruction = snapshot.reconstructions?.find((item) => item.id === reconstructionId);
  const pin = reconstruction?.reference_pin;
  const version = snapshot.reference_versions?.find((item) => item.id === pin?.revision_id);
  const asset = snapshot.reference_assets?.find((item) => item.id === pin?.asset_id);
  const latest = snapshot.reconstruction_reviews?.filter((item) => item.reconstruction_id === reconstructionId).at(-1);
  const [criteria, setCriteria] = useState<Criteria>(latest?.criteria ?? EMPTY);
  const [notes, setNotes] = useState(latest?.notes ?? '');
  useEffect(() => { setCriteria(latest?.criteria ?? EMPTY); setNotes(latest?.notes ?? ''); }, [reconstructionId, latest?.id]);
  const references = version?.references?.filter((item: Record<string, any>) => item.state === (pin?.state ?? 'default')) ?? [];
  const mismatched = Object.values(criteria).includes('mismatch');
  const canSubmit = !disabled && Boolean(reconstruction) && Boolean(notes.trim());
  async function save(decision: 'accepted' | 'changes_requested') {
    await onCommand('review_reconstruction', { reconstruction_id: reconstructionId, decision, criteria, notes: notes.trim() });
  }
  return <aside className="reconstruction-review" aria-label="Reference to 3D comparison">
    <header><div><span className="eyebrow">Visual fidelity</span><h3>Compare against references</h3></div><button className="icon-button" aria-label="Close asset comparison" onClick={onClose}><X size={15} /></button></header>
    <p>{displayText(asset?.name ?? 'Reconstructed asset')} · {pin?.state ?? 'default'}</p>
    <p className="inspector-subtitle">The world shows the reconstructed mesh. These approved photographs define its intended appearance. Orbit and inspect the mesh before recording differences.</p>
    <div className="reconstruction-reference-images">{references.map((reference: Record<string, any>) => {
      const artifact = snapshot.artifacts?.find((item) => item.id === reference.artifact_id);
      return artifact ? <button key={artifact.id} onClick={() => onInspect(artifact)}><img src={artifact.url} alt={`Approved ${reference.slot} photograph of ${asset?.name ?? 'asset'}`} /><span>{reference.slot} · approved v{version?.version}</span></button> : <p key={reference.slot} role="alert">Approved image unavailable</p>;
    })}</div>
    <p className="inspector-subtitle">Authored height {reconstruction?.height_m} m. Static mesh; unseen surfaces and transparent materials may differ from the photographs.</p>
    {latest && <p className="reconstruction-review-record">Last review: {latest.decision === 'accepted' ? 'Accepted for planning' : 'Changes requested'} · {displayText(latest.actor)}<br />{displayText(latest.notes)}</p>}
    <fieldset disabled={disabled}><legend>Compare this exact mesh</legend>{CRITERIA.map((key) => <label key={key}><span>{key.charAt(0).toUpperCase() + key.slice(1)}</span><select aria-label={`Compare ${key}`} value={criteria[key]} onChange={(event) => setCriteria((current) => ({ ...current, [key]: event.target.value as Finding }))}><option value="unknown">Not established</option><option value="match">Matches for planning</option><option value="mismatch">Needs correction</option></select></label>)}<label className="reconstruction-review-notes">Differences and limitations<textarea aria-label="Asset comparison notes" maxLength={4000} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Describe mismatches, unverified surfaces, or what is acceptable for this shot." /></label></fieldset>
    {mismatched && <p role="status">Resolve the recorded mismatches before accepting this asset for planning. Request changes to keep the differences visible.</p>}
    <div className="reconstruction-review-actions"><button className="button" disabled={!canSubmit} onClick={() => save('changes_requested')}>Request asset changes</button><button className="button primary" disabled={!canSubmit || mismatched} onClick={() => save('accepted')}>Accept for planning</button></div>
    <p className="inspector-subtitle">This local review makes no API call. Acceptance permits placement; it does not certify photorealism or change the approved images sent to video generation.</p>
  </aside>;
}
