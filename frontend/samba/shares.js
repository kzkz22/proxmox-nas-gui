import { api, guard, refreshState, S } from "../core/api.js";
import { openBrowser } from "../core/browser.js";
import { $, esc, humanSize, reportResult, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { takePrefillPath } from "../core/nav.js";
import { accessLevels, matrixRow, readMatrix } from "./matrix.js";

export function sharesList() {
  const names = Object.keys(S.shares).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const rows = names.map((n) => {
    const s = S.shares[n];
    return `<tr class="clickable" data-name="${esc(n)}">
      <td><b>${esc(n)}</b>${s.comment ? `<div class="mono">${esc(s.comment)}</div>` : ""}</td>
      <td class="mono">${esc(s.path)}</td>
      <td>${esc(t("export." + s.export))}</td>
      <td><span class="badge ${s.security}">${esc(t("security." + s.security))}</span></td>
      <td><span class="badge ${s.recycle ? "on" : "off"}">${s.recycle ? "✓" : "—"}</span></td>
    </tr>`;
  }).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("shares.title"))}</h1>
      <button class="primary" id="add-share">+ ${esc(t("shares.add"))}</button></div>
    ${names.length ? `<table><thead><tr>
        <th>${esc(t("common.name"))}</th><th>${esc(t("shares.path"))}</th>
        <th>${esc(t("shares.export"))}</th><th>${esc(t("shares.security"))}</th>
        <th>${esc(t("shares.recycle"))}</th></tr></thead>
      <tbody>${rows}</tbody></table>`
      : `<div class="empty">${esc(t("shares.empty"))}</div>`}`;
  $("#add-share").onclick = () => (location.hash = "#/shares/new");
  view().querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = `#/shares/edit/${encodeURIComponent(tr.dataset.name)}`)));
}

