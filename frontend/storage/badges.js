import { S } from "../core/api.js";
import { esc } from "../core/dom.js";

/** Marks a directory that is a pool mountpoint in the directory browser.
 *
 *  Installed into the browser by main.js rather than imported by the pages
 *  that open it, so the Samba side keeps showing the hint when picking a
 *  share path without importing anything from storage. */
export function poolBadge(entry) {
  for (const [name, pool] of Object.entries((S && S.pools) || {})) {
    if (pool.mountpoint === entry.path) {
      return `<span class="ds">POOL: ${esc(name)}</span>`;
    }
  }
  return "";
}
