const STORAGE_KEY = "pnas_lang";

// One file per package, merged on load. The dictionaries are flat maps of
// dotted keys, so a plain merge is enough - nesting them would break t().
const BUNDLES = ["core", "samba", "storage"];

let lang = localStorage.getItem(STORAGE_KEY) || "hu";
let dict = {};

export function currentLang() {
  return lang;
}

export async function loadLang() {
  // Absolute paths: a relative URL would resolve against this module's
  // directory rather than the document.
  const parts = await Promise.all(
    BUNDLES.map((b) => fetch(`/i18n/${b}/${lang}.json`).then((r) => r.json()))
  );
  dict = Object.assign({}, ...parts);
  document.documentElement.lang = lang;
  document.getElementById("lang-toggle").textContent = lang === "hu" ? "EN" : "HU";
  applyTranslations();
}

/** Fill every [data-i18n] element. Must run after the markup carrying those
 *  attributes exists - the nav is generated, so order matters. */
export function applyTranslations(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
}

export async function toggleLang() {
  lang = lang === "hu" ? "en" : "hu";
  localStorage.setItem(STORAGE_KEY, lang);
  await loadLang();
}

export function t(key, vars) {
  let s = dict[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}
