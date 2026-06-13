import type { ClassValue } from "clsx"
import { clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Human-readable byte size. Accepts bytes; `mib` flag treats input as MiB. */
export function formatBytes(value: number | null | undefined, mib = false): string {
  if (value == null || Number.isNaN(value)) return '—'
  let bytes = mib ? value * 1024 * 1024 : value
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024
    i++
  }
  return `${bytes.toFixed(bytes < 10 && i > 0 ? 1 : 0)} ${units[i]}`
}

/** MiB → GB string (GPU memory is reported in MiB by the backend). */
export function mibToGb(mib: number | null | undefined): string {
  if (mib == null) return '—'
  return `${(mib / 1024).toFixed(1)} GB`
}

/** Compact latency in ms, switching to seconds past 1000ms. */
export function formatLatency(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

/** Thousands-separated integer, or compact (1.2k / 3.4M) when `compact`. */
export function formatNumber(n: number | null | undefined, compact = false): string {
  if (n == null || Number.isNaN(n)) return '—'
  if (compact) {
    return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
  }
  return new Intl.NumberFormat('en').format(n)
}

/** Percentage with one decimal, e.g. 38.6%. */
export function formatPercent(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `${n.toFixed(1)}%`
}

/** Relative "time ago" from a Unix-seconds timestamp. */
export function timeAgo(unixSeconds: number | null | undefined): string {
  if (unixSeconds == null) return '—'
  const diff = Date.now() / 1000 - unixSeconds
  if (diff < 5) return 'just now'
  if (diff < 60) return `${Math.floor(diff)}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

/** Clock time (HH:MM:SS) from Unix seconds. */
export function formatTime(unixSeconds: number | null | undefined): string {
  if (unixSeconds == null) return '—'
  return new Date(unixSeconds * 1000).toLocaleTimeString('en-GB')
}

/** Duration between two Unix-seconds stamps as e.g. "1m 34s". */
export function formatDuration(from?: number | null, to?: number | null): string {
  if (from == null) return '—'
  const end = to ?? Date.now() / 1000
  const secs = Math.max(0, end - from)
  if (secs < 60) return `${secs.toFixed(0)}s`
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}m ${s}s`
}
