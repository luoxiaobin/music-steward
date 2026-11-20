// Interface translation (English source). The dictionary comes from /api/i18n/<lang>
// (music_steward/i18n.py). Interface text is translated as it is rendered: exact phrases, then
// patterns with {names}, then pieces of " · "-joined lines, then line by line. Data (titles,
// artists, paths) never matches an entry, so it is left as is.
"use strict";

const I18N = { lang: "en", exact: new Map(), patterns: [], ready: false };
const NUMERIC = new Set(["n", "a", "b", "d", "e", "s", "m", "g", "f", "pct", "id", "y", "z"]);
const WORD_ONLY = new Set(["k", "v"]);   // must themselves be known words ("album from cue sheet")

function _escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

function _compile(dict) {
  I18N.exact = new Map();
  I18N.patterns = [];
  for (const [en, tr] of Object.entries(dict)) {
    if (!/\{\w+\}/.test(en)) { I18N.exact.set(en, tr); continue; }
    const names = [];
    // typed slots, so that data (an album called "Pride of Lions") never matches a pattern:
    // counts and years only take numbers, sizes only sizes, everything else any text
    const re = "^" + en.split(/(\{\w+\})/).map(p => {
      const m = p.match(/^\{(\w+)\}$/);
      if (!m) return _escapeRe(p);
      names.push(m[1]);
      if (NUMERIC.has(m[1])) return "(\\d[\\d.,]*|\\?)";
      if (m[1].startsWith("size")) return "(\\d[\\d.,]* ?[KMGT]?B)";
      return "(.*?)";
    }).join("") + "$";
    I18N.patterns.push({ re: new RegExp(re, "s"), names, tr, lit: en.replace(/\{\w+\}/g, "").length });
  }
  I18N.patterns.sort((a, b) => b.lit - a.lit);   // most specific pattern first
}

function _core(s) {
  if (I18N.exact.has(s)) return I18N.exact.get(s);
  for (const p of I18N.patterns) {
    const m = p.re.exec(s);
    if (!m) continue;
    if (p.names.some((n, i) => WORD_ONLY.has(n) && !I18N.exact.has(m[i + 1].trim()))) continue;
    let out = p.tr;
    p.names.forEach((n, i) => {
      const v = m[i + 1], t = v.trim();
      const tv = t && I18N.exact.has(t) ? v.replace(t, I18N.exact.get(t)) : v;   // values: exact words only
      out = out.split(`{${n}}`).join(tv);
    });
    return out;
  }
  return null;
}

// translate one piece of interface text; returns it unchanged when unknown
function tr(text) {
  if (I18N.lang === "en" || !text) return text;
  const lead = text.match(/^[\s·]*/)[0], trail = text.match(/[\s·]*$/)[0];   // keep " · " separators at the edges
  const s = text.slice(lead.length, text.length - trail.length);
  if (!s || !/[A-Za-z]/.test(s)) return text;
  let r = _core(s);
  if (r == null && s.includes("\n")) {
    const lines = s.split("\n").map(l => (l.trim() ? (_core(l.trim()) ?? l) : l));
    r = lines.join("\n");
    if (r === s) r = null;
  }
  if (r == null && s.includes(" · ")) {
    const parts = s.split(" · ").map(p => _core(p.trim()) ?? p);
    r = parts.join(" · ");
    if (r === s) r = null;
  }
  return r == null ? text : lead + r + trail;
}

const _ATTRS = ["placeholder", "title", "aria-label"];
function _translateNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const p = node.parentNode;
    if (!p || p.closest && p.closest("script, style, [data-no-translate]")) return;
    if (node._i18nDone === node.nodeValue) return;
    const t = tr(node.nodeValue);
    if (t !== node.nodeValue) node.nodeValue = t;
    node._i18nDone = node.nodeValue;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE || node.closest("[data-no-translate]")) return;
  for (const a of _ATTRS) {
    const v = node.getAttribute(a);
    if (v && node[`_i18n_${a}`] !== v) { const t = tr(v); if (t !== v) node.setAttribute(a, t); node[`_i18n_${a}`] = node.getAttribute(a); }
  }
  if (node.tagName === "OPTION" || node.tagName === "BUTTON" || node.childNodes.length) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n = walker.nextNode();
    while (n) { if (n.nodeType === Node.TEXT_NODE) _translateNode(n); else if (n !== node) for (const a of _ATTRS) if (n.hasAttribute(a)) _translateNode(n); n = walker.nextNode(); }
  }
}

let _observer = null;
async function setLanguage(lang) {
  const CANON = { en: "en", "zh-hans": "zh-Hans", "zh-hant": "zh-Hant", "fr-ca": "fr-CA", "es-us": "es-US" };
  const want = lang && lang !== "auto" ? (CANON[String(lang).toLowerCase()] || "en") : _browserLang();
  I18N.lang = want;
  document.documentElement.lang = want === "en" ? "en" : want;
  if (want === "en") { I18N.ready = true; return; }
  const dict = await (await fetch(`/api/i18n/${encodeURIComponent(want)}`)).json();
  _compile(dict);
  I18N.ready = true;
  _translateNode(document.body);
  if (!_observer) {
    _observer = new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === "characterData") _translateNode(m.target);
        else if (m.type === "attributes") _translateNode(m.target);
        else for (const n of m.addedNodes) _translateNode(n);
      }
    });
    _observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: _ATTRS });
  }
}
function _browserLang() {
  const l = (navigator.language || "en").toLowerCase();
  if (l.startsWith("zh")) return /hant|tw|hk|mo/.test(l) ? "zh-Hant" : "zh-Hans";
  if (l.startsWith("fr")) return "fr-CA";
  if (l.startsWith("es")) return "es-US";
  return "en";
}
