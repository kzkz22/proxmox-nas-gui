import { api, guard, refreshState } from "../core/api.js";
import { confirmDialog } from "../core/dialog.js";
import { $, esc, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";

/** Fixed display order, independent of whatever order findings arrive in. */
const CATEGORIES = ["pools", "binds", "shares", "mounts", "units", "disks"];

export async function diagList() {
  view().innerHTML = `
    <div class="page-head">
      <h1>${esc(t("diag.title"))}</h1>
      <button class="primary" id="run-diag">${esc(t("diag.runAll"))}</button>
    </div>
    <div id="diag-body">${esc(t("common.loading"))}</div>`;
  $("#run-diag").onclick = load;
  await load();
}

async function load() {
  const box = $("#diag-body");
  const btn = $("#run-diag");
  btn.disabled = true;
  btn.textContent = t("diag.running");
  let data;
  try {
    data = await guard(() => api("/diagnostics"));
  } catch (e) {
    box.textContent = e.message;
    return;
  } finally {
    btn.disabled = false;
    btn.textContent = t("diag.runAll");
  }
  if (!$("#diag-body")) return; // navigated away while the request was in flight
  render(data);
}

function render(data) {
  const box = $("#diag-body");
  const s = data.summary;
  const byCategory = {};
  for (const f of data.findings) (byCategory[f.category] ||= []).push(f);

  box.innerHTML = `
    <div class="panel summary">
      <div class="stat"><span class="k">${esc(t("diag.statCrit"))}</span>
        <span class="v${s.crit ? " crit" : ""}">${s.crit}</span></div>
      <div class="stat"><span class="k">${esc(t("diag.statWarn"))}</span>
        <span class="v${s.warn ? " warn" : ""}">${s.warn}</span></div>
      <div class="stat"><span class="k">${esc(t("diag.statInfo"))}</span>
        <span class="v">${s.info}</span></div>
      <div class="stat"><span class="k">${esc(t("diag.lastRun"))}</span>
        <span class="v">${esc(timestamp(data.generated_at))}</span></div>
    </div>
    ${data.findings.length
      ? CATEGORIES.filter((c) => byCategory[c]?.length)
          .map((c) => categoryBox(c, byCategory[c])).join("")
      : `<div class="empty">${esc(t("diag.empty"))}</div>`}`;

  wire(data.findings);
}

function categoryBox(category, findings) {
  return `<details class="warnbox" open>
    <summary class="warnline">${esc(t("diagcat." + category))} (${findings.length})</summary>
    ${findings.map(findingRow).join("")}
  </details>`;
}

/** Disk-category findings come straight from disksleep.describe() and reuse
 *  its sleepwarn.* strings verbatim - no separate copy of that copy. */
function findingText(f, part) {
  return f.category === "disks"
    ? t(`sleepwarn.${f.id}.${part}`, f.vars)
    : t(`diagfinding.${f.id}.${part}`, f.vars);
}

function findingConfirm(f) {
  return f.category === "disks" ? t(`sleepwarn.${f.id}.confirm`) : t(`diagfinding.${f.id}.confirm`);
}

function findingRow(f) {
  return `<div class="wrow">
    <div class="wsev ${esc(f.severity)}"></div>
    <div class="wbody">
      <h3>${esc(findingText(f, "title"))}</h3>
      <p>${esc(findingText(f, "body"))}</p>
      ${f.command ? `<div class="cmd">${esc(f.command)}</div>` : ""}
      <div class="wact">
        ${f.fixable
          ? `<button class="small primary" data-fix="${esc(f.id)}" data-fix-entity="${esc(f.entity)}">
              ${esc(t("diag.runFix"))}</button>`
          : f.command ? `<button class="small" data-copy="${esc(f.command)}">${esc(t("diag.copyCommand"))}</button>` : ""}
      </div>
    </div>
  </div>`;
}

function wire(findings) {
  const box = $("#diag-body");
  box.querySelectorAll("[data-fix]").forEach((btn) => btn.addEventListener("click", async () => {
    const id = btn.dataset.fix;
    const entity = btn.dataset.fixEntity;
    const finding = findings.find((f) => f.id === id && f.entity === entity);
    if (!(await confirmDialog(finding ? findingConfirm(finding) : t("diag.fixConfirm")))) return;
    const res = await guard(() => api("/diagnostics/fix", { method: "POST", body: { id, entity } }));
    toast(res.detail, "ok", 6000);
    // Some fixes rewrite the saved configuration, not just the running
    // system - enabling passthrough edits the pool. Without this the pool
    // editor would still show the settings from before the fix.
    await refreshState();
    load();
  }));

  box.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      toast(t("diag.copied"), "ok");
    } catch {
      toast(btn.dataset.copy, "warn", 10000);
    }
  }));
}

function timestamp(unixSeconds) {
  const d = new Date(unixSeconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
