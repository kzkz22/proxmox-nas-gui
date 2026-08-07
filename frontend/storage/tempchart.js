import { esc } from "../core/dom.js";
import { t } from "../core/i18n.js";

/** The temperature history chart.
 *
 *  Four hues, in a fixed order, assigned from the full sorted disk list - not
 *  from whatever subset is currently filtered in. Colour follows the disk, so
 *  narrowing to one disk never repaints the others.
 *
 *  Four and no more because that is what the surface allows: on this dark
 *  panel a fifth hue either drops below the chroma floor or collides with one
 *  of these under protanopia. Validated on #1f1e27 - the worst adjacent pair
 *  is green/orange at deltaE 7.8 for deuteranopia, which the palette rules
 *  permit only alongside secondary encoding, so every line also carries a
 *  legend entry, a label at its right end, and its name in the tooltip.
 *  Disks beyond the fourth reuse the hues with a dashed stroke, which is a
 *  hue-and-dash pair rather than a recycled hue. */
export const SERIES_COLORS = ["#58a6ff", "#ff8c2f", "#6fcf7f", "#f778ba"];

const W = 900, H = 300;
const PAD = { top: 12, right: 118, bottom: 26, left: 40 };
/** End labels are truncated rather than allowed to run past the viewBox: the
 *  SVG scales to the panel width, so an overflowing label is clipped, not
 *  wrapped. */
const END_LABEL_MAX = 12;
const END_LABEL_GAP = 12;
const PLOT = { w: W - PAD.left - PAD.right, h: H - PAD.top - PAD.bottom };

export function seriesStyle(index) {
  return {
    color: SERIES_COLORS[index % SERIES_COLORS.length],
    dashed: index >= SERIES_COLORS.length,
  };
}

/** Disk label: the model alone doesn't identify a disk - two drives of the
 *  same model differ only in the serial number carried by the by-id name,
 *  so the full by-id name is always shown alongside it (as on the other
 *  storage screens), never just the model. Single-line form, for spots
 *  that can't lay out two lines (a <select> option, an SVG <text>, the
 *  aria-label). */
export function diskLabel(byId, model) {
  return model ? `${model} (${byId})` : byId;
}

/** Same identity, split for a two-line layout: model on top, full by-id
 *  underneath in a dimmer, smaller line. No id line when there's no model
 *  to distinguish it from - the name already is the by-id then. */
export function diskLabelParts(byId, model) {
  return { name: model || byId, id: model ? byId : "" };
}

function shortLabel(byId, model) {
  const full = diskLabel(byId, model);
  return full.length > END_LABEL_MAX ? `${full.slice(0, END_LABEL_MAX - 1)}…` : full;
}

/** Nudge labels apart so two lines ending at similar temperatures do not
 *  print on top of each other. */
function spread(entries) {
  const sorted = [...entries].sort((a, b) => a.y - b.y);
  for (let i = 1; i < sorted.length; i++) {
    const gap = sorted[i].y - sorted[i - 1].y;
    if (gap < END_LABEL_GAP) sorted[i].y = sorted[i - 1].y + END_LABEL_GAP;
  }
  return sorted;
}

function niceTicks(lo, hi) {
  const span = Math.max(1, hi - lo);
  const step = span <= 10 ? 2 : span <= 25 ? 5 : 10;
  const start = Math.floor(lo / step) * step;
  const ticks = [];
  for (let v = start; v <= hi + step; v += step) ticks.push(v);
  return ticks;
}

function timeLabel(ts, windowSeconds) {
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  if (windowSeconds <= 2 * 86400) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return `${d.getMonth() + 1}.${pad(d.getDate())}.`;
}

/**
 * @param {object} data  the /sleep/temps/history payload
 * @param {Array}  order the full, stable disk order: [{by_id, model}, ...]
 */
