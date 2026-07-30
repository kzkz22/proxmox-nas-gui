import { api, guard, refreshState, S } from "../core/api.js";
import { openBrowser } from "../core/browser.js";
import { $, esc, humanSize, reportResult, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { setPrefillPath } from "../core/nav.js";
import { usageBar } from "./usage.js";

export async function poolsList() {
  const names = Object.keys(S.pools).sort();
  const rows = names.map((n) => {
    const p = S.pools[n];
    return `<tr class="clickable" data-name="${esc(n)}">
      <td><b>${esc(n)}</b></td>
      <td class="mono">${esc(p.mountpoint)}</td>
      <td><span class="badge ${p.mounted ? "on" : "off"}">${esc(t(p.mounted ? "pools.mounted" : "pools.notMounted"))}</span></td>
      <td>${p.usage ? usageBar(p.usage) : "—"}</td>
    </tr>`;
  }).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("pools.title"))}</h1>
      <button class="primary" id="add-pool">+ ${esc(t("pools.add"))}</button></div>
    ${names.length ? `<table><thead><tr><th>${esc(t("common.name"))}</th>
        <th>${esc(t("pools.mountpoint"))}</th><th>${esc(t("pools.status"))}</th>
        <th>${esc(t("pools.usage"))}</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty">${esc(t("pools.empty"))}</div>`}
    <h2>${esc(t("pools.disksTitle"))}</h2>
    <div id="disks-section" class="panel">${esc(t("common.loading"))}</div>`;
  $("#add-pool").onclick = () => (location.hash = "#/pools/new");
  view().querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", () => (location.hash = `#/pools/edit/${encodeURIComponent(tr.dataset.name)}`)));
  renderDisksSection();
}

/** Kept in the same module as poolsList on purpose: the two call each other,
 *  which across module boundaries would be an import cycle. */
