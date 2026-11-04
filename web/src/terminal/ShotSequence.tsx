import { Plus } from 'lucide-react';
import type { Snapshot } from '../api/contracts';
import { humanize } from './primitives';

export function ShotSequence({ snapshot, activeShotId, onShot, onCreate }: { snapshot: Snapshot; activeShotId?: string; onShot: (id: string) => void; onCreate: () => void }) {
  return <section className="shot-sequence" aria-label="Shots"><div className="sequence-label"><strong>SHOT SEQUENCE</strong><span>{snapshot.shots?.length ?? 0} shots · {snapshot.takes?.length ?? 0} takes</span></div><div className="shot-cards">{snapshot.shots?.map((shot, index) => {
    const takes = snapshot.takes?.filter((take) => take.shot_id === shot.id) ?? [];
    const take = [...takes].reverse().find((item) => item.status === 'accepted') ?? takes.at(-1);
    const poster = snapshot.artifacts?.find((artifact) => artifact.id === take?.poster_id);
    const number = String(index + 1).padStart(2, '0');
    return <button key={shot.id} aria-label={`Shot ${number}: ${shot.name}, ${shot.duration} seconds, ${humanize(shot.status)}`} aria-pressed={activeShotId === shot.id} className={`shot-card ${activeShotId === shot.id ? 'selected' : ''}`} onClick={() => onShot(shot.id)}><span className="shot-card-number">{poster ? <img src={poster.url} alt="" /> : number}</span><span className="shot-description"><strong className="shot-name">{shot.name}</strong><span className="shot-status">{shot.duration}s · {take?.status ?? humanize(shot.status)} · {takes.length} takes</span></span></button>;
  })}</div><button className="icon-button" aria-label="Create a new shot" title="Write a new shot brief" onClick={onCreate}><Plus size={16} /></button></section>;
}
