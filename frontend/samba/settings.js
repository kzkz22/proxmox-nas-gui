import { api, guard, refreshState, S } from "../core/api.js";
import { $, esc, reportResult, toast, view } from "../core/dom.js";
import { t } from "../core/i18n.js";

export function settingsPage() {
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
