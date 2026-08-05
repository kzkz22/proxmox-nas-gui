import { api, guard, refreshState, S } from "../core/api.js";
import { openBrowser } from "../core/browser.js";
import { confirmDialog } from "../core/dialog.js";
import { $, esc, reportResult, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { setPrefillPath } from "../core/nav.js";

/** Where a bind's data physically lives, as a short label.
 *
 *  The whole point of the page is that the browsable path says nothing about
 *  the storage behind it, so every row has to say it explicitly. */
function backingLabel(b) {
  if (b.backing_pool) return `POOL: ${b.backing_pool}`;
  if (b.source_on_root_fs || b.source_mount === "/") return t("binds.onRootFs");
  // Prefixed, or it reads as a second copy of the source path rather than as
  // the filesystem that path sits on.
  return `fs: ${b.source_mount}`;
}

function statusBadge(b) {
  return `<span class="badge ${b.mounted ? "on" : "off"}">${
    esc(t(b.mounted ? "binds.mounted" : "binds.notMounted"))}</span>`;
}

/** Group the bind targets into the directory tree they actually form.
 *
 *  A flat source->target table is accurate but unreadable; the users' mental
 *  model is the folder tree they browse, so that is what gets drawn. */
function buildTree(binds) {
  const root = { children: new Map(), bind: null };
  for (const b of binds) {
    let node = root;
    for (const part of b.target.split("/").filter(Boolean)) {
      if (!node.children.has(part)) {
        node.children.set(part, { children: new Map(), bind: null });
      }
      node = node.children.get(part);
    }
    node.bind = b;
  }
  return root;
}

/** Flatten the tree to rows, folding single-child chains into one line so a
 *  deep presentation root shows as "/mnt/family_pool", not four levels. */
function treeRows(node, depth, prefix, out) {
  const names = [...node.children.keys()].sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    let label = name;
    let cur = node.children.get(name);
    while (!cur.bind && cur.children.size === 1) {
      const [childName] = cur.children.keys();
      label += `/${childName}`;
      cur = cur.children.get(childName);
    }
    out.push({ label: prefix + label, depth, bind: cur.bind });
    treeRows(cur, depth + 1, "", out);
  }
  return out;
}

function renderTree(binds) {
  const rows = treeRows(buildTree(binds), 0, "/", []);
  return rows.map((row) => {
    const b = row.bind;
    const indent = `style="padding-left:${0.6 + row.depth * 1.3}rem"`;
    if (!b) {
      return `<div class="tree-row" ${indent}><span class="mono">📁 ${esc(row.label)}</span></div>`;
    }
    return `<div class="tree-row" ${indent}>
      <span class="mono">🔗 ${esc(row.label)}</span>
      <span class="tree-src mono">← ${esc(b.source)}</span>
      <span class="ds">${esc(backingLabel(b))}</span>
      ${b.read_only ? `<span class="badge off">${esc(t("binds.readOnly"))}</span>` : ""}
      ${b.source_exists ? "" : `<span class="badge private">${esc(t("binds.sourceMissing"))}</span>`}
      ${statusBadge(b)}</div>`;
  }).join("");
}

