import { S } from "../core/api.js";
import { esc } from "../core/dom.js";
import { t } from "../core/i18n.js";

/** The permission matrix, rendered from both directions: on a share it lists
 *  the accounts, on an account it lists the shares. Same data, two views. */

export function accessLevels(security) {
  return security === "secure" ? ["read", "write"] : ["no", "read", "write"];
}

export function matrixRow(kind, name, level, levels, extra = "") {
  const radios = levels.map((lv) => `
    <label><input type="radio" name="acc-${kind}-${esc(name)}" value="${lv}" ${lv === level ? "checked" : ""}>
      ${esc(t("access." + lv))}</label>`).join("");
  return `<tr>
    <td>${kind === "group" ? "👥 " : "👤 "}${esc(name)}${extra}</td>
    <td><span class="radio-group" data-kind="${kind}" data-name="${esc(name)}">${radios}</span></td>
  </tr>`;
}

export function readMatrix(root) {
  const out = { user: {}, group: {} };
  root.querySelectorAll(".radio-group[data-kind]").forEach((g) => {
    const checked = g.querySelector("input:checked");
    if (checked) out[g.dataset.kind][g.dataset.name] = checked.value;
  });
  return out;
}

export function accountShareMatrix(kind, accountName) {
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

export function readShareMatrix(root) {
  const out = {};
  root.querySelectorAll(".radio-group[data-share]").forEach((g) => {
    const checked = g.querySelector("input:checked");
    if (checked) out[g.dataset.share] = checked.value;
  });
  return out;
}
