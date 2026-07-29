import { pages as sambaPages } from "./samba/pages.js";
import { pages as storagePages } from "./storage/pages.js";

const byId = Object.fromEntries(
  [...sambaPages, ...storagePages].map((page) => [page.id, page])
);

/** Nav order is a product decision, not an artefact of which package a page
 *  came from, so it is spelled out here in the composition root. */
export const PAGES = ["shares", "pools", "users", "groups", "settings"].map((id) => {
  const page = byId[id];
  if (!page) throw new Error(`no page registered for "${id}"`);
  return page;
});

export const DEFAULT_PAGE = PAGES[0].id;
