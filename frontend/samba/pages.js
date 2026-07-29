import { groupForm, groupsList } from "./groups.js";
import { settingsPage } from "./settings.js";
import { shareForm, sharesList } from "./shares.js";
import { userForm, usersList } from "./users.js";

/** Page descriptors: id is both the nav key and the first hash segment, so
 *  #/<id>, #/<id>/new and #/<id>/edit/<name> need no per-page routing code. */
export const pages = [
  { id: "shares", navKey: "nav.shares", list: sharesList, form: shareForm },
  { id: "users", navKey: "nav.users", list: usersList, form: userForm },
  { id: "groups", navKey: "nav.groups", list: groupsList, form: groupForm },
  { id: "settings", navKey: "nav.settings", list: settingsPage },
];
