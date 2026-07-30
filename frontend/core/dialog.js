import { $, esc } from "./dom.js";
import { t } from "./i18n.js";

/** In-page replacements for window.confirm()/prompt().
 *
 *  Native dialogs can be permanently silenced by the browser itself: once a
 *  user ticks "prevent this page from creating additional dialogs" (offered
 *  after a couple of confirm/prompt calls in a row), every later confirm()
 *  returns false and every prompt() returns null with no dialog shown at
 *  all - there is no way to detect this from script, and the only way out
 *  is reloading the page. For a destructive action like formatting a disk,
 *  that reads as "the button stopped working". Rendering our own modal
 *  into #modal-root (same element openBrowser() uses) sidesteps the browser
 *  API entirely, so it can't be suppressed this way. */

function openModal(bodyHtml) {
  const root = document.getElementById("modal-root");
  root.innerHTML = `<div class="modal">${bodyHtml}</div>`;
  return root;
}

function onEscape(handler) {
  const listener = (ev) => { if (ev.key === "Escape") handler(); };
  document.addEventListener("keydown", listener);
  return () => document.removeEventListener("keydown", listener);
}

export function confirmDialog(message) {
  return new Promise((resolve) => {
    const root = openModal(`
      <p>${esc(message)}</p>
      <form id="dlg-form">
        <div class="actions">
          <span class="spacer"></span>
          <button type="button" id="dlg-cancel">${esc(t("common.cancel"))}</button>
          <button class="primary">${esc(t("common.ok"))}</button>
        </div>
      </form>`);
    const stopEscape = onEscape(() => close(false));
    const close = (result) => { stopEscape(); root.innerHTML = ""; resolve(result); };
    $("#dlg-cancel").onclick = () => close(false);
    $("#dlg-form").addEventListener("submit", (ev) => { ev.preventDefault(); close(true); });
  });
}

export function promptDialog(message, defaultValue = "") {
  return new Promise((resolve) => {
    const root = openModal(`
      <p>${esc(message)}</p>
      <form id="dlg-form">
        <div class="field"><input type="text" id="dlg-input" value="${esc(defaultValue)}"></div>
        <div class="actions">
          <span class="spacer"></span>
          <button type="button" id="dlg-cancel">${esc(t("common.cancel"))}</button>
          <button class="primary">${esc(t("common.ok"))}</button>
        </div>
      </form>`);
    const input = $("#dlg-input");
    const stopEscape = onEscape(() => close(null));
    const close = (result) => { stopEscape(); root.innerHTML = ""; resolve(result); };
    $("#dlg-cancel").onclick = () => close(null);
    $("#dlg-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      close(input.value.trim() || null);
    });
    input.focus();
    input.select();
  });
}
