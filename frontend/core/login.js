import { api, ApiError } from "./api.js";
import { $, esc, toast, view } from "./dom.js";
import { t } from "./i18n.js";
import { startApp } from "../router.js";

export function showLogin() {
  document.getElementById("topbar").hidden = true;
  view().innerHTML = `
    <div class="login-wrap">
      <form class="panel login-box" id="login-form">
        <span class="brand-mark">▣</span>
        <h1>${esc(t("login.title"))}</h1>
        <div class="field"><label>${esc(t("login.user"))}</label>
          <input type="text" name="username" value="root" autocomplete="username" required></div>
        <div class="field"><label>${esc(t("login.password"))}</label>
          <input type="password" name="password" autocomplete="current-password" required autofocus></div>
        <button class="primary" style="width:100%">${esc(t("login.submit"))}</button>
      </form>
    </div>`;
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    try {
      await api("/login", { method: "POST", body: { username: f.username.value, password: f.password.value } });
      await startApp();
    } catch (e) {
      // A lockout has to say so, otherwise the correct password looks wrong
      // and the natural reaction is to keep trying, which extends the wait.
      const wait = e instanceof ApiError && e.status === 429 ? e.retryAfter : 0;
      toast(wait ? t("login.locked", { seconds: wait }) : t("login.error"), "err", 8000);
    }
  });
}