async function renderDisksSection() {
  const box = $("#disks-section");
  let data;
  try {
    data = await api("/disks");
  } catch (e) {
    if (box) box.textContent = e.message;
    return;
  }
  if (!box) return;
  const mountNames = Object.keys(data.mounts).sort();
  const managedRows = mountNames.map((n) => {
    const m = data.mounts[n];
    const pools = m.used_by_pools || [];
    return `<tr><td><b>${esc(n)}</b>
        ${pools.length ? `<div class="mono">${esc(t("disks.usedByPool", { pools: pools.join(", ") }))}</div>` : ""}</td>
      <td class="mono">${esc(m.mountpoint)}</td>
      <td class="mono">${esc(m.fstype)}</td>
      <td>${m.usage ? usageBar(m.usage) : `<span class="badge off">${esc(t("pools.notMounted"))}</span>`}</td>
      <td><button class="small danger" data-unmount="${esc(n)}" ${pools.length ? "disabled" : ""}>
        ${esc(t("disks.unmountBtn"))}</button></td></tr>`;
  }).join("");
  const candidates = data.devices.filter((d) => d.mountable);
  const candRows = candidates.map((d) => `
    <tr><td class="mono">${esc(d.path)}${byIdLine(d)}</td>
      <td>${esc(d.model || "")}${d.label ? ` <span class="mono">(${esc(d.label)})</span>` : ""}</td>
      <td>${humanSize(d.size)}</td>
      <td class="mono">${esc(d.fstype)}</td>
      <td><button class="small primary" data-mount="${esc(d.uuid)}">${esc(t("disks.mountBtn"))}</button></td></tr>`).join("");
  const blanks = data.devices.filter((d) => d.formattable);
  const blankRows = blanks.map((d, i) => `
    <tr><td class="mono">${esc(d.path)}${byIdLine(d)}
        <div style="font-size:0.85em;opacity:0.7">${esc(t("disks.currentFsNone"))}</div></td>
      <td>${esc(d.model || "")}</td>
      <td>${humanSize(d.size)}</td>
      <td><select data-fstype="${i}"><option value="ext4">ext4</option><option value="xfs">xfs</option></select></td>
      <td><button class="small danger" data-format="${esc(d.path)}" data-format-idx="${i}"
        data-size="${humanSize(d.size)}">${esc(t("disks.formatBtn"))}</button></td></tr>`).join("");
  box.innerHTML = `
    ${mountNames.length ? `<h2 style="margin-top:0">${esc(t("disks.managed"))}</h2>
      <table><tbody>${managedRows}</tbody></table>` : ""}
    <h2 ${mountNames.length ? "" : 'style="margin-top:0"'}>${esc(t("disks.available"))}</h2>
    ${candidates.length ? `<table><thead><tr><th>${esc(t("disks.device"))}</th><th></th>
        <th>${esc(t("disks.size"))}</th><th></th><th></th></tr></thead><tbody>${candRows}</tbody></table>`
      : `<div class="matrix-note">${esc(t("disks.none"))}</div>`}
    <h2>${esc(t("disks.formattable"))}</h2>
    ${blanks.length ? `<table><thead><tr><th>${esc(t("disks.device"))}</th><th></th>
        <th>${esc(t("disks.size"))}</th><th>${esc(t("disks.formatFsLabel"))}</th><th></th></tr></thead>
        <tbody>${blankRows}</tbody></table>`
      : `<div class="matrix-note">${esc(t("disks.noneFormattable"))}</div>`}`;
  box.querySelectorAll("[data-mount]").forEach((btn) => btn.addEventListener("click", async () => {
    const mname = prompt(t("disks.namePrompt"));
    if (!mname) return;
    await guard(() => api("/disks/mount", { method: "POST", body: { uuid: btn.dataset.mount, name: mname } }));
    toast(t("common.saved"), "ok");
    await refreshState();
    poolsList();
  }));
  box.querySelectorAll("[data-unmount]").forEach((btn) => btn.addEventListener("click", async () => {
    const mname = btn.dataset.unmount;
    if (!confirm(t("disks.unmountConfirm", { name: mname }))) return;
    await guard(() => api(`/disks/mount/${encodeURIComponent(mname)}`, { method: "DELETE" }));
    toast(t("common.saved"), "ok");
    await refreshState();
    poolsList();
  }));
  box.querySelectorAll("[data-format]").forEach((btn) => btn.addEventListener("click", async () => {
    const path = btn.dataset.format;
    const select = $(`[data-fstype="${btn.dataset.formatIdx}"]`);
    const fstype = select.value;
    if (!confirm(t("disks.formatConfirm", { path, size: btn.dataset.size, fstype }))) return;
    const mname = prompt(t("disks.namePrompt"));
    if (!mname) return;
    toast(t("disks.formatStarted", { path }), "ok");
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    select.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${esc(t("disks.formatting"))}`;
    try {
      await guard(() => api("/disks/format", { method: "POST", body: { path, fstype, name: mname } }));
      toast(t("common.saved"), "ok");
      await refreshState();
      poolsList();
    } finally {
      btn.disabled = false;
      select.disabled = false;
      btn.innerHTML = originalHtml;
    }
  }));
}

function byIdLine(d) {
  return d.by_id && d.by_id.length
    ? `<div class="mono" style="font-size:0.85em;opacity:0.7">${esc(d.by_id[0])}</div>`
    : "";
}

export function poolForm(name) {
  const isNew = !name;
  const p = isNew
    ? { name: "", mountpoint: "", branches: [], create_policy: "mfs",
        minfreespace: "4G", moveonenospc: true, extra_options: "" }
    : S.pools[name];
  if (!p) { location.hash = "#/pools"; return; }
  const branches = p.branches.map((b) => ({ path: b.path, mode: b.mode }));
  const policies = ["mfs", "epmfs", "ff", "pfrd", "rand", "lus", "lfs", "eplfs", "epff"];
  const polOpts = policies.map((v) =>
    `<option value="${v}" ${p.create_policy === v ? "selected" : ""}>${esc(t("policy." + v))}</option>`).join("");
  const usageByPath = {};
  (p.branch_usage || []).forEach((bu) => { usageByPath[bu.path] = bu.usage; });

  view().innerHTML = `
    <div class="page-head"><h1>${esc(t(isNew ? "pool.new" : "pool.edit"))}${isNew ? "" : `: ${esc(name)}`}</h1>
      ${isNew ? "" : `<button id="create-share" class="primary">+ ${esc(t("pool.createShare"))}</button>`}</div>
    <form class="panel" id="pool-form">
      <div class="field"><label>${esc(t("common.name"))}</label>
        <input type="text" name="name" value="${esc(p.name)}" ${isNew ? "" : "disabled"} required
               pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,30}">
        <div class="hint">${esc(t("pool.nameHint"))}</div></div>
      <div class="field"><label>${esc(t("pools.mountpoint"))}</label>
        <input type="text" name="mountpoint" value="${esc(p.mountpoint)}" required class="mono">
        <div class="hint">${esc(t("pool.mountpointHint"))}</div></div>
      <div class="field"><label>${esc(t("pool.branches"))}</label>
        <div id="branch-list"></div>
        <div id="quick-add"></div>
        <button type="button" id="add-branch">${esc(t("pool.addBranch"))}</button></div>
      <div class="field"><label>${esc(t("pool.policy"))}</label>
        <select name="create_policy">${polOpts}</select></div>
      <div class="field"><label>${esc(t("pool.minfree"))}</label>
        <input type="text" name="minfreespace" value="${esc(p.minfreespace)}" required pattern="\\d+[KMGT]?">
        <div class="hint">${esc(t("pool.minfreeHint"))}</div></div>
      <div class="field"><label class="check">
        <input type="checkbox" name="moveonenospc" ${p.moveonenospc ? "checked" : ""}>
        ${esc(t("pool.moveonenospc"))}</label></div>
      <div class="field"><label>${esc(t("pool.extraOptions"))}</label>
        <input type="text" name="extra_options" value="${esc(p.extra_options)}" class="mono">
        <div class="hint">${esc(t("pool.extraHint"))}</div></div>
      <div class="actions">
        <button type="button" id="cancel">${esc(t("common.cancel"))}</button>
        <span class="spacer"></span>
        ${isNew ? "" : `<button type="button" class="danger" id="del">${esc(t("common.delete"))}</button>`}
        <button class="primary">${esc(t("common.save"))}</button>
      </div>
    </form>`;

  const form = $("#pool-form");

  function renderBranches() {
    const list = $("#branch-list");
    if (!branches.length) {
      list.innerHTML = `<div class="matrix-note">${esc(t("pool.noBranches"))}</div>`;
    } else {
      list.innerHTML = branches.map((b, i) => `
        <div class="branch-row">
          <span class="mono">📀 ${esc(b.path)}</span>
          <span style="min-width:10rem">${usageBar(usageByPath[b.path]) || ""}</span>
          <select data-mode="${i}">
            ${["RW", "RO", "NC"].map((m) => `<option ${b.mode === m ? "selected" : ""}>${m}</option>`).join("")}
          </select>
          <button type="button" class="small danger" data-rm="${i}">✕</button>
        </div>`).join("");
      list.querySelectorAll("[data-rm]").forEach((btn) => btn.addEventListener("click", () => {
        branches.splice(Number(btn.dataset.rm), 1);
        renderBranches();
      }));
      list.querySelectorAll("[data-mode]").forEach((sel) => sel.addEventListener("change", () => {
        branches[Number(sel.dataset.mode)].mode = sel.value;
      }));
    }
    const inBranches = new Set(branches.map((b) => b.path));
    const chips = Object.values(S.disk_mounts || {})
      .filter((m) => !inBranches.has(m.mountpoint))
      .map((m) => `<button type="button" class="chip" data-chip="${esc(m.mountpoint)}">+ ${esc(m.mountpoint)}</button>`)
      .join("");
    $("#quick-add").innerHTML = chips ? `<span class="usage-label">${esc(t("pool.quickAdd"))}</span> ${chips}` : "";
    $("#quick-add").querySelectorAll("[data-chip]").forEach((btn) => btn.addEventListener("click", () => {
      branches.push({ path: btn.dataset.chip, mode: "RW" });
      renderBranches();
    }));
  }

  renderBranches();
  if (isNew) {
    form.name.addEventListener("input", () => {
      if (!form.mountpoint.value || form.mountpoint.dataset.auto !== "0") {
        form.mountpoint.value = form.name.value ? `/mnt/pool/${form.name.value}` : "";
      }
    });
    form.mountpoint.addEventListener("input", () => { form.mountpoint.dataset.auto = "0"; });
  }
  $("#add-branch").onclick = () =>
    openBrowser("/mnt", (path) => {
      if (!branches.some((b) => b.path === path)) branches.push({ path, mode: "RW" });
      renderBranches();
    });
  $("#cancel").onclick = () => (location.hash = "#/pools");
  if (!isNew) {
    $("#create-share").onclick = () => {
      setPrefillPath(p.mountpoint);
      location.hash = "#/shares/new";
    };
    $("#del").onclick = async () => {
      if (!confirm(t("pool.deleteConfirm", { name }))) return;
      await guard(() => api(`/pools/${encodeURIComponent(name)}`, { method: "DELETE" }));
      toast(t("common.saved"), "ok");
      await refreshState();
      location.hash = "#/pools";
    };
  }
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = {
      name: isNew ? form.name.value.trim() : name,
      mountpoint: form.mountpoint.value.trim(),
      branches,
      create_policy: form.create_policy.value,
      minfreespace: form.minfreespace.value.trim(),
      moveonenospc: form.moveonenospc.checked,
      extra_options: form.extra_options.value.trim(),
    };
    const res = await guard(() =>
      isNew
        ? api("/pools", { method: "POST", body })
        : api(`/pools/${encodeURIComponent(name)}`, { method: "PUT", body }));
    reportResult(res);
    await refreshState();
    location.hash = "#/pools";
  });
}