export async function bindsList() {
  const names = Object.keys(S.bind_mounts || {}).sort();
  const binds = names.map((n) => S.bind_mounts[n]);
  const rows = names.map((n) => {
    const b = S.bind_mounts[n];
    return `<tr>
      <td><b>${esc(n)}</b></td>
      <td class="mono">${esc(b.source)}<div class="ds">${esc(backingLabel(b))}</div></td>
      <td class="mono">${esc(b.target)}</td>
      <td>${statusBadge(b)}</td>
      <td>
        <button class="small" data-edit="${esc(n)}">${esc(t("binds.editBtn"))}</button>
        <button class="small" data-toggle="${esc(n)}" data-mounted="${b.mounted ? "1" : ""}">
          ${esc(t(b.mounted ? "binds.unmountBtn" : "binds.mountBtn"))}</button>
      </td></tr>`;
  }).join("");

  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("binds.title"))}</h1>
      <div>
        <button id="gen-toggle">${esc(t("gen.title"))}</button>
        <button class="primary" id="add-bind">+ ${esc(t("binds.add"))}</button>
      </div></div>
    <div class="matrix-note">${esc(t("binds.intro"))}</div>
    <div id="gen-panel" hidden></div>
    ${names.length ? `<h2>${esc(t("binds.treeTitle"))}</h2>
      <div class="panel tree">${renderTree(binds)}</div>
      <h2>${esc(t("binds.tableTitle"))}</h2>
      <table><thead><tr><th>${esc(t("common.name"))}</th><th>${esc(t("binds.source"))}</th>
        <th>${esc(t("binds.target"))}</th><th>${esc(t("binds.status"))}</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`
      : `<div class="empty">${esc(t("binds.empty"))}</div>`}
    <div class="matrix-note" style="margin-top:1.2rem">⚠ ${esc(t("binds.syncHint"))}</div>`;

  $("#add-bind").onclick = () => (location.hash = "#/binds/new");
  $("#gen-toggle").onclick = () => {
    const panel = $("#gen-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) renderGenerator(panel);
  };
  view().querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () =>
    (location.hash = `#/binds/edit/${encodeURIComponent(btn.dataset.edit)}`)));
  view().querySelectorAll("[data-toggle]").forEach((btn) => btn.addEventListener("click", async () => {
    const name = btn.dataset.toggle;
    const action = btn.dataset.mounted ? "unmount" : "mount";
    if (action === "unmount" && !(await confirmDialog(t("binds.unmountConfirm", { name })))) return;
    const res = await guard(() =>
      api(`/binds/${encodeURIComponent(name)}/${action}`, { method: "POST" }));
    reportResult(res);
    await refreshState();
    bindsList();
  }));
}

/** The tree generator.
 *
 *  Kept in the same module as bindsList because the two call each other, which
 *  across module boundaries would be an import cycle - the same reason the
 *  disks section lives inside pools.js. */
