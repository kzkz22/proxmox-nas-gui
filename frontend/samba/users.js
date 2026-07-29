import { api, guard, refreshState, S } from "../core/api.js";
import { $, esc, reportResult, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { accountShareMatrix, readShareMatrix } from "./matrix.js";

export function usersList() {
  const names = Object.keys(S.users).sort();
  const rows = names.map((n) => `
    <tr class="clickable" data-name="${esc(n)}">
      <td><b>👤 ${esc(n)}</b></td>
      <td>${esc(S.users[n].description || "")}</td>
    </tr>`).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("users.title"))}</h1>
      <button class="primary" id="add-user">+ ${esc(t("users.add"))}</button></div>
    ${names.length ? `<table><thead><tr><th>${esc(t("common.name"))}</th>
        <th>${esc(t("common.description"))}</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty">${esc(t("users.empty"))}</div>`}`;
  $("#add-user").onclick = () => (location.hash = "#/users/new");
  view().querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = `#/users/edit/${encodeURIComponent(tr.dataset.name)}`)));
}

export function userForm(name) {
  const isNew = !name;
  const u = isNew ? { description: "" } : S.users[name];
  if (!u) { location.hash = "#/users"; return; }
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t(isNew ? "user.new" : "user.edit"))}${isNew ? "" : `: ${esc(name)}`}</h1></div>
    <form class="panel" id="user-form">
      ${isNew ? `<div class="field"><label>${esc(t("common.name"))}</label>
        <input type="text" name="name" required pattern="[a-z_][a-z0-9_-]{0,30}">
        <div class="hint">${esc(t("user.nameHint"))}</div></div>` : ""}
      <div class="field"><label>${esc(t("common.description"))}</label>
        <input type="text" name="description" value="${esc(u.description)}"></div>
      <div class="field"><label>${esc(t("user.password"))}</label>
        <input type="password" name="password" autocomplete="new-password" ${isNew ? "required" : ""}>
        ${isNew ? "" : `<div class="hint">${esc(t("user.passwordChangeHint"))}</div>`}</div>
      <div class="field"><label>${esc(t("user.password2"))}</label>
        <input type="password" name="password2" autocomplete="new-password" ${isNew ? "required" : ""}></div>
      <div id="user-matrix">${isNew ? "" : accountShareMatrix("user", name)}</div>
      <div class="actions">
        <button type="button" id="cancel">${esc(t("common.cancel"))}</button>
        <span class="spacer"></span>
        ${isNew ? "" : `<button type="button" class="danger" id="del">${esc(t("common.delete"))}</button>`}
        <button class="primary">${esc(t("common.save"))}</button>
      </div>
    </form>`;
  const form = $("#user-form");
  $("#cancel").onclick = () => (location.hash = "#/users");
  if (!isNew) {
    $("#del").onclick = async () => {
      if (!confirm(t("user.deleteConfirm", { name }))) return;
      const res = await guard(() => api(`/users/${encodeURIComponent(name)}`, { method: "DELETE" }));
      reportResult(res);
      await refreshState();
      location.hash = "#/users";
    };
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (form.password.value !== form.password2.value) {
      toast(t("user.passwordMismatch"), "err");
      return;
    }
    let res;
    if (isNew) {
      res = await guard(() => api("/users", {
        method: "POST",
        body: { name: form.name.value.trim(), password: form.password.value, description: form.description.value.trim() },
      }));
    } else {
      const body = { description: form.description.value.trim(), access: readShareMatrix($("#user-matrix")) };
      if (form.password.value) body.password = form.password.value;
      res = await guard(() => api(`/users/${encodeURIComponent(name)}`, { method: "PUT", body }));
    }
    reportResult(res);
    await refreshState();
    location.hash = "#/users";
  });
}
