import { diagList } from "./diagnostics.js";

export const pages = [
  // No form: findings are surfaced and fixed in place, never edited as
  // their own object.
  { id: "diag", navKey: "nav.diag", list: diagList },
];