export function shareForm(name) {
  const isNew = !name;
  const prefillPath = isNew ? takePrefillPath() : "";
  const s = isNew
    ? { name: "", path: prefillPath, comment: "", export: "yes", security: "public", recycle: false, user_access: {}, group_access: {} }
    : S.shares[name];
  if (!s) { location.hash = "#/shares"; return; }

  const secRadio = ["public", "secure", "private"].map((v) => `
    <label><input type="radio" name="security" value="${v}" ${s.security === v ? "checked" : ""}>
      ${esc(t("security." + v))}</label>`).join("");
  const expOpts = ["yes", "hidden", "no"].map((v) =>
    `<option value="${v}" ${s.export === v ? "selected" : ""}>${esc(t("export." + v))}</option>`).join("");

  view().innerHTML = `
    <div class="page-head"><h1>${esc(t(isNew ? "share.new" : "share.edit"))}${isNew ? "" : `: ${esc(name)}`}</h1></div>
    <form class="panel" id="share-form">
      <div class="field"><label>${esc(t("common.name"))}</label>
        <input type="text" name="name" value="${esc(s.name)}" ${isNew ? "" : "disabled"} required
               pattern="[A-Za-z0-9][A-Za-z0-9._ -]*" maxlength="80"></div>
      <div class="field"><label>${esc(t("share.comment"))}</label>
        <input type="text" name="comment" value="${esc(s.comment)}"></div>
      <div class="field"><label>${esc(t("shares.path"))}</label>
        <div class="field-row">
          <input type="text" name="path" value="${esc(s.path)}" required class="mono">
          <button type="button" id="browse">${esc(t("share.browse"))}</button>
        </div>
        <div class="hint">${esc(t("share.pathHint"))}</div></div>
      <div class="field"><label>${esc(t("shares.export"))}</label>
        <select name="export">${expOpts}</select>
        <div class="hint">${esc(t("export.hint"))}</div></div>
      <div class="field"><label>${esc(t("shares.security"))}</label>
        <span class="radio-group" id="sec-group">${secRadio}</span>
        <div class="hint" id="sec-hint"></div></div>
      <div class="field"><label class="check">
        <input type="checkbox" name="recycle" ${s.recycle ? "checked" : ""}> ${esc(t("shares.recycle"))}</label>
        <div id="recycle-info" class="hint"></div></div>
      <div id="matrix-wrap"></div>
      <div class="actions">
        <button type="button" id="cancel">${esc(t("common.cancel"))}</button>
        <span class="spacer"></span>
        ${isNew ? "" : `<button type="button" class="danger" id="del">${esc(t("common.delete"))}</button>`}
        <button class="primary">${esc(t("common.save"))}</button>
      </div>
    </form>`;

  const form = $("#share-form");
  const matrixWrap = $("#matrix-wrap");

  function currentSecurity() {
    return form.querySelector("input[name=security]:checked").value;
  }

  function renderMatrix() {
    const sec = currentSecurity();
    $("#sec-hint").textContent = t("security.hint." + sec);
    if (sec === "public") {
      matrixWrap.innerHTML = `<h2>${esc(t("share.accessTitle"))}</h2>
        <div class="matrix-note">${esc(t("share.accessPublic"))}</div>`;
      return;
    }
    const levels = accessLevels(sec);
    const defLevel = sec === "secure" ? "read" : "no";
    const users = Object.keys(S.users).sort();
    const groups = Object.keys(S.groups).sort();
    if (!users.length && !groups.length) {
      matrixWrap.innerHTML = `<h2>${esc(t("share.accessTitle"))}</h2>
        <div class="matrix-note">${esc(t("share.noAccounts"))}</div>`;
      return;
    }
    const rows =
      users.map((u) => matrixRow("user", u, s.user_access[u] || defLevel, levels)).join("") +
      groups.map((g) => matrixRow("group", g, s.group_access[g] || defLevel, levels)).join("");
    matrixWrap.innerHTML = `<h2>${esc(t("share.accessTitle"))}</h2>
      <div class="matrix-note">${esc(t(sec === "secure" ? "share.accessHintSecure" : "share.accessHintPrivate"))}</div>
      <table><tbody>${rows}</tbody></table>`;
  }

  renderMatrix();
  $("#sec-group").addEventListener("change", renderMatrix);
  $("#browse").onclick = () =>
    openBrowser(form.path.value.trim() || "/", (p) => (form.path.value = p));
  $("#cancel").onclick = () => (location.hash = "#/shares");

  if (!isNew) {
    if (s.recycle) {
      api(`/shares/${encodeURIComponent(name)}/recycle`).then((info) => {
        $("#recycle-info").innerHTML = `${esc(t("share.recycleUsage",
          { files: info.files, size: humanSize(info.bytes) }))}
          <button type="button" class="small danger" id="recycle-empty">${esc(t("share.recycleEmptyBtn"))}</button>`;
        $("#recycle-empty").onclick = async () => {
          if (!confirm(t("share.recycleConfirm", { name }))) return;
          await guard(() => api(`/shares/${encodeURIComponent(name)}/recycle/empty`, { method: "POST" }));
          toast(t("common.saved"), "ok");
          shareForm(name);
        };
      }).catch(() => {});
    }
    $("#del").onclick = async () => {
      if (!confirm(t("share.deleteConfirm", { name }))) return;
      const res = await guard(() => api(`/shares/${encodeURIComponent(name)}`, { method: "DELETE" }));
      reportResult(res);
      await refreshState();
      location.hash = "#/shares";
    };
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const sec = currentSecurity();
    const acc = readMatrix(matrixWrap);
    const body = {
      name: isNew ? form.name.value.trim() : name,
      path: form.path.value.trim(),
      comment: form.comment.value.trim(),
      export: form.export.value,
      security: sec,
      recycle: form.recycle.checked,
      user_access: sec === "public" ? {} : acc.user,
      group_access: sec === "public" ? {} : acc.group,
    };
    const res = await guard(() =>
      isNew
        ? api("/shares", { method: "POST", body })
        : api(`/shares/${encodeURIComponent(name)}`, { method: "PUT", body }));
    reportResult(res);
    await refreshState();
    location.hash = "#/shares";
  });
}
