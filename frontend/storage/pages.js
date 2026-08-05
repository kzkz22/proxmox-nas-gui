import { bindForm, bindsList } from "./binds.js";
import { poolForm, poolsList } from "./pools.js";

export const pages = [
  { id: "pools", navKey: "nav.pools", list: poolsList, form: poolForm },
  { id: "binds", navKey: "nav.binds", list: bindsList, form: bindForm },
];
