import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Film, Image as ImageIcon, Maximize2 } from 'lucide-react';
import type { Artifact, Snapshot, Take } from '../api/contracts';
import { EmptyState, humanize, isTerminalJobStatus, shortId, Working } from './primitives';
import { useArtifactText } from './Artifacts';
import { configuredVideoModel, isVideoJob, videoModelLabel } from './records';
import { displayText } from './modelPresentation';

export function OutputShelf({ snapshot, activeShotId, take, onInspectTake, onOpen, seek, pausePlayback = false, focusedTaskId }: { snapshot: Snapshot; activeShotId: string | null; take?: Take; onShot: (id: string) => void; onInspectTake: (take: Take) => void; onOpen: (artifact: Artifact) => void; seek?: { takeId: string; timeMs: number; nonce: number } | null; pausePlayback?: boolean; focusedTaskId?: string }) {
  const artifacts = snapshot.artifacts ?? [];
  const media = artifacts.find((artifact) => artifact.id === take?.artifact_id);
  const poster = artifacts.find((artifact) => artifact.id === take?.poster_id);
  const metaArtifact = [...artifacts].reverse().find((artifact) => {
    const provider = String(artifact.metadata?.provider ?? '').toLowerCase();
    const model = String(artifact.metadata?.model ?? artifact.metadata?.requested_model ?? '').toLowerCase();
    return (provider === 'meta' || model.startsWith('muse-') || model.startsWith('sam-')) && ['image', 'response', 'evidence'].includes(artifact.kind);
  });
  const metaTask = [...(snapshot.tasks ?? [])].reverse().find((task) => String(task.model ?? '').startsWith('muse-') || String(task.model ?? '').startsWith('sam-'));
  const focusedTask = snapshot.tasks?.find((task) => task.id === focusedTaskId && (String(task.model ?? '').startsWith('muse-') || String(task.model ?? '').startsWith('sam-')));
  const focusedOutput = artifacts.find((artifact) => (focusedTask?.result?.output_refs ?? focusedTask?.output_refs)?.includes(artifact.id));
  const metaOutput = focusedOutput ?? metaArtifact ?? artifacts.find((artifact) => (metaTask?.result?.output_refs ?? metaTask?.output_refs)?.includes(artifact.id));
  const text = useArtifactText(metaOutput);
  const video = useRef<HTMLVideoElement>(null);
  const [mediaError, setMediaError] = useState(false);
  useEffect(() => { setMediaError(false); }, [media?.id]);
  useEffect(() => { if (pausePlayback && video.current && !video.current.paused) video.current.pause(); }, [pausePlayback]);
  useEffect(() => { if (seek && seek.takeId === take?.id && video.current) video.current.currentTime = seek.timeMs / 1000; }, [seek, take?.id]);
  const liveJob = [...(snapshot.jobs ?? [])].reverse().find((job) => (job.shot_id ?? snapshot.runs?.find((run) => run.id === job.run_id)?.shot_id) === activeShotId && isVideoJob(job, snapshot) && !job.dispatch_retired && !isTerminalJobStatus(job.status));
  const takePackage = take ? snapshot.packages?.find((item) => item.id === take.package_id) : undefined;
  const takeJob = take ? snapshot.jobs?.find((item) => item.id === take.job_id) : undefined;
  const recordedModel = takePackage?.model ?? takeJob?.model ?? media?.metadata?.model;
  const modelLabel = videoModelLabel(snapshot, take ? recordedModel : liveJob?.model ?? configuredVideoModel(snapshot));
  return <section className="output-shelf" aria-label="Production outputs">
    <div className="media-panel"><div className="panel-label"><Film size={12} /><span className="media-model-label" title={modelLabel}>{modelLabel.toUpperCase()} TAKE</span><span className="panel-meta">{take ? shortId(take.id, 7) : 'NO TAKE YET'}</span></div><div className="media-body">
      {mediaError ? <EmptyState compact title="Recorded media is unavailable" detail="The artifact could not be decoded or loaded. Inspect its receipt to investigate." /> : media?.mime_type.startsWith('video/') ? <><video key={media.id} ref={video} src={media.url} poster={poster?.url} controls preload="metadata" onError={() => setMediaError(true)} aria-label={`${modelLabel} take ${take?.id}`} /><button className="icon-button media-expand" aria-label="Inspect selected take" onClick={() => take && onInspectTake(take)}><ArrowUpRight size={12} /></button></> : poster ? <button className="preview-button" aria-label="Inspect selected take" onClick={() => take && onInspectTake(take)}><img src={poster.url} alt={displayText(poster.name)} /></button> : <EmptyState compact icon={<Film size={23} />} title={liveJob ? humanize(liveJob.status) : 'Your next take starts here'} detail={liveJob ? liveJob.status === 'submission_unknown' ? 'Submission outcome is unknown. The full reservation remains held.' : 'Remote work is pending. The set stays interactive.' : 'Select a shot, review its package, then generate.'} />}
    </div></div>
    <div className="meta-panel"><div className="panel-label"><ImageIcon size={12} /> META OUTPUT <span className="panel-meta">{metaOutput ? metaOutput.kind.toUpperCase() : 'WAITING'}</span></div>
      {metaOutput?.mime_type.startsWith('image/') ? <div className="media-body"><img className="meta-image" src={metaOutput.url} alt={displayText(metaOutput.name)} /><button className="icon-button media-expand" aria-label="Open Meta output" onClick={() => onOpen(metaOutput)}><Maximize2 size={12} /></button></div> : metaOutput ? <div className="meta-result">{text.loading ? <Working label="Loading output" /> : text.error ? <span className="artifact-error">{text.error}</span> : <pre>{displayText(text.text).slice(0, 700)}</pre>}<button className="text-button output-link" onClick={() => onOpen(metaOutput)}>Inspect complete result <ArrowUpRight size={11} /></button></div> : <EmptyState compact title="No Meta output recorded" detail="Image, planning, and review results appear here when returned." />}
    </div>

  </section>;
}
