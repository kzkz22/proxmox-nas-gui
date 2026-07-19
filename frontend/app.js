"use strict";

/* ---------- i18n ---------- */

let LANG = localStorage.getItem("psg_lang") || "hu";
let DICT = {};

async function loadLang() {
  const res = await fetch(`i18n/${LANG}.json`);
  DICT = await res.json();
  document.documentElement.lang = LANG;
  document.getElementById("lang-toggle").textContent = LANG === "hu" ? "EN" : "HU";
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
}

function t(key, vars) {
  let s = DICT[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

/* ---------- helpers ---------- */

const $ = (sel, root) => (root || document).querySelector(sel);
const view = () => document.getElementById("view");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function humanSize(bytes) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function toast(msg, kind = "ok", ms = 3500) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  document.getElementById("toast-root").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function reportResult(res) {
  toast(t("common.saved"), "ok");
  if (res && res.warning) toast(res.warning, "warn", 6000);
}

/* ---------- API ---------- */

class ApiError extends Error {}

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (init.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(`/api${path}`, init);
  if (res.status === 401 && path !== "/login") {
    showLogin();
    throw new ApiError("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof data.detail === "string" ? data.detail : res.statusText;
    throw new ApiError(msg);
  }
  return data;
}

async function guard(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof ApiError && e.message !== "unauthorized") toast(e.message, "err", 6000);
    else if (!(e instanceof ApiError)) toast(String(e), "err", 6000);
    throw e;
  }
}

let S = null;

async function refreshState() {
  S = await api("/state");
  return S;
}

/* ---------- login ---------- */

function showLogin() {
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
    } catch {
      toast(t("login.error"), "err");
    }
  });
}

/* ---------- access matrix ---------- */

function accessLevels(security) {
  return security === "secure" ? ["read", "write"] : ["no", "read", "write"];
}

function matrixRow(kind, name, level, levels, extra = "") {
  const radios = levels.map((lv) => `
    <label><input type="radio" name="acc-${kind}-${esc(name)}" value="${lv}" ${lv === level ? "checked" : ""}>
      ${esc(t("access." + lv))}</label>`).join("");
  return `<tr>
    <td>${kind === "group" ? "👥 " : "👤 "}${esc(name)}${extra}</td>
    <td><span class="radio-group" data-kind="${kind}" data-name="${esc(name)}">${radios}</span></td>
  </tr>`;
}

function readMatrix(root) {
  const out = { user: {}, group: {} };
  root.querySelectorAll(".radio-group[data-kind]").forEach((g) => {
    const checked = g.querySelector("input:checked");
    if (checked) out[g.dataset.kind][g.dataset.name] = checked.value;
  });
  return out;
}

function usageBar(u) {
  if (!u || !u.total) return "";
  const pct = Math.round((u.used / u.total) * 100);
  const cls = pct >= 90 ? "crit" : pct >= 75 ? "warn" : "";
  return `<div class="usage-bar"><div class="usage-fill ${cls}" style="width:${pct}%"></div></div>
    <div class="usage-label">${humanSize(u.used)} / ${humanSize(u.total)} (${pct}%)</div>`;
}

/* ---------- directory browser modal ---------- */

