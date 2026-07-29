import { refreshState, S } from "./core/api.js";
import { groupForm, groupsList } from "./samba/groups.js";
import { settingsPage } from "./samba/settings.js";
import { shareForm, sharesList } from "./samba/shares.js";
import { userForm, usersList } from "./samba/users.js";
import { poolForm, poolsList } from "./storage/pools.js";

export async function route() {
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

export async function startApp() {
  await refreshState();
  document.getElementById("topbar").hidden = false;
  if (!location.hash) location.hash = "#/shares";
  await route();
}