export function temperatureChart(data, order) {
  const series = order
    .map((d, i) => ({ ...d, points: data.series[d.by_id] || [], ...seriesStyle(i) }))
    .filter((s) => s.points.length);
  if (!series.length) {
    return `<div class="empty">${esc(t("temp.chartEmpty"))}</div>`;
  }

  const values = series.flatMap((s) => s.points.map((p) => p[1]));
  const lo = Math.min(...values), hi = Math.max(...values);
  const ticks = niceTicks(lo - 1, hi + 1);
  const yLo = ticks[0], yHi = ticks[ticks.length - 1];
  const x = (ts) => PAD.left + ((ts - data.since) / (data.until - data.since)) * PLOT.w;
  const y = (c) => PAD.top + PLOT.h - ((c - yLo) / Math.max(1, yHi - yLo)) * PLOT.h;

  // A gap wider than a couple of buckets is a stretch with no samples, which
  // means the disk was asleep. Breaking the path there is the point: joining
  // across it would draw a temperature the disk never had.
  const gapLimit = data.bucket * 2.5;
  const pathFor = (points) => {
    let d = "", previous = null;
    for (const [ts, c] of points) {
      d += (previous === null || ts - previous > gapLimit ? "M" : "L");
      d += `${x(ts).toFixed(1)},${y(c).toFixed(1)}`;
      previous = ts;
    }
    return d;
  };

  const grid = ticks.map((v) => `
    <line class="c-grid" x1="${PAD.left}" y1="${y(v).toFixed(1)}"
          x2="${PAD.left + PLOT.w}" y2="${y(v).toFixed(1)}"/>
    <text class="c-tick" x="${PAD.left - 6}" y="${(y(v) + 4).toFixed(1)}"
          text-anchor="end">${v}</text>`).join("");

  const threshold = (value, cls, key) =>
    value > yLo && value < yHi
      ? `<line class="c-thr ${cls}" x1="${PAD.left}" y1="${y(value).toFixed(1)}"
              x2="${PAD.left + PLOT.w}" y2="${y(value).toFixed(1)}"/>
         <text class="c-thr-label ${cls}" x="${PAD.left + 4}"
               y="${(y(value) - 4).toFixed(1)}">${esc(t(key))}</text>`
      : "";

  const steps = 5;
  const xLabels = Array.from({ length: steps + 1 }, (_, i) => {
    const ts = data.since + ((data.until - data.since) * i) / steps;
    return `<text class="c-tick" x="${x(ts).toFixed(1)}" y="${H - 8}"
             text-anchor="${i === 0 ? "start" : i === steps ? "end" : "middle"}"
             >${esc(timeLabel(ts, data.until - data.since))}</text>`;
  }).join("");

  const lines = series.map((s) => `<path class="c-line${s.dashed ? " dashed" : ""}"
      style="stroke:${s.color}" d="${pathFor(s.points)}"/>`).join("");

  // Direct labels at the right end, so identity does not rest on the legend
  // alone - or on colour alone, which the green/orange pair requires.
  const endLabels = spread(series.map((s) => ({
    s, y: y(s.points[s.points.length - 1][1]) + 4,
  }))).map(({ s, y: ly }) => `<text class="c-end" x="${PAD.left + PLOT.w + 6}"
      y="${ly.toFixed(1)}" style="fill:${s.color}"
      >${esc(shortLabel(s.by_id, s.model))}</text>`).join("");

  return `
    <div class="chart-wrap">
      <svg class="chart" viewBox="0 0 ${W} ${H}"
           role="img" aria-label="${esc(t("temp.chartAria", { count: series.length }))}">
        ${grid}
        ${threshold(data.warn_celsius, "warn", "temp.thresholdWarn")}
        ${threshold(data.crit_celsius, "crit", "temp.thresholdCrit")}
        ${xLabels}
        ${lines}
        ${endLabels}
        <line class="c-cross" x1="0" y1="${PAD.top}" x2="0" y2="${PAD.top + PLOT.h}" hidden/>
        <rect class="c-hit" x="${PAD.left}" y="${PAD.top}"
              width="${PLOT.w}" height="${PLOT.h}" fill="transparent"/>
      </svg>
      <div class="chart-tip" hidden></div>
    </div>
    <div class="legend">${series.map((s) => `
      <span class="legend-item"><i style="background:${s.color}"
        class="${s.dashed ? "dashed" : ""}"></i>${esc(diskLabel(s.by_id, s.model))}</span>`).join("")}
    </div>`;
}

/** Crosshair and tooltip. An SVG chart on a page is interactive by default;
 *  reading a value off a 500-point line otherwise means guessing. */
export function wireChart(root, data, order) {
  const svg = root.querySelector("svg.chart");
  if (!svg) return;
  const cross = svg.querySelector(".c-cross");
  const tip = root.querySelector(".chart-tip");
  const hit = svg.querySelector(".c-hit");

  const series = order
    .map((d, i) => ({ ...d, points: data.series[d.by_id] || [], ...seriesStyle(i) }))
    .filter((s) => s.points.length);

  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const ratio = (ev.clientX - box.left) / box.width;
    const svgX = ratio * W;
    const ts = data.since + ((svgX - PAD.left) / PLOT.w) * (data.until - data.since);

    const rows = series.map((s) => {
      const nearest = s.points.reduce((best, p) =>
        Math.abs(p[0] - ts) < Math.abs(best[0] - ts) ? p : best, s.points[0]);
      // Nothing within a couple of buckets means the disk was asleep here.
      if (Math.abs(nearest[0] - ts) > data.bucket * 2.5) return null;
      return { label: diskLabel(s.by_id, s.model), color: s.color, value: nearest[1], at: nearest[0] };
    }).filter(Boolean);

    if (!rows.length) { tip.hidden = true; cross.setAttribute("hidden", ""); return; }

    cross.removeAttribute("hidden");
    cross.setAttribute("x1", svgX);
    cross.setAttribute("x2", svgX);
    tip.hidden = false;
    tip.innerHTML =
      `<div class="tip-time">${esc(new Date(rows[0].at * 1000).toLocaleString())}</div>` +
      rows.map((r) => `<div class="tip-row"><i style="background:${r.color}"></i>
        <span class="tip-name">${esc(r.label)}</span>
        <b>${r.value.toFixed(1)} °C</b></div>`).join("");
    // Flip to the left of the cursor near the right edge, so the tooltip
    // never pushes the page sideways.
    const left = ev.clientX - box.left;
    tip.style.left = `${left > box.width - 200 ? left - 210 : left + 14}px`;
  });

  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("hidden", "");
  });
}
