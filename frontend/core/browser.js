import { api } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { t } from "./i18n.js";

/** Extra markup shown next to a directory entry.
 *
 *  The browser is used by both halves of the app and must not know about
 *  either, so the decorator is injected once by main.js rather than imported
 *  here. Default: no decoration. */
let decorateEntry = () => "";

export function setEntryDecorator(fn) {
  decorateEntry = fn;
}

export function openBrowser(startPath, onSelect) {
  const root = document.getElementById("modal-root");

  async function render(path) {
    let data;
    try {
      data = await api(`/fs/list?path=${encodeURIComponent(path)}`);
    } catch (e) {
      toast(e.message, "err");
      return;
    }
    const items = data.entries.map((e2) => `
      <div class="browser-item" data-path="${esc(e2.path)}">📁 ${esc(e2.name)}
        ${e2.dataset ? `<span class="ds">ZFS: ${esc(e2.dataset)}</span>` : ""}
        ${decorateEntry(e2)}</div>`).join("");
    root.innerHTML = `
      <div class="modal">
        <h2>${esc(t("browse.title"))}</h2>
        <div class="browser-path mono">${esc(data.path)}${data.dataset ? ` <span class="ds">— ZFS: ${esc(data.dataset)}</span>` : ""}</div>
        <div class="browser-list">${items || `<div class="browser-item" style="cursor:default">—</div>`}</div>
        <div class="actions">
          <button id="br-up" ${data.parent ? "" : "disabled"}>⬆ ${esc(t("browse.up"))}</button>
          <button id="br-mkdir">${esc(t("browse.newFolder"))}</button>
          ${data.dataset ? `<button id="br-mkds">${esc(t("browse.newDataset"))}</button>` : ""}
          <span class="spacer"></span>
          <button id="br-cancel">${esc(t("common.cancel"))}</button>
          <button id="br-select" class="primary">${esc(t("browse.select"))}</button>
        </div>
      </div>`;
    root.querySelectorAll(".browser-item[data-path]").forEach((el) =>
      el.addEventListener("click", () => render(el.dataset.path)));
    $("#br-up").onclick = () => data.parent && render(data.parent);
    $("#br-cancel").onclick = () => (root.innerHTML = "");
    $("#br-select").onclick = () => { root.innerHTML = ""; onSelect(data.path); };
    const mk = async (dataset) => {
      const name = prompt(t(dataset ? "browse.datasetPrompt" : "browse.namePrompt"));
      if (!name) return;
      try {
        await api("/fs/mkdir", { method: "POST", body: { parent: data.path, name, dataset } });
        render(data.path);
      } catch (e) { toast(e.message, "err"); }
    };
    $("#br-mkdir").onclick = () => mk(false);
    const mkds = $("#br-mkds");
    if (mkds) mkds.onclick = () => mk(true);
  }

  render(startPath || "/");
}
