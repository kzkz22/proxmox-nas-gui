import { api, clearState, S, UNAUTHORIZED_EVENT } from "./core/api.js";
import { setEntryDecorator } from "./core/browser.js";
import { loadLang, toggleLang } from "./core/i18n.js";
import { showLogin } from "./core/login.js";
import { route, startApp } from "./router.js";
import { poolBadge } from "./storage/badges.js";

// Composition root: the only place that wires the two halves together. The
// directory browser lives in core and must not know about pools, so the pool
// marker is installed here instead of imported where the browser is opened.
setEntryDecorator(poolBadge);

window.addEventListener(UNAUTHORIZED_EVENT, showLogin);
window.addEventListener("hashchange", route);

document.getElementById("lang-toggle").addEventListener("click", async () => {
  await toggleLang();
  if (S) route(); else showLogin();
});

document.getElementById("logout").addEventListener("click", async () => {
  await api("/logout", { method: "POST" }).catch(() => {});
  clearState();
  showLogin();
});

(async function boot() {
  await loadLang();
  try {
    await api("/session");
    await startApp();
  } catch {
    /* showLogin already triggered by the unauthorized event */
  }
})();
