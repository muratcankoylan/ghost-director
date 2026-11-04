import { useEffect, useState } from 'react';
import type { Artifact, Command, Snapshot } from '../api/contracts';
import { displayText } from './modelPresentation';
import { money } from './primitives';

/** Review the compiled record itself, never the newest preview or asset version. */
export function SubmissionReview({ snapshot, pkg, disabled, onOpen, onCommand }: {
  snapshot: Snapshot; pkg: Record<string, any>; disabled: boolean;
  onOpen: (artifact: Artifact) => void;
  onCommand: (operation: Command['operation'], payload: Record<string, unknown>) => unknown;
}) {
  const [approved, setApproved] = useState(false);
  const [note, setNote] = useState('');
  useEffect(() => { setApproved(false); setNote(''); }, [pkg.id, pkg.review_sha256]);
  const artifact = (id: unknown) => snapshot.artifacts?.find(item => item.id === id);
  const references: string[] = pkg.reference_ids ?? [];
  const motion = artifact(pkg.motion_video_id);
  const prompt = artifact(pkg.prompt_ref);
  const receipt = artifact(pkg.motion_receipt_id);
  const estimate = pkg.pricing_estimate;
  const complete = references.length > 0 && references.every(id => artifact(id)?.mime_type.startsWith('image/'))
    && motion?.mime_type === 'video/mp4' && Boolean(pkg.prompt?.trim()) && /^[a-f0-9]{64}$/.test(pkg.review_sha256 ?? '')
    && Number.isFinite(estimate?.estimated_usd) && Number.isFinite(estimate?.reserve_usd);
  const submitted = Boolean(snapshot.takes?.some(take => take.package_id === pkg.id)
    || snapshot.jobs?.some(job => job.run_id === pkg.run_id && job.category === 'video'));
  return <section className="plan-evidence submission-review" aria-label="Complete production request">
    <strong>Review the exact production request</strong>
    <p>{pkg.duration}s · {pkg.resolution ?? 'Recorded resolution'} · {references.length} approved images + 1 clay motion clip</p>
    <p className="plan-note">The images define appearance. The clay video defines camera motion and blocking. No world screenshot replaces these images.</p>
    <div className="submission-images">{references.map((id, index) => {
      const image = artifact(id);
      const mapping = pkg.reference_mapping?.find((item: Record<string, any>) => item.artifact_id === id);
      const pin = pkg.reference_pins?.find((item: Record<string, any>) => item.asset_id === mapping?.asset_id && item.revision_id === mapping?.revision_id);
      return <div className="submission-image" key={`${id}-${index}`}>
        {image ? <button onClick={() => onOpen(image)} aria-label={`Inspect submitted image ${index + 1}`}><img src={image.url} alt={`@Image${index + 1} · ${displayText(mapping?.name ?? image.name)}`} /></button> : <p role="alert">Image unavailable: {id}</p>}
        <strong>@Image{index + 1} · {displayText(mapping?.name ?? image?.name ?? id)}</strong>
        <small>{mapping?.asset_id ?? 'Asset mapping unavailable'} · {mapping?.slot ?? 'Recorded image'} · {pin?.state ?? 'default'}</small>
        <small>Revision: {mapping?.revision_id ?? 'unavailable'}</small>
      </div>;
    })}</div>
    <strong>@Video1 · submitted Blender motion</strong>
    {motion ? <video controls preload="metadata" src={motion.url} aria-label="Submitted clay motion reference" /> : <p role="alert">Submitted clay motion is unavailable.</p>}
    {receipt && <button onClick={() => onOpen(receipt)}>Inspect submitted motion receipt</button>}
    <details open><summary>Exact compiled prompt</summary><pre className="submission-prompt">{pkg.prompt ?? 'Prompt not recorded in this package.'}</pre>{prompt && <button onClick={() => onOpen(prompt)}>Review exact prompt</button>}</details>
    {!!pkg.appearance_coverage_warnings?.length && <div className="plan-warning"><strong>Appearance remains unspecified</strong><ul>{pkg.appearance_coverage_warnings.map((warning: unknown, index: number) => {
      const finding = warning && typeof warning === 'object' ? warning as Record<string, unknown> : null;
      const description = finding && typeof finding.reason === 'string' ? `${String(finding.name ?? finding.entity_id ?? 'Set element')}: ${finding.reason}` : String(warning);
      return <li key={index}>{displayText(description)}</li>;
    })}</ul></div>}
    {!!pkg.limitations?.length && <ul className="plan-note">{pkg.limitations.map((limit: string) => <li key={limit}>{displayText(limit)}</li>)}</ul>}
    {estimate && <p className="submission-cost">Estimated video cost: <strong>{money(estimate.estimated_usd)}</strong> · reservation <strong>{money(estimate.reserve_usd)}</strong>. The automatic review uses additional Meta inference; all calls remain subject to the production budget.</p>}
    {!complete && <p className="plan-warning" role="alert">Prepare this submission again to verify the complete media request and cost before approval.</p>}
    {submitted ? <p className="plan-note">This package was already dispatched. Inspect its job or take before creating another reviewed package.</p> : <>
      <label>Generation approval note<textarea maxLength={2000} value={note} disabled={disabled || !complete} onChange={event => setNote(event.target.value)} placeholder="Record the framing, references and remaining limitations you approve." /></label>
      <label className="plan-reference"><input type="checkbox" checked={approved} disabled={disabled || !complete} onChange={event => setApproved(event.target.checked)} />I approve this exact request and authorize its API cost.</label>
      <p className="plan-note">Recorded as local-operator against this immutable request. A changed package requires a new approval.</p>
    </>}
    <button className="primary-button" disabled={disabled || !complete || submitted || !approved || !note.trim()} onClick={() => onCommand('generate_package', { package_id: pkg.id, approval: { accepted: true, review_sha256: pkg.review_sha256, actor: 'local-operator', note: note.trim() } })}>Generate movie take</button>
  </section>;
}
