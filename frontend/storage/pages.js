import { bindForm, bindsList } from "./binds.js";
import { poolForm, poolsList } from "./pools.js";
import { sleepPage } from "./sleep.js";

export const pages = [
  { id: "pools", navKey: "nav.pools", list: poolsList, form: poolForm },
  { id: "binds", navKey: "nav.binds", list: bindsList, form: bindForm },
  // No form: the sleep page has no per-object editor, and its second tab
  // rides on the hash the router leaves alone.
  { id: "sleep", navKey: "nav.sleep", list: sleepPage },
];
