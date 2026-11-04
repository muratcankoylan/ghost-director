import { useEffect, useState } from 'react';
import { ArrowUpRight, FileText, Film, Image as ImageIcon } from 'lucide-react';
import type { Artifact } from '../api/contracts';
import { Dialog } from './Dialog';
import { displayText } from './modelPresentation';
import { EmptyState, shortId, Working } from './primitives';

export function artifactContentUrl(artifact: Artifact): string {
  return artifact.url;
}

export function isExportedDerivative(artifact: Artifact): boolean {
  const transformation = artifact.metadata?.export_transformation;
  return typeof transformation === 'string' && transformation !== 'byte-identical' && transformation.length > 0;
}

export function useArtifactText(artifact: Artifact | undefined) {
  const [state, setState] = useState<{ text: string; loading: boolean; error: string | null }>({ text: '', loading: false, error: null });
  const url = artifact?.url;
  const isText = artifact && (artifact.mime_type.startsWith('text/') || artifact.mime_type.includes('json') || ['prompt', 'response', 'context', 'scene'].includes(artifact.kind));
  useEffect(() => {
    if (!url || !isText) { setState({ text: '', loading: false, error: null }); return; }
    const controller = new AbortController();
    setState({ text: '', loading: true, error: null });
    fetch(url, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(`Artifact unavailable (${response.status})`);
      const content = await response.text();
      if (content.length > 2_000_000) throw new Error('Artifact exceeds the 2 MB inspector limit.');
      let text = content;
      try { text = JSON.stringify(JSON.parse(content), null, 2); } catch { /* Plain text is a valid prompt artifact. */ }
      if (!controller.signal.aborted) setState({ text, loading: false, error: null });
    }).catch((error: unknown) => { if (!controller.signal.aborted) setState({ text: '', loading: false, error: error instanceof Error ? error.message : 'Artifact unavailable' }); });
    return () => controller.abort();
  }, [url, isText]);
  return state;
}

export function ArtifactRows({ artifacts, onOpen, empty = 'No artifacts recorded.', labels = {} }: { artifacts: Artifact[]; onOpen: (artifact: Artifact) => void; empty?: string; labels?: Record<string, string> }) {
  if (!artifacts.length) return <p>{empty}</p>;
  return <div className="artifact-list">{artifacts.map((artifact, index) => <button type="button" className="artifact-row" key={artifact.id} onClick={() => onOpen(artifact)}>{artifact.mime_type.startsWith('image/') ? <img className="artifact-thumbnail" src={artifact.url} loading="lazy" alt={displayText(artifact.name)} /> : artifact.mime_type.startsWith('video/') ? <Film size={15} /> : <FileText size={15} />}<span className="artifact-row-copy">{labels[artifact.id] && <span className="artifact-role">{displayText(labels[artifact.id])}</span>}<span className="artifact-name">{index + 1}. {displayText(artifact.name)}</span><span className="artifact-hash">{artifact.kind} · {shortId(artifact.sha256, 10)}</span></span><ArrowUpRight size={12} /></button>)}</div>;
}

export function ArtifactDialog({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  const { text, loading, error } = useArtifactText(artifact);
  const preview = displayText(text);
  return <Dialog title={displayText(artifact.name)} onClose={onClose} footer={<><span className="inspector-id">{isExportedDerivative(artifact) ? 'Exported derivative hash' : 'Original SHA-256'} {shortId(artifact.sha256, 16)}</span><a className="button" href={artifact.url} target="_blank" rel="noreferrer">Open original <ArrowUpRight size={12} /></a></>}>
    {text && preview !== text && <p className="inspector-subtitle">Display preview · open original for the recorded payload.</p>}
    {artifact.mime_type.startsWith('image/') ? <img src={artifact.url} alt={displayText(artifact.name)} /> : artifact.mime_type.startsWith('video/') ? <video src={artifact.url} controls autoPlay={false} preload="metadata" /> : loading ? <Working label="Loading recorded artifact" /> : error ? <p className="artifact-error">{displayText(error)}</p> : text ? <pre>{preview}</pre> : <EmptyState icon={<ImageIcon size={24} />} title="Artifact available" detail="Open the original file to inspect this format." />}
  </Dialog>;
}
