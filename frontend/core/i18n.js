const STORAGE_KEY = "psg_lang";

let lang = localStorage.getItem(STORAGE_KEY) || "hu";
let dict = {};

export function currentLang() {
  return lang;
}

export async function loadLang() {
  // Absolute path: relative URLs would resolve against this module's
  // directory, not the document.
  const res = await fetch(`/i18n/${lang}.json`);
  dict = await res.json();
  document.documentElement.lang = lang;
  document.getElementById("lang-toggle").textContent = lang === "hu" ? "EN" : "HU";
  applyTranslations();
}

/** Fill every [data-i18n] element. Must run after any markup that carries
 *  those attributes has been inserted. */
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
