import { S } from "../core/api.js";
import { esc } from "../core/dom.js";

/** Marks a directory the storage side already has plans for, in the directory
 *  browser.
 *
 *  Installed into the browser by main.js rather than imported by the pages
 *  that open it, so the Samba side keeps showing the hint when picking a
 *  share path without importing anything from storage.
 *
 *  Bind targets matter here as much as pool mountpoints: a directory that is
 *  only a mountpoint while its bind is up looks like an ordinary empty folder
 *  in the browser, and picking it as a share path without knowing that is how
 *  people end up sharing a hole. */
export function pathBadge(entry) {
  for (const [name, pool] of Object.entries((S && S.pools) || {})) {
    if (pool.mountpoint === entry.path) {
      return `<span class="ds">POOL: ${esc(name)}</span>`;
    }
  }
  for (const [name, bind] of Object.entries((S && S.bind_mounts) || {})) {
    if (bind.target === entry.path) {
      return `<span class="ds">BIND: ${esc(name)}</span>`;
    }
  }
  return "";
}
