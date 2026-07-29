import { refreshState, S } from "./core/api.js";
import { applyTranslations } from "./core/i18n.js";
import { esc } from "./core/dom.js";
import { DEFAULT_PAGE, PAGES } from "./pages.js";

/** Build the nav from the page registry. Runs before the first loadLang(),
 *  which is what fills the [data-i18n] labels - generate after that and the
 *  bar renders as empty links. */
export function renderNav() {
  document.getElementById("nav").innerHTML = PAGES.map((page) =>
    `<a href="#/${esc(page.id)}" data-nav="${esc(page.id)}" data-i18n="${esc(page.navKey)}"></a>`
  ).join("");
  applyTranslations(document.getElementById("nav"));
}

export async function route() {
  if (!S) return;
  const hash = location.hash || `#/${DEFAULT_PAGE}`;
  const [id, action, arg] = hash.slice(2).split("/").map(decodeURIComponent);
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === id));

  const page = PAGES.find((p) => p.id === id);
  if (!page) {
    location.hash = `#/${DEFAULT_PAGE}`;
  } else if (page.form && action === "new") {
    page.form(null);
  } else if (page.form && action === "edit") {
    page.form(arg);
  } else {
    page.list();
  }
}

export async function startApp() {
  await refreshState();
  document.getElementById("topbar").hidden = false;
  if (!location.hash) location.hash = `#/${DEFAULT_PAGE}`;
  await route();
}