function renderGenerator(panel) {
  let planned = [];

  panel.innerHTML = `
    <div class="panel">
      <h2 style="margin-top:0">${esc(t("gen.title"))}</h2>
      <div class="matrix-note">${esc(t("gen.intro"))}</div>
      <div class="field"><label>${esc(t("gen.root"))}</label>
        <div class="field-row"><input type="text" id="gen-root" class="mono" value="/mnt/family_pool">
          <button type="button" id="gen-root-browse">${esc(t("bind.browse"))}</button></div>
        <div class="hint">${esc(t("gen.rootHint"))}</div></div>
      <div class="field"><label>${esc(t("gen.folders"))}</label>
        <input type="text" id="gen-folders" class="mono" placeholder="kz, kzs, kv">
        <div class="hint">${esc(t("gen.foldersHint"))}</div></div>
      <div class="field"><label>${esc(t("gen.tiers"))}</label>
        <div id="gen-tiers"></div>
        <button type="button" id="gen-add-tier">${esc(t("gen.addTier"))}</button>
        <div class="hint">${esc(t("gen.tiersHint"))}</div></div>
      <div class="field"><label>${esc(t("gen.preview"))}</label>
        <div id="gen-preview"></div></div>
      <div class="field"><label class="check">
        <input type="checkbox" id="gen-create-sources" checked>
        ${esc(t("gen.createSources"))}</label>
        <div class="hint">${esc(t("gen.createSourcesHint"))}</div></div>
      <div class="actions">
        <button type="button" id="gen-refresh">${esc(t("gen.refresh"))}</button>
        <span class="spacer"></span>
        <button type="button" class="primary" id="gen-apply">${esc(t("gen.apply"))}</button>
      </div>
    </div>`;

  function addTier(label = "", sourceRoot = "") {
    const row = document.createElement("div");
    row.className = "branch-row tier-row";
    row.innerHTML = `
      <input type="text" data-tier-label class="mono" style="width:9rem" value="${esc(label)}"
             placeholder="${esc(t("gen.label"))}">
      <input type="text" data-tier-source class="mono" style="flex:1" value="${esc(sourceRoot)}"
             placeholder="${esc(t("gen.sourceRoot"))}">
      <button type="button" class="small" data-tier-browse>${esc(t("bind.browse"))}</button>
      <button type="button" class="small danger" data-tier-remove>✕</button>`;
    row.querySelector("[data-tier-browse]").onclick = () =>
      openBrowser("/mnt", (path) => {
        row.querySelector("[data-tier-source]").value = path;
        refreshPreview();
      });
    row.querySelector("[data-tier-remove]").onclick = () => { row.remove(); refreshPreview(); };
    row.querySelectorAll("input").forEach((input) =>
      input.addEventListener("change", refreshPreview));
    $("#gen-tiers").appendChild(row);
  }

  function readTiers() {
    return [...panel.querySelectorAll(".tier-row")]
      .map((row) => ({
        label: row.querySelector("[data-tier-label]").value.trim(),
        source_root: row.querySelector("[data-tier-source]").value.trim(),
      }))
      .filter((tier) => tier.label && tier.source_root);
  }

  async function refreshPreview() {
    const box = $("#gen-preview");
    const root = $("#gen-root").value.trim();
    const folders = $("#gen-folders").value.split(",").map((s) => s.trim()).filter(Boolean);
    const tiers = readTiers();
    planned = [];
    if (!root || !folders.length || !tiers.length) {
      box.innerHTML = `<div class="matrix-note">${esc(t("gen.previewEmpty"))}</div>`;
      return;
    }
    let res;
    try {
      res = await api("/binds/plan", { method: "POST", body: { root, folders, tiers } });
    } catch (e) {
      box.innerHTML = `<div class="matrix-note">${esc(e.message)}</div>`;
      return;
    }
    planned = res.binds;
    box.innerHTML = `<table><thead><tr><th>${esc(t("binds.source"))}</th>
        <th>${esc(t("binds.target"))}</th><th></th></tr></thead><tbody>${
      planned.map((p) => `<tr>
        <td class="mono">${esc(p.source)}
          <div class="ds">${esc(backingLabel(p))}</div></td>
        <td class="mono">${esc(p.target)}</td>
        <td>${p.conflict
          ? `<span class="badge private">${esc(p.conflict)}</span>`
          : p.source_exists
            ? `<span class="badge on">${esc(t("gen.exists"))}</span>`
            : `<span class="badge secure">${esc(t("gen.missing"))}</span>`}</td></tr>`).join("")
    }</tbody></table>`;
  }

  addTier("fontos", "");
  addTier("nemfontos", "");
  refreshPreview();

  $("#gen-add-tier").onclick = () => addTier();
  $("#gen-refresh").onclick = refreshPreview;
  $("#gen-root").addEventListener("change", refreshPreview);
  $("#gen-folders").addEventListener("change", refreshPreview);
  $("#gen-root-browse").onclick = () =>
    openBrowser("/mnt", (path) => { $("#gen-root").value = path; refreshPreview(); });

  $("#gen-apply").onclick = async () => {
    await refreshPreview();
    if (!planned.length) { toast(t("gen.previewEmpty"), "err"); return; }
    const blocked = planned.filter((p) => p.conflict);
    if (blocked.length) { toast(t("gen.hasConflicts"), "err", 6000); return; }
    const res = await guard(() => api("/binds/bulk", {
      method: "POST",
      body: {
        binds: planned.map(({ name, source, target, read_only }) =>
          ({ name, source, target, read_only })),
        create_sources: $("#gen-create-sources").checked,
      },
    }));
    reportResult(res);
    await refreshState();
    bindsList();
  };
}

