import { api, guard, refreshState, S } from "../core/api.js";
import { confirmDialog } from "../core/dialog.js";
import { $, esc, reportResult, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { accountShareMatrix, readShareMatrix } from "./matrix.js";

export function groupsList() {
  const names = Object.keys(S.groups).sort();
  const rows = names.map((n) => `
    <tr class="clickable" data-name="${esc(n)}">
      <td><b>👥 ${esc(n)}</b></td>
      <td>${esc(S.groups[n].description || "")}</td>
      <td class="mono">${esc((S.groups[n].members || []).join(", "))}</td>
    </tr>`).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("groups.title"))}</h1>
      <button class="primary" id="add-group">+ ${esc(t("groups.add"))}</button></div>
    ${names.length ? `<table><thead><tr><th>${esc(t("common.name"))}</th>
        <th>${esc(t("common.description"))}</th><th>${esc(t("groups.members"))}</th></tr></thead>
      <tbody>${rows}</tbody></table>`
      : `<div class="empty">${esc(t("groups.empty"))}</div>`}`;
  $("#add-group").onclick = () => (location.hash = "#/groups/new");
  view().querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = `#/groups/edit/${encodeURIComponent(tr.dataset.name)}`)));
}

export function groupForm(name) {
  const isNew = !name;
  const g = isNew ? { description: "", members: [] } : S.groups[name];
  if (!g) { location.hash = "#/groups"; return; }
  const users = Object.keys(S.users).sort();
  const memberBoxes = users.map((u) => `
    <label class="check" style="margin-bottom:.3rem">
      <input type="checkbox" name="member" value="${esc(u)}" ${(g.members || []).includes(u) ? "checked" : ""}>
      👤 ${esc(u)}</label>`).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t(isNew ? "group.new" : "group.edit"))}${isNew ? "" : `: ${esc(name)}`}</h1></div>
    <form class="panel" id="group-form">
      ${isNew ? `<div class="field"><label>${esc(t("common.name"))}</label>
        <input type="text" name="name" required pattern="[a-z_][a-z0-9_-]{0,30}">
        <div class="hint">${esc(t("user.nameHint"))}</div></div>` : ""}
      <div class="field"><label>${esc(t("common.description"))}</label>
        <input type="text" name="description" value="${esc(g.description)}"></div>
      <div class="field"><label>${esc(t("groups.members"))}</label>
        ${users.length ? memberBoxes : `<div class="hint">${esc(t("group.noUsers"))}</div>`}</div>
      <div id="group-matrix">${isNew ? "" : accountShareMatrix("group", name)}</div>
      <div class="actions">
        <button type="button" id="cancel">${esc(t("common.cancel"))}</button>
        <span class="spacer"></span>
        ${isNew ? "" : `<button type="button" class="danger" id="del">${esc(t("common.delete"))}</button>`}
        <button class="primary">${esc(t("common.save"))}</button>
      </div>
    </form>`;
  const form = $("#group-form");
  $("#cancel").onclick = () => (location.hash = "#/groups");
  if (!isNew) {
    $("#del").onclick = async () => {
      if (!(await confirmDialog(t("group.deleteConfirm", { name })))) return;
      const res = await guard(() => api(`/groups/${encodeURIComponent(name)}`, { method: "DELETE" }));
      reportResult(res);
      await refreshState();
      location.hash = "#/groups";
    };
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const members = [...form.querySelectorAll("input[name=member]:checked")].map((c) => c.value);
    let res;
    if (isNew) {
      res = await guard(() => api("/groups", {
        method: "POST",
        body: { name: form.name.value.trim(), description: form.description.value.trim(), members },
      }));
    } else {
      res = await guard(() => api(`/groups/${encodeURIComponent(name)}`, {
        method: "PUT",
        body: { description: form.description.value.trim(), members, access: readShareMatrix($("#group-matrix")) },
      }));
    }
    reportResult(res);
    await refreshState();
    location.hash = "#/groups";
  });
}
