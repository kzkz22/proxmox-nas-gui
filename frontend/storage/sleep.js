import { api, guard } from "../core/api.js";
import { confirmDialog } from "../core/dialog.js";
import { $, esc, humanSize, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";
import { diskLabel, seriesStyle, temperatureChart, wireChart } from "./tempchart.js";

/** The disk sleep page: two tabs behind one route.
 *
 *  #/sleep shows the disks, #/sleep/log the event log. The router only
 *  special-cases "new" and "edit" and falls through to list() for anything
 *  else, so the second tab needs no router change - it just reads the hash. */

const DAY = 86400;
const LOG_PAGE = 25;
/** How often the live throughput readout refreshes. Cheap enough to be this
 *  frequent: the endpoint only reads /proc/diskstats, which is kernel memory
 *  and issues no I/O, so polling it can never wake a sleeping disk. */
const IO_INTERVAL = 2000;
/** Samples kept per disk for the sparkline - two minutes at the poll rate.
 *  Long enough to tell a burst from a steady stream, short enough that the
 *  shape still describes what the disk is doing now. */
const IO_SAMPLES = 60;

/** Filters of the log tab, kept across re-renders so paging does not reset
 *  what the user is looking at. */
let logFilters = { disk: "", event: "", reason: "", text: "", offset: 0 };

export async function sleepPage() {
  stopIo();
  const tab = location.hash.startsWith("#/sleep/log") ? "log"
    : location.hash.startsWith("#/sleep/temps") ? "temps" : "disks";
  const link = (id, href, key) =>
    `<a href="${href}" class="${tab === id ? "active" : ""}">${esc(t(key))}</a>`;
  view().innerHTML = `
    <div class="page-head"><h1>${esc(t("sleep.title"))}</h1></div>
    <div class="tabs">
      ${link("disks", "#/sleep", "sleep.tabDisks")}
      ${link("temps", "#/sleep/temps", "sleep.tabTemps")}
      ${link("log", "#/sleep/log", "sleep.tabLog")}
    </div>
    <div id="sleep-body">${esc(t("common.loading"))}</div>`;
  if (tab === "log") await renderLog();
  else if (tab === "temps") await renderTemps();
  else await renderDisks();
}

/* ---------------------------------------------------------------- disks -- */

async function renderDisks() {
  const box = $("#sleep-body");
  let data;
  try {
    data = await api("/sleep");
  } catch (e) {
    box.textContent = e.message;
    return;
  }
  if (!box) return;

  const s = data.summary;
  box.innerHTML = `
    ${data.hd_idle_running ? `<div class="banner">
      <span class="btxt">${esc(t("sleep.hdIdleBanner"))}</span>
      <button class="primary" id="takeover">${esc(t("sleep.takeover"))}</button>
    </div>` : ""}
    <div class="panel summary">
      <div class="stat"><span class="k">${esc(t("sleep.statDisks"))}</span><span class="v">${s.total}</span></div>
      <div class="stat"><span class="k">${esc(t("sleep.statAsleep"))}</span><span class="v">${s.asleep}<small> / ${s.total}</small></span></div>
      <div class="stat"><span class="k">${esc(t("sleep.statAsleepTime"))}</span><span class="v">${esc(duration(s.asleep_seconds))}</span></div>
      <div class="stat"><span class="k">${esc(t("sleep.statWakes"))}</span><span class="v">${s.wake_count}</span></div>
      <div class="stat"><span class="k">${esc(t("sleep.statWarnings"))}</span><span class="v${s.warnings ? " warn" : ""}">${s.warnings}</span></div>
    </div>
    ${data.disks.length
      ? data.disks.map((d) => diskCard(d, data.idle_choices)).join("")
      : `<div class="empty">${esc(t("sleep.empty"))}</div>`}
    ${data.other.length ? `<h2>${esc(t("sleep.otherTitle"))}</h2>
      <div class="panel">
        <div class="matrix-note">${esc(t("sleep.otherNote"))}</div>
        <table><tbody>${data.other.map((d) => `<tr>
          <td class="mono">${esc(d.by_id)}</td>
          <td>${esc(d.model || "")}</td>
          <td>${humanSize(d.size)}</td>
          <td><span class="badge off">${esc(t("sleep.notRotational"))}</span></td>
        </tr>`).join("")}</tbody></table>
      </div>` : ""}
    <div class="panel" id="sleep-settings" style="margin-top:1.2rem">
      ${settingsForm(data.settings, data.idle_choices, data.ssd_offset)}
    </div>`;

  wireDisks();
  wireSettings(data.settings);
  startIo();
  if (data.hd_idle_running) {
    $("#takeover").onclick = async () => {
      if (!(await confirmDialog(t("sleep.takeoverConfirm")))) return;
      const res = await guard(() => api("/sleep/takeover", { method: "POST" }));
      toast(t("sleep.takeoverDone", { count: res.imported }), "ok");
      (res.notes || []).forEach((n) => toast(n, "warn", 8000));
      sleepPage();
    };
  }
}

function diskCard(d, choices) {
  const role = [
    d.zfs_pool ? `<span class="ds">ZFS: ${esc(d.zfs_pool)}</span>` : "",
    ...d.pools.map((p) => `<span class="ds">POOL: ${esc(p)}</span>`),
    ...d.mountpoints.map((m) => `<span class="mono">${esc(m)}</span>`),
  ].filter(Boolean).join(' <span class="arrow">·</span> ');

  return `<div class="panel disk" data-disk="${esc(d.by_id)}">
    <div class="disk-top">
      <div class="disk-id">
        <div class="disk-title"><h3>${esc(d.model || d.by_id)}</h3>
          <span class="size">${humanSize(d.size)} · ${esc(d.path)}</span></div>
        <div class="byid">${esc(d.by_id)}</div>
        <div class="role">${role || `<span class="mono">${esc(t("sleep.noMount"))}</span>`}</div>
      </div>
      <div class="state-col">
        <span class="badge ${d.asleep ? "sleeping" : "awake"}">● ${esc(t(d.asleep ? "sleep.stateAsleep" : "sleep.stateAwake"))}</span>
        ${d.since ? `<span class="since">${esc(t("sleep.since", { time: duration(nowSeconds() - d.since) }))}</span>` : ""}
        ${d.state === "unknown" ? `<span class="since">${esc(t("sleep.stateUnknown"))}</span>` : ""}
        ${tempBadge(d.temperature)}
        <span class="io" data-io="${esc(d.by_id)}" data-io-asleep="${d.asleep ? "1" : "0"}"></span>
      </div>
    </div>
    ${timelineHtml(d.timeline)}
    <div class="disk-ctl">
      <span class="lbl">${esc(t("sleep.idleLabel"))}</span>
      <select data-idle="${esc(d.by_id)}">
        ${choices.map((c) => `<option value="${c}" ${c === d.idle_seconds ? "selected" : ""}>${esc(idleLabel(c))}</option>`).join("")}
      </select>
      <span class="spacer"></span>
      <button class="small" data-log="${esc(d.by_id)}">${esc(t("sleep.openLog"))}</button>
      <button class="small ${d.asleep ? "" : "primary"}" data-spindown="${esc(d.by_id)}" ${d.asleep ? "disabled" : ""}>
        ${esc(t("sleep.spinDownNow"))}</button>
    </div>
    ${d.warnings.length ? warningsHtml(d) : ""}
  </div>`;
}

/** The card's temperature. A sleeping disk keeps its last known reading with
 *  the age attached: "22 °C · 3 órája" says far more than a dash, and the age
 *  is what stops it being read as live. */
function tempBadge(temp) {
  if (!temp || temp.celsius === null) return "";
  const age = nowSeconds() - temp.temp_at;
  const stale = age > 900;
  return `<span class="temp ${esc(temp.level)}${stale ? " stale" : ""}">
    ${temp.celsius} °C${stale ? ` <span class="temp-age">· ${esc(duration(age))}</span>` : ""}
  </span>`;
}

function timelineHtml(segments) {
  if (!segments || !segments.length) return "";
  const start = segments[0].start;
  const end = segments[segments.length - 1].end;
  const span = Math.max(1, end - start);
  const bars = segments.map((seg) =>
    `<i class="${esc(seg.state)}" style="width:${((seg.end - seg.start) / span) * 100}%"></i>`).join("");
  return `<div class="tl-wrap"><div class="tl">${bars}</div>
    <div class="tl-ax"><span>${esc(t("sleep.timelineStart"))}</span>
      <span>${esc(t("sleep.timelineEnd"))}</span></div></div>`;
}

function warningsHtml(d) {
  const rows = d.warnings.map((w) => `
    <div class="wrow">
      <div class="wsev ${esc(w.severity)}"></div>
      <div class="wbody">
        <h3>${esc(t(`sleepwarn.${w.id}.title`, w.vars))}</h3>
        <p>${esc(t(`sleepwarn.${w.id}.body`, w.vars))}</p>
        ${w.command ? `<div class="cmd">${esc(w.command)}</div>` : ""}
        <div class="wact">
          ${w.fixable
            ? `<button class="small primary" data-fix="${esc(w.id)}" data-fix-disk="${esc(d.by_id)}">${esc(t("sleep.runFix"))}</button>`
            : w.command ? `<button class="small" data-copy="${esc(w.command)}">${esc(t("sleep.copyCommand"))}</button>` : ""}
          <span class="why">${esc(t(`sleepwarn.${w.id}.why`, w.vars))}</span>
        </div>
      </div>
    </div>`).join("");
  return `<details class="warnbox">
    <summary class="warnline">⚠ ${esc(t("sleep.warnCount", { count: d.warnings.length }))}</summary>
    ${rows}
  </details>`;
}

function wireDisks() {
  view().querySelectorAll("[data-idle]").forEach((sel) => sel.addEventListener("change", async () => {
    await guard(() => api(`/sleep/policy/${encodeURIComponent(sel.dataset.idle)}`, {
      method: "PUT", body: { idle_seconds: Number(sel.value) },
    }));
    toast(t("common.saved"), "ok");
  }));

  view().querySelectorAll("[data-spindown]").forEach((btn) => btn.addEventListener("click", async () => {
    const id = btn.dataset.spindown;
    if (!(await confirmDialog(t("sleep.spinDownConfirm", { disk: id })))) return;
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${esc(t("sleep.spinningDown"))}`;
    try {
      const res = await guard(() => api(`/sleep/spindown/${encodeURIComponent(id)}`, { method: "POST" }));
      toast(t("sleep.spunDown", { method: res.method }), "ok");
      sleepPage();
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }));

  view().querySelectorAll("[data-log]").forEach((btn) => btn.addEventListener("click", () => {
    logFilters = { disk: btn.dataset.log, event: "", reason: "", text: "", offset: 0 };
    location.hash = "#/sleep/log";
  }));

  view().querySelectorAll("[data-fix]").forEach((btn) => btn.addEventListener("click", async () => {
    if (!(await confirmDialog(t(`sleepwarn.${btn.dataset.fix}.confirm`)))) return;
    const res = await guard(() => api("/sleep/fix", {
      method: "POST", body: { disk: btn.dataset.fixDisk, check: btn.dataset.fix },
    }));
    toast(res.detail, "ok", 6000);
    sleepPage();
  }));

  view().querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      toast(t("sleep.copied"), "ok");
    } catch {
      toast(btn.dataset.copy, "warn", 10000);
    }
  }));
}

/* ------------------------------------------------------------ live I/O -- */

/** Previous counter sample, so a rate can be derived from two of them. */
let ioPrevious = null;
let ioTimer = null;
/** by-id -> [[read, write], ...], newest last. */
const ioHistory = new Map();

function stopIo() {
  if (ioTimer) clearInterval(ioTimer);
  ioTimer = null;
  ioPrevious = null;
  // The history goes too. Keeping it across a pause would draw the gap as a
  // continuous line, which is a lie about what the disk was doing.
  ioHistory.clear();
}

function startIo() {
  stopIo();
  pollIo();
  ioTimer = setInterval(pollIo, IO_INTERVAL);
}

async function pollIo() {
  // Self-cleaning rather than unregistered on navigation: the router only
  // replaces the view's markup, so a timer left running would keep polling
  // from every page the user visits afterwards.
  if (!location.hash.startsWith("#/sleep") || !document.querySelector("[data-io]")) {
    stopIo();
    return;
  }
  if (document.visibilityState === "hidden") {
    // Background tabs are throttled, so keeping the baseline would spread the
    // next delta over however long the user was away and report it as a rate.
    ioPrevious = null;
    return;
  }

  let data;
  try {
    data = await api("/sleep/io");
  } catch {
    // A dropped sample is not worth a toast; the next tick retries. Clearing
    // the baseline stops the gap from being reported as a huge burst.
    ioPrevious = null;
    return;
  }

  const previous = ioPrevious;
  ioPrevious = data;
  if (!previous) return;
  const seconds = data.ts - previous.ts;
  if (seconds <= 0) return;

  document.querySelectorAll("[data-io]").forEach((el) => {
    const now = data.disks[el.dataset.io];
    const before = previous.disks[el.dataset.io];
    if (!now || !before) { el.textContent = ""; return; }
    const read = Math.max(0, now.read_bytes - before.read_bytes) / seconds;
    const write = Math.max(0, now.write_bytes - before.write_bytes) / seconds;
    const history = remember(el.dataset.io, read, write);
    // A disk with no traffic at all says nothing rather than blinking zeroes
    // at the user - the state badge above it already says "asleep".
    if (read < 1 && write < 1 && el.dataset.ioAsleep === "1") {
      el.textContent = "";
      return;
    }
    el.innerHTML =
      sparkline(history) +
      `<span class="io-n"><i class="io-r">↓</i> ${esc(rate(read))}</span>` +
      `<span class="io-n"><i class="io-w">↑</i> ${esc(rate(write))}</span>`;
  });
}

function remember(byId, read, write) {
  const history = ioHistory.get(byId) || [];
  history.push([read, write]);
  if (history.length > IO_SAMPLES) history.splice(0, history.length - IO_SAMPLES);
  ioHistory.set(byId, history);
  return history;
}

/** A mirrored sparkline: reads above the centre line, writes below.
 *
 *  Mirroring rather than two overlaid lines because at this size overlapping
 *  fills would hide each other, and the read/write balance is most of what
 *  the shape is for - a steady trickle of writes looks nothing like the burst
 *  of a backup or a scrub.
 *
 *  Both halves share one scale, deliberately. Giving each series its own
 *  maximum would make a 900 B/s write look the same size as a 48 MiB/s read,
 *  which is the dual-axis mistake drawn small.
 *
 *  The scale is the window's own peak, so the shape always fills the box; the
 *  numbers beside it and the peak in the tooltip carry the magnitude, which
 *  is the division of labour a sparkline is for.
 */
function sparkline(history) {
  if (history.length < 2) return "";
  const peakRead = Math.max(...history.map((s) => s[0]));
  const peakWrite = Math.max(...history.map((s) => s[1]));
  const peak = Math.max(peakRead, peakWrite);
  if (peak < 1) return "";

  const W = 78, H = 26, mid = H / 2, amp = mid - 1.5;
  // Anchored to the right edge, so a partly filled buffer grows leftwards and
  // the newest sample is always in the same place.
  const step = W / (IO_SAMPLES - 1);
  const x = (i) => W - (history.length - 1 - i) * step;
  const area = (pick, sign) => {
    const points = history
      .map((s, i) => `${x(i).toFixed(1)},${(mid + sign * (pick(s) / peak) * amp).toFixed(1)}`)
      .join(" L");
    return `M${x(0).toFixed(1)},${mid} L${points} L${x(history.length - 1).toFixed(1)},${mid}Z`;
  };
  const last = history[history.length - 1];
  const tip = mid - (last[0] / peak) * amp;

  return `<svg class="spark" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"
      role="img" aria-label="${esc(t("sleep.sparkAria", {
        window: Math.round((IO_SAMPLES * IO_INTERVAL) / 1000 / 60),
        read: rate(peakRead), write: rate(peakWrite),
      }))}">
    <title>${esc(t("sleep.sparkTitle", {
      window: Math.round((IO_SAMPLES * IO_INTERVAL) / 1000 / 60),
      read: rate(peakRead), write: rate(peakWrite),
    }))}</title>
    <path class="spark-r" d="${area((s) => s[0], -1)}"/>
    <path class="spark-w" d="${area((s) => s[1], 1)}"/>
    <line class="spark-mid" x1="0" y1="${mid}" x2="${W}" y2="${mid}"/>
    <circle class="spark-tip" cx="${W}" cy="${tip.toFixed(1)}" r="1.8"/>
  </svg>`;
}

/** Reuses humanSize so the units match the rest of the application (KiB,
 *  MiB), rather than introducing a second, decimal convention here. */
function rate(bytesPerSecond) {
  return `${humanSize(bytesPerSecond)}/s`;
}

/* ------------------------------------------------------------- settings -- */

function settingsForm(settings, choices, ssdOffset = 20) {
  return `<h2 style="margin-top:0">${esc(t("sleep.settingsTitle"))}</h2>
    <div class="field"><label class="check">
      <input type="checkbox" id="set-enabled" ${settings.enabled ? "checked" : ""}>
      ${esc(t("sleep.settingEnabled"))}</label>
      <div class="hint">${esc(t("sleep.settingEnabledHint"))}</div></div>
    <div class="field"><label>${esc(t("sleep.settingPoll"))}</label>
      <select id="set-poll">${[15, 30, 60, 120].map((v) =>
        `<option value="${v}" ${v === settings.poll_seconds ? "selected" : ""}>${v} ${esc(t("sleep.secondsWord"))}</option>`).join("")}</select>
      <div class="hint">${esc(t("sleep.settingPollHint"))}</div></div>
    <div class="field"><label>${esc(t("sleep.settingDefault"))}</label>
      <select id="set-default">${choices.map((c) =>
        `<option value="${c}" ${c === settings.default_idle_seconds ? "selected" : ""}>${esc(idleLabel(c))}</option>`).join("")}</select>
      <div class="hint">${esc(t("sleep.settingDefaultHint"))}</div></div>
    <div class="field"><label>${esc(t("sleep.settingRetention"))}</label>
      <select id="set-retention">${[30, 90, 180, 365].map((v) =>
        `<option value="${v}" ${v === settings.retention_days ? "selected" : ""}>${v} ${esc(t("sleep.daysWord"))}</option>`).join("")}</select>
      <div class="hint">${esc(t("sleep.settingRetentionHint"))}</div></div>
    <h2>${esc(t("sleep.tabTemps"))}</h2>
    <div class="field"><label class="check">
      <input type="checkbox" id="set-temp" ${settings.temp_enabled ? "checked" : ""}>
      ${esc(t("sleep.settingTempEnabled"))}</label>
      <div class="hint">${esc(t("sleep.settingTempEnabledHint"))}</div></div>
    <div class="field"><label>${esc(t("sleep.settingTempInterval"))}</label>
      <select id="set-temp-interval">${[300, 600, 900, 1800].map((v) =>
        `<option value="${v}" ${v === settings.temp_interval_seconds ? "selected" : ""}
          >${v / 60} ${esc(t("sleep.minutesWord"))}</option>`).join("")}</select></div>
    <div class="field"><label>${esc(t("sleep.settingTempRetention"))}</label>
      <select id="set-temp-retention">${[90, 180, 365, 730].map((v) =>
        `<option value="${v}" ${v === settings.temp_retention_days ? "selected" : ""}
          >${v} ${esc(t("sleep.daysWord"))}</option>`).join("")}</select>
      <div class="hint">${esc(t("sleep.settingTempRetentionHint"))}</div></div>
    <div class="field-row">
      <div class="field" style="margin:0"><label>${esc(t("sleep.settingTempWarn"))}</label>
        <select id="set-temp-warn">${[35, 40, 45, 50].map((v) =>
          `<option value="${v}" ${v === settings.temp_warn_celsius ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div class="field" style="margin:0"><label>${esc(t("sleep.settingTempCrit"))}</label>
        <select id="set-temp-crit">${[50, 55, 60, 65].map((v) =>
          `<option value="${v}" ${v === settings.temp_crit_celsius ? "selected" : ""}>${v}</option>`).join("")}</select></div>
    </div>
    <div class="hint">${esc(t("sleep.settingTempThresholdHint", { offset: ssdOffset }))}</div>
    <div class="actions"><span class="spacer"></span>
      <button class="primary" id="save-settings">${esc(t("common.save"))}</button></div>`;
}

function wireSettings(settings) {
  $("#save-settings").onclick = async () => {
    await guard(() => api("/sleep/settings", {
      method: "PUT",
      body: {
        ...settings,
        enabled: $("#set-enabled").checked,
        poll_seconds: Number($("#set-poll").value),
        default_idle_seconds: Number($("#set-default").value),
        retention_days: Number($("#set-retention").value),
        temp_enabled: $("#set-temp").checked,
        temp_interval_seconds: Number($("#set-temp-interval").value),
        temp_retention_days: Number($("#set-temp-retention").value),
        temp_warn_celsius: Number($("#set-temp-warn").value),
        temp_crit_celsius: Number($("#set-temp-crit").value),
      },
    }));
    toast(t("common.saved"), "ok");
  };
}

/* ---------------------------------------------------------- temperature -- */

/** Kept across re-renders, like the log filters. */
let tempWindow = "24h";
let tempDisk = "";

async function renderTemps() {
  const box = $("#sleep-body");
  let current, history;
  try {
    const params = new URLSearchParams({ window: tempWindow });
    if (tempDisk) params.set("disk", tempDisk);
    [current, history] = await Promise.all([
      api("/sleep/temps"),
      api(`/sleep/temps/history?${params}`),
    ]);
  } catch (e) {
    box.textContent = e.message;
    return;
  }
  if (!box) return;

  // The colour order comes from the full disk list, never from the filtered
  // subset: narrowing to one disk must not repaint the others.
  const order = current.disks.map((d) => ({ by_id: d.by_id, model: d.model }));
  const windows = ["24h", "7d", "30d", "1y"];

  box.innerHTML = `
    <div class="panel summary">
      ${current.disks.map((d) => `<div class="stat">
        <span class="k">${esc(diskLabel(d.by_id, d.model))}${d.system ? ` · ${esc(t("temp.systemDisk"))}` : ""}</span>
        <span class="v${d.level && d.level !== "ok" ? " " + esc(d.level) : ""}">
          ${d.celsius === null ? "—" : `${d.celsius} °C`}</span>
      </div>`).join("")}
    </div>

    <div class="panel filters">
      <div class="field"><label>${esc(t("temp.window"))}</label>
        <div class="radio-group">${windows.map((w) => `
          <label><input type="radio" name="tw" value="${w}" ${w === tempWindow ? "checked" : ""}>
            ${esc(t("temp.window." + w))}</label>`).join("")}</div></div>
      <div class="field"><label>${esc(t("sleep.filterDisk"))}</label>
        <select id="t-disk"><option value="">${esc(t("sleep.filterAll"))}</option>
          ${order.map((d) => `<option value="${esc(d.by_id)}" ${d.by_id === tempDisk ? "selected" : ""}
            >${esc(diskLabel(d.by_id, d.model))}</option>`).join("")}
        </select></div>
      <span class="spacer"></span>
      <button id="t-csv">${esc(t("temp.csv"))}</button>
    </div>

    <div class="panel" id="t-chart">${temperatureChart(history, order)}</div>

    <h2>${esc(t("temp.statsTitle", { window: t("temp.window." + tempWindow) }))}</h2>
    ${Object.keys(history.stats).length ? `<table><thead><tr>
        <th>${esc(t("sleep.colDisk"))}</th><th>${esc(t("temp.min"))}</th>
        <th>${esc(t("temp.avg"))}</th><th>${esc(t("temp.max"))}</th>
        <th>${esc(t("temp.samples"))}</th></tr></thead>
      <tbody>${order.filter((d) => history.stats[d.by_id]).map((d, i) => {
        const s = history.stats[d.by_id];
        const style = seriesStyle(order.findIndex((o) => o.by_id === d.by_id));
        return `<tr>
          <td><i class="swatch" style="background:${style.color}"></i>
            ${esc(diskLabel(d.by_id, d.model))}</td>
          <td class="num">${s.min} °C</td><td class="num">${s.avg} °C</td>
          <td class="num">${s.max} °C</td><td class="num">${s.samples}</td>
        </tr>`;
      }).join("")}</tbody></table>`
      : `<div class="empty">${esc(t("temp.chartEmpty"))}</div>`}
    <div class="matrix-note" style="margin-top:.7rem">${esc(t("temp.gapNote"))}</div>`;

  wireChart($("#t-chart"), history, order);
  box.querySelectorAll('input[name="tw"]').forEach((r) =>
    r.addEventListener("change", () => { tempWindow = r.value; renderTemps(); }));
  $("#t-disk").addEventListener("change", (ev) => {
    tempDisk = ev.target.value;
    renderTemps();
  });
  $("#t-csv").onclick = () => {
    const params = new URLSearchParams({ window: tempWindow, format: "csv" });
    if (tempDisk) params.set("disk", tempDisk);
    window.location.href = `/api/sleep/temps/history?${params}`;
  };
}

/* ------------------------------------------------------------------ log -- */

async function renderLog() {
  const box = $("#sleep-body");
  const params = new URLSearchParams({ limit: String(LOG_PAGE), offset: String(logFilters.offset) });
  ["disk", "event", "reason", "text"].forEach((k) => { if (logFilters[k]) params.set(k, logFilters[k]); });
  let data;
  try {
    data = await api(`/sleep/events?${params}`);
  } catch (e) {
    box.textContent = e.message;
    return;
  }
  if (!box) return;

  const from = data.total ? logFilters.offset + 1 : 0;
  const to = Math.min(logFilters.offset + LOG_PAGE, data.total);
  box.innerHTML = `
    <div class="panel filters">
      <div class="field"><label>${esc(t("sleep.filterDisk"))}</label>
        <select id="f-disk"><option value="">${esc(t("sleep.filterAll"))}</option>
          ${data.disks.map((d) => `<option value="${esc(d)}" ${d === logFilters.disk ? "selected" : ""}>${esc(d)}</option>`).join("")}
        </select></div>
      <div class="field"><label>${esc(t("sleep.filterEvent"))}</label>
        <select id="f-event"><option value="">${esc(t("sleep.filterAll"))}</option>
          ${["sleep", "wake", "sleep_failed"].map((v) =>
            `<option value="${v}" ${v === logFilters.event ? "selected" : ""}>${esc(t("sleep.event." + v))}</option>`).join("")}
        </select></div>
      <div class="field"><label>${esc(t("sleep.filterReason"))}</label>
        <select id="f-reason"><option value="">${esc(t("sleep.filterAll"))}</option>
          ${["timeout", "manual", "external"].map((v) =>
            `<option value="${v}" ${v === logFilters.reason ? "selected" : ""}>${esc(t("sleep.reason." + v))}</option>`).join("")}
        </select></div>
      <div class="field"><label>${esc(t("sleep.filterText"))}</label>
        <input type="text" id="f-text" value="${esc(logFilters.text)}"></div>
      <button class="primary" id="f-apply">${esc(t("sleep.filterApply"))}</button>
      <button id="f-clear">${esc(t("sleep.filterClear"))}</button>
    </div>
    ${data.events.length ? `<table><thead><tr>
        <th>${esc(t("sleep.colTime"))}</th><th>${esc(t("sleep.colDisk"))}</th>
        <th>${esc(t("sleep.colEvent"))}</th><th>${esc(t("sleep.colReason"))}</th>
        <th>${esc(t("sleep.colDetail"))}</th></tr></thead>
      <tbody>${data.events.map(eventRow).join("")}</tbody></table>
      <div class="pager"><span class="num">${esc(t("sleep.pageInfo", { from, to, total: data.total }))}</span>
        <span class="spacer"></span>
        <button class="small" id="p-prev" ${logFilters.offset === 0 ? "disabled" : ""}>← ${esc(t("sleep.prev"))}</button>
        <button class="small" id="p-next" ${to >= data.total ? "disabled" : ""}>${esc(t("sleep.next"))} →</button>
      </div>`
      : `<div class="empty">${esc(t("sleep.logEmpty"))}</div>`}`;

  $("#f-apply").onclick = () => {
    logFilters = {
      disk: $("#f-disk").value, event: $("#f-event").value,
      reason: $("#f-reason").value, text: $("#f-text").value.trim(), offset: 0,
    };
    renderLog();
  };
  $("#f-clear").onclick = () => {
    logFilters = { disk: "", event: "", reason: "", text: "", offset: 0 };
    renderLog();
  };
  const prev = $("#p-prev");
  const next = $("#p-next");
  if (prev) prev.onclick = () => { logFilters.offset = Math.max(0, logFilters.offset - LOG_PAGE); renderLog(); };
  if (next) next.onclick = () => { logFilters.offset += LOG_PAGE; renderLog(); };
}

function eventRow(row) {
  const badge = { sleep: "sleeping", wake: "awake", sleep_failed: "off" }[row.event] || "off";
  return `<tr>
    <td class="mono num">${esc(timestamp(row.ts))}</td>
    <td class="mono">${esc(row.disk)}</td>
    <td><span class="badge ${badge}">${esc(t("sleep.event." + row.event))}</span></td>
    <td class="why-cell">${esc(t("sleep.reason." + row.reason))}${row.actor ? ` — ${esc(row.actor)}` : ""}</td>
    <td class="mono">${esc(detailText(row.detail))}</td>
  </tr>`;
}

/** The monitor stores the I/O that accompanied a wake-up in a machine form,
 *  because the log is also read with sqlite3 from a shell. Only the display
 *  is translated - "0 reads, 96 writes" is the single most useful line in the
 *  whole log, since a wake-up that is pure writes is never someone browsing. */
function detailText(detail) {
  if (!detail) return "—";
  const io = detail.match(/^reads=(\d+) writes=(\d+)$/);
  if (io) return t("sleep.ioDetail", { reads: io[1], writes: io[2] });
  return detail;
}

/* -------------------------------------------------------------- helpers -- */

function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}

function idleLabel(seconds) {
  return seconds === 0 ? t("sleep.never") : t("sleep.idle." + seconds);
}

function duration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const days = Math.floor(total / DAY);
  const hours = Math.floor((total % DAY) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const parts = [];
  if (days) parts.push(`${days} ${t("sleep.daysShort")}`);
  if (hours) parts.push(`${hours} ${t("sleep.hoursShort")}`);
  if (minutes || !parts.length) parts.push(`${minutes} ${t("sleep.minutesShort")}`);
  return parts.join(" ");
}

function timestamp(ts) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