export function bindForm(name) {
  const isNew = !name;
  const b = isNew
    ? { name: "", source: "", target: "", read_only: false }
    : S.bind_mounts[name];
  if (!b) { location.hash = "#/binds"; return; }

  view().innerHTML = `
    <div class="page-head"><h1>${esc(t(isNew ? "bind.new" : "bind.edit"))}${isNew ? "" : `: ${esc(name)}`}</h1>
      ${isNew ? "" : `<button id="create-share" class="primary">+ ${esc(t("bind.createShare"))}</button>`}</div>
    <form class="panel" id="bind-form">
      <div class="field"><label>${esc(t("common.name"))}</label>
        <input type="text" name="name" value="${esc(b.name)}" ${isNew ? "" : "disabled"} required
               pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,30}">
        <div class="hint">${esc(t("bind.nameHint"))}</div></div>
      <div class="field"><label>${esc(t("bind.source"))}</label>
        <div class="field-row">
          <input type="text" name="source" value="${esc(b.source)}" required class="mono">
          <button type="button" id="browse-source">${esc(t("bind.browse"))}</button></div>
        <div class="hint">${esc(t("bind.sourceHint"))}</div>
        ${isNew ? "" : `<div class="hint">${esc(t("binds.backing"))}: ${esc(backingLabel(b))}${
          b.source_exists ? "" : ` — ${esc(t("binds.sourceMissing"))}`}</div>`}
        ${!isNew && b.source_on_root_fs
          ? `<div class="hint">⚠ ${esc(t("binds.rootFsWarn"))}</div>` : ""}</div>
      <div class="field"><label>${esc(t("bind.target"))}</label>
        <div class="field-row">
          <input type="text" name="target" value="${esc(b.target)}" required class="mono">
          <button type="button" id="browse-target">${esc(t("bind.browse"))}</button></div>
        <div class="hint">${esc(t("bind.targetHint"))}</div></div>
      <div class="field"><label class="check">
        <input type="checkbox" name="read_only" ${b.read_only ? "checked" : ""}>
        ${esc(t("bind.readOnlyLabel"))}</label></div>
      <div class="field"><label class="check">
        <input type="checkbox" name="create_source" ${isNew ? "checked" : ""}>
        ${esc(t("bind.createSource"))}</label>
        <div class="hint">${esc(t("bind.createSourceHint"))}</div></div>
      <div class="matrix-note">⚠ ${esc(t("binds.syncHint"))}</div>
      <div class="actions">
        <button type="button" id="cancel">${esc(t("common.cancel"))}</button>
        <span class="spacer"></span>
        ${isNew ? "" : `<button type="button" class="danger" id="del">${esc(t("common.delete"))}</button>`}
        <button class="primary">${esc(t("common.save"))}</button>
      </div>
    </form>`;

  const form = $("#bind-form");
  $("#browse-source").onclick = () =>
    openBrowser(form.source.value || "/mnt", (path) => { form.source.value = path; });
  $("#browse-target").onclick = () =>
    openBrowser(form.target.value || "/mnt", (path) => { form.target.value = path; });
  $("#cancel").onclick = () => (location.hash = "#/binds");

  if (!isNew) {
    $("#create-share").onclick = () => {
      setPrefillPath(b.target);
      location.hash = "#/shares/new";
    };
    $("#del").onclick = async () => {
      if (!(await confirmDialog(t("binds.deleteConfirm", { name })))) return;
      const res = await guard(() =>
        api(`/binds/${encodeURIComponent(name)}`, { method: "DELETE" }));
      reportResult(res);
      await refreshState();
      location.hash = "#/binds";
    };
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {
      name: isNew ? form.name.value.trim() : name,
      source: form.source.value.trim(),
      target: form.target.value.trim(),
      read_only: form.read_only.checked,
    };
    const query = `?create_source=${form.create_source.checked}`;
    const res = await guard(() =>
      isNew
        ? api(`/binds${query}`, { method: "POST", body })
        : api(`/binds/${encodeURIComponent(name)}${query}`, { method: "PUT", body }));
    reportResult(res);
    await refreshState();
    location.hash = "#/binds";
  });
}
