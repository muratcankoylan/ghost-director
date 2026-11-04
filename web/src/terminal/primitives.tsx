import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { AlertCircle, Check, Circle, Clock3, LoaderCircle } from 'lucide-react';

export function shortId(value?: string | null, length = 8): string {
  if (!value) return '—';
  return value.length > length + 4 ? `${value.slice(0, length)}…` : value;
}

export function humanize(value: string): string {
  return value.replace(/[_\.]/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase());
}

export function money(value: number | null | undefined): string {
  return value == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value);
}

export function localTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  const lower = status.toLowerCase();
  const tone = /fail|reject|error/.test(lower) ? 'bad' : /complete|accept|approved|success|succeed|pass|ready|consumed/.test(lower) ? 'good' : /unknown|conflict|blocked|unavailable/.test(lower) ? 'warn' : /running|submitted|pending|queued|started/.test(lower) ? 'active' : 'neutral';
  const Icon = tone === 'bad' || tone === 'warn' ? AlertCircle : tone === 'good' ? Check : tone === 'active' ? Clock3 : Circle;
  return <span className={`status-badge status-${tone} ${compact ? 'compact' : ''}`}><Icon size={12} aria-hidden="true" /><span>{humanize(status)}</span></span>;
}

export function isTerminalJobStatus(status: string): boolean {
  return ['succeeded', 'completed', 'failed', 'cancelled', 'canceled'].includes(status);
}

export function IconButton({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return <button type="button" className={`icon-button ${className}`} aria-label={label} title={label} {...props}>{children}</button>;
}

export function EmptyState({ icon, title, detail, compact = false }: { icon?: ReactNode; title: string; detail?: string; compact?: boolean }) {
  return <div className={`empty-state ${compact ? 'empty-compact' : ''}`}>{icon && <span className="empty-icon">{icon}</span>}<strong>{title}</strong>{detail && <p>{detail}</p>}</div>;
}

export function SectionTitle({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return <div className="section-title"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>{action}</div>;
}

export function Working({ label }: { label: string }) {
  return <span className="working"><LoaderCircle size={13} aria-hidden="true" />{label}</span>;
}
