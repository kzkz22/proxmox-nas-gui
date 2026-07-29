/** Hand-off between two pages that must not import each other: the pool
 *  editor pre-fills the new-share form with the pool's mountpoint.
 *
 *  The key lives here rather than being spelled out at both ends, because a
 *  typo would produce no error - the field would just come up empty. */
const PREFILL_PATH_KEY = "pnas_prefill_path";

export function setPrefillPath(path) {
  sessionStorage.setItem(PREFILL_PATH_KEY, path);
}

/** Reads and clears, so a pre-fill applies exactly once. */
export function takePrefillPath() {
  const path = sessionStorage.getItem(PREFILL_PATH_KEY) || "";
  if (path) sessionStorage.removeItem(PREFILL_PATH_KEY);
  return path;
}
