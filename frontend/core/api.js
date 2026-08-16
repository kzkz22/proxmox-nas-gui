import { toast } from "./dom.js";

/** Carries the status alongside the message so a caller can tell the cases
 *  apart without re-reading the response. The login screen needs it: a
 *  rejected password and a lockout both arrive here as a failure, but only
 *  one of them has a wait to show. */
export class ApiError extends Error {
  constructor(message, status = 0, retryAfter = 0) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Raised on a 401 instead of calling showLogin() directly. The login screen
 *  restarts the app, which needs the API client, so a direct call would make
 *  api.js and login.js import each other. */
export const UNAUTHORIZED_EVENT = "pnas:unauthorized";

export async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (init.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(`/api${path}`, init);
  if (res.status === 401 && path !== "/login") {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    throw new ApiError("unauthorized", res.status);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof data.detail === "string" ? data.detail : res.statusText;
    throw new ApiError(msg, res.status, Number(res.headers.get("Retry-After")) || 0);
  }
  return data;
}

export async function guard(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.message !== "unauthorized") toast(e.message, "err", 6000);
    else if (!(e instanceof ApiError)) toast(String(e), "err", 6000);
    throw e;
  }
}

/** The last GET /api/state payload, shared by every page.
 *
 *  Importers get a live binding, so they always read the current value, but
 *  they cannot assign to it - both writes stay in this module. */
export let S = null;

export async function refreshState() {
  S = await api("/state");
  return S;
}

export function clearState() {
  S = null;
}
