import type { ReferencePin, Snapshot } from '../api/contracts';
import { displayText } from './modelPresentation';

export function ApprovedReferencePicker({ snapshot, pins, disabled, onChange }: {
  snapshot: Snapshot; pins: ReferencePin[]; disabled: boolean; onChange: (pins: ReferencePin[]) => void;
}) {
  const choices = (snapshot.reference_assets ?? []).flatMap(asset => {
    const versions = (snapshot.reference_versions ?? []).filter(version => version.asset_id === asset.id && (
      (snapshot.reference_decisions ?? []).filter(decision => decision.revision_id === version.id).at(-1)?.decision === 'approved'
      || version.id === asset.approved_revision_id
    ));
    const options = versions.flatMap(version => {
      const required = snapshot.reference_templates?.[asset.category]?.required_slots ?? (asset.category === 'character' ? ['front', 'back', 'face'] : asset.category === 'location' ? ['wide'] : ['primary']);
      return [...new Set<string>((version.references ?? []).map((ref: Record<string, any>) => ref.state ?? 'default'))]
        .filter(state => required.every((slot: string) => version.references.some((ref: Record<string, any>) => ref.state === state && ref.slot === slot)))
        .map(state => ({ asset_id: asset.id, revision_id: version.id, state, label: `Version ${version.version} · ${state}` }));
    });
    const selected = pins.find(pin => pin.asset_id === asset.id);
    // Old snapshots may omit version detail. Retain the explicitly pinned ID;
    // the server checks its immutable approval before compilation.
    if (selected && !options.some(option => option.revision_id === selected.revision_id && option.state === selected.state)) options.push({ ...selected, label: `Pinned ${selected.revision_id} · ${selected.state}` });
    if (!options.length && asset.approved_revision_id) options.push({ asset_id: asset.id, revision_id: asset.approved_revision_id, state: 'default', label: 'Approved version · default' });
    return options.length ? [{ asset, options, selected }] : [];
  });
  return <details open><summary>Approved visual identities · {pins.length}</summary>{choices.map(({ asset, options, selected }) => <div className="plan-identity" key={asset.id}>
    <label className="plan-reference"><input type="checkbox" checked={Boolean(selected)} disabled={disabled} onChange={event => {
      const initial = options.find(option => option.revision_id === asset.approved_revision_id) ?? options.at(-1)!;
      onChange(event.target.checked ? [...pins, { asset_id: initial.asset_id, revision_id: initial.revision_id, state: initial.state }] : pins.filter(pin => pin.asset_id !== asset.id));
    }} />{displayText(asset.name)}<small>{asset.category}</small></label>
    {selected && <label>Version and state for {displayText(asset.name)}<select disabled={disabled} value={JSON.stringify([selected.revision_id, selected.state])} onChange={event => {
      const [revision_id, state] = JSON.parse(event.target.value) as [string, string];
      onChange(pins.map(pin => pin.asset_id === asset.id ? { ...pin, revision_id, state } : pin));
    }}>{options.map(option => <option key={`${option.revision_id}:${option.state}`} value={JSON.stringify([option.revision_id, option.state])}>{option.label}</option>)}</select></label>}
  </div>)}{!choices.length && <p className="plan-note">Approve identity images in References to enable reference-based generation.</p>}<p className="plan-note">Saved shots keep their exact approved version and appearance state. Select an update explicitly.</p></details>;
}
