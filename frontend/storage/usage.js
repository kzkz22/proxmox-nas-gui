import { humanSize } from "../core/dom.js";

/** Unraid-style capacity bar, shown per pool and per branch. Only the storage
 *  pages report free space, so this lives here rather than in core. */
export function usageBar(u) {
  if (!u || !u.total) return "";
  const pct = Math.round((u.used / u.total) * 100);
  const cls = pct >= 90 ? "crit" : pct >= 75 ? "warn" : "";
  return `<div class="usage-bar"><div class="usage-fill ${cls}" style="width:${pct}%"></div></div>
    <div class="usage-label">${humanSize(u.used)} / ${humanSize(u.total)} (${pct}%)</div>`;
}