function openBrowser(startPath, onSelect) {
  const root = document.getElementById("modal-root");

  async function render(path) {
    let data;
    try {
      data = await api(`/fs/list?path=${encodeURIComponent(path)}`);
    } catch (e) {
      toast(e.message, "err");
      return;
    }
    const poolByMp = {};
    for (const [pn, p] of Object.entries(S && S.pools || {})) poolByMp[p.mountpoint] = pn;
    const items = data.entries.map((e2) => `
      <div class="browser-item" data-path="${esc(e2.path)}">📁 ${esc(e2.name)}
        ${e2.dataset ? `<span class="ds">ZFS: ${esc(e2.dataset)}</span>` : ""}
        ${poolByMp[e2.path] ? `<span class="ds">POOL: ${esc(poolByMp[e2.path])}</span>` : ""}</div>`).join("");
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

/* ---------- shares ---------- */

function sharesList() {
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

function shareForm(name) {
  const isNew = !name;
  const prefillPath = isNew ? sessionStorage.getItem("psg_prefill_path") || "" : "";
  if (prefillPath) sessionStorage.removeItem("psg_prefill_path");
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

/* ---------- pools & disks ---------- */

async function poolsList() {
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
    <tr><td class="mono">${esc(d.path)}</td>
      <td>${esc(d.model || "")}${d.label ? ` <span class="mono">(${esc(d.label)})</span>` : ""}</td>
      <td>${humanSize(d.size)}</td>
      <td class="mono">${esc(d.fstype)}</td>
      <td><button class="small primary" data-mount="${esc(d.uuid)}">${esc(t("disks.mountBtn"))}</button></td></tr>`).join("");
  box.innerHTML = `
    ${mountNames.length ? `<h2 style="margin-top:0">${esc(t("disks.managed"))}</h2>
      <table><tbody>${managedRows}</tbody></table>` : ""}
    <h2 ${mountNames.length ? "" : 'style="margin-top:0"'}>${esc(t("disks.available"))}</h2>
    ${candidates.length ? `<table><thead><tr><th>${esc(t("disks.device"))}</th><th></th>
        <th>${esc(t("disks.size"))}</th><th></th><th></th></tr></thead><tbody>${candRows}</tbody></table>`
      : `<div class="matrix-note">${esc(t("disks.none"))}</div>`}`;
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
}

function poolForm(name) {
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
      sessionStorage.setItem("psg_prefill_path", p.mountpoint);
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

/* ---------- users ---------- */

function usersList() {
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

function accountShareMatrix(kind, accountName) {
  const shares = Object.keys(S.shares).sort();
  if (!shares.length) return "";
  const rows = shares.map((sn) => {
    const share = S.shares[sn];
    if (share.security === "public") {
      return `<tr><td>▣ ${esc(sn)}</td>
        <td><span class="badge public">${esc(t("security.public"))}</span></td></tr>`;
    }
    const levels = accessLevels(share.security);
    const map = kind === "user" ? share.user_access : share.group_access;
    const level = map[accountName] || (share.security === "secure" ? "read" : "no");
    const radios = levels.map((lv) => `
      <label><input type="radio" name="sacc-${esc(sn)}" value="${lv}" ${lv === level ? "checked" : ""}>
        ${esc(t("access." + lv))}</label>`).join("");
    return `<tr><td>▣ ${esc(sn)}
        <span class="badge ${share.security}">${esc(t("security." + share.security))}</span></td>
      <td><span class="radio-group" data-share="${esc(sn)}">${radios}</span></td></tr>`;
  }).join("");
  return `<h2>${esc(t(kind + ".accessTitle"))}</h2>
    <div class="matrix-note">${esc(t(kind + ".accessHint"))}</div>
    <table><tbody>${rows}</tbody></table>`;
}

function readShareMatrix(root) {
  const out = {};
  root.querySelectorAll(".radio-group[data-share]").forEach((g) => {
    const checked = g.querySelector("input:checked");
    if (checked) out[g.dataset.share] = checked.value;
  });
  return out;
}

function userForm(name) {
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

/* ---------- groups ---------- */

function groupsList() {
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

function groupForm(name) {
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
      if (!confirm(t("group.deleteConfirm", { name }))) return;
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

/* ---------- settings ---------- */

function settingsPage() {
  const st = S.settings;
  const svc = S.service || {};
  const protos = ["NT1", "SMB2", "SMB2_10", "SMB3", "SMB3_11"].map((p) =>
    `<option value="${p}" ${st.min_protocol === p ? "selected" : ""}>${p}</option>`).join("");
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("settings.title"))}</h1></div>
    <form class="panel" id="settings-form">
      <h2 style="margin-top:0">${esc(t("settings.samba"))}</h2>
      <div class="field"><label>${esc(t("settings.workgroup"))}</label>
        <input type="text" name="workgroup" value="${esc(st.workgroup)}" required></div>
      <div class="field"><label>${esc(t("settings.serverString"))}</label>
        <input type="text" name="server_string" value="${esc(st.server_string)}"></div>
      <div class="field"><label>${esc(t("settings.netbios"))}</label>
        <input type="text" name="netbios_name" value="${esc(st.netbios_name)}">
        <div class="hint">${esc(t("settings.netbiosHint"))}</div></div>
      <div class="field"><label>${esc(t("settings.minProtocol"))}</label>
        <select name="min_protocol">${protos}</select></div>
      <div class="actions"><button class="primary">${esc(t("common.save"))}</button></div>
    </form>
    <h2>${esc(t("settings.service"))}</h2>
    <div class="panel">
      <p><span class="svc-dot ${svc.active ? "on" : "off"}"></span>
        ${esc(t(svc.active ? "settings.running" : "settings.stopped"))}
        ${svc.version ? `<span class="mono"> — ${esc(svc.version)}</span>` : ""}</p>
      <div class="actions"><button id="svc-restart">${esc(t("settings.restart"))}</button></div>
    </div>`;
  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    const res = await guard(() => api("/settings", {
      method: "PUT",
      body: {
        workgroup: f.workgroup.value.trim(),
        server_string: f.server_string.value.trim(),
        netbios_name: f.netbios_name.value.trim(),
        min_protocol: f.min_protocol.value,
      },
    }));
    reportResult(res);
    await refreshState();
  });
  $("#svc-restart").onclick = async () => {
    await guard(() => api("/service/restart", { method: "POST" }));
    toast(t("settings.restarted"), "ok");
    await refreshState();
    settingsPage();
  };
}

/* ---------- router ---------- */

async function route() {
  if (!S) return;
  const hash = location.hash || "#/shares";
  const parts = hash.slice(2).split("/").map(decodeURIComponent);
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === parts[0]));
  const [page, action, arg] = parts;
  if (page === "shares") {
    if (action === "new") shareForm(null);
    else if (action === "edit") shareForm(arg);
    else sharesList();
  } else if (page === "pools") {
    if (action === "new") poolForm(null);
    else if (action === "edit") poolForm(arg);
    else poolsList();
  } else if (page === "users") {
    if (action === "new") userForm(null);
    else if (action === "edit") userForm(arg);
    else usersList();
  } else if (page === "groups") {
    if (action === "new") groupForm(null);
    else if (action === "edit") groupForm(arg);
    else groupsList();
  } else if (page === "settings") {
    settingsPage();
  } else {
    location.hash = "#/shares";
  }
}

async function startApp() {
  await refreshState();
  document.getElementById("topbar").hidden = false;
  if (!location.hash) location.hash = "#/shares";
  await route();
}

/* ---------- boot ---------- */

window.addEventListener("hashchange", route);

document.getElementById("lang-toggle").addEventListener("click", async () => {
  LANG = LANG === "hu" ? "en" : "hu";
  localStorage.setItem("psg_lang", LANG);
  await loadLang();
  if (S) route(); else showLogin();
});

document.getElementById("logout").addEventListener("click", async () => {
  await api("/logout", { method: "POST" }).catch(() => {});
  S = null;
  showLogin();
});

(async function boot() {
  await loadLang();
  try {
    await api("/session");
    await startApp();
  } catch {
    /* showLogin already triggered on 401 */
  }
})();
