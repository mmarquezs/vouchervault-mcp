// ==UserScript==
// @name         VoucherVault Checkout Reminder
// @namespace    https://curiositystream.stream/
// @version      1.1.0
// @description  Shows VoucherVault coupon codes matching the merchant you are currently visiting (checkout reminder)
// @license      MIT
// @match        https://*/*
// @exclude      https://vouchervault.curiositystream.stream/*
// @exclude      https://pocketid.curiositystream.stream/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @grant        GM.getValue
// @grant        GM.setValue
// @grant        GM.xmlHttpRequest
// @grant        GM.setClipboard
// @grant        GM.registerMenuCommand
// ==/UserScript==

/*
 * VoucherVault Checkout Reminder — surfaces your VoucherVault coupons for
 * the shop you are currently visiting.
 *
 * Original client-side code (no VoucherVault sources used).
 * Copyright (c) 2026 mmarquezs
 * SPDX-License-Identifier: MIT
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including the rights to use,
 * copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, subject to the following conditions: the above copyright
 * notice and this permission notice shall be included in all copies or
 * substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
 */

(function () {
  "use strict";

  const FETCH_TIMEOUT_MS = 15000;
  const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6h

  // --------------------------------------------------- GM API compatibility
  // Greasemonkey 4 renamed the sync GM_* APIs to async GM.* ones. Support both.
  const GMAPI = {
    get: (k, d) => (typeof GM !== "undefined" && GM.getValue ? GM.getValue(k, d) : Promise.resolve(GM_getValue(k, d))),
    set: (k, v) => (typeof GM !== "undefined" && GM.setValue ? GM.setValue(k, v) : Promise.resolve(GM_setValue(k, v))),
    xhr: (o) => (typeof GM !== "undefined" && GM.xmlHttpRequest ? GM.xmlHttpRequest(o) : GM_xmlhttpRequest(o)),
    clipboard: (t) => (typeof GM !== "undefined" && GM.setClipboard ? GM.setClipboard(t) : (typeof GM_setClipboard === "function" ? GM_setClipboard(t) : navigator.clipboard.writeText(t))),
    menu: (label, fn) => { if (typeof GM !== "undefined" && GM.registerMenuCommand) { GM.registerMenuCommand(label, fn); } else if (typeof GM_registerMenuCommand === "function") { GM_registerMenuCommand(label, fn); } }
  };

  // ---------------------------------------------------------------- config

  async function getConfig() {
    const url = await GMAPI.get("vv_url", "");
    const token = await GMAPI.get("vv_token", "");
    const user = await GMAPI.get("vv_user", "");
    if (!url || !token || !user) return null;
    return { url: String(url).replace(/\/+$/, ""), token, user };
  }

  async function promptForConfig() {
    const url = (window.prompt("VoucherVault base URL (e.g. https://vouchervault.example.com)", await GMAPI.get("vv_url", "")) || "").trim();
    if (!url) return null;
    const user = (window.prompt("VoucherVault username", await GMAPI.get("vv_user", "")) || "").trim();
    if (!user) return null;
    const token = (window.prompt("VoucherVault API token", "") || "").trim();
    if (!token) return null;
    await GMAPI.set("vv_url", url);
    await GMAPI.set("vv_user", user);
    await GMAPI.set("vv_token", token);
    return getConfig();
  }

  // ---------------------------------------------------------------- domain

  // Small hardcoded second-level suffix list (avoid pulling in a full PSL library)
  const SECOND_LEVEL = new Set([
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "net.nz", "org.nz",
    "com.br", "com.mx", "com.ar",
    "co.jp", "co.kr", "com.tr",
    "co.za", "com.sg", "com.hk",
    "co.in", "com.es", "com.pt", "com.pl",
  ]);

  function registrableDomain(hostname) {
    const labels = hostname.split(".").filter(Boolean);
    if (labels.length <= 2) return labels.join(".");
    const lastTwo = labels.slice(-2).join(".");
    if (SECOND_LEVEL.has(lastTwo) && labels.length >= 3) {
      return labels.slice(-3).join(".");
    }
    return lastTwo;
  }

  const rawSite = location.hostname;
  const site = rawSite.replace(/^www\./, "");
  const registrable = registrableDomain(site);

  // issuer===site || issuer===registrable || site.endsWith('.'+issuer) ||
  // registrable.endsWith('.'+issuer) || issuer.endsWith('.'+registrable)
  function issuerMatches(issuer) {
    const iss = String(issuer || "").replace(/^www\./, "").toLowerCase();
    if (!iss) return false;
    return (
      iss === site ||
      iss === registrable ||
      site.endsWith("." + iss) ||
      registrable.endsWith("." + iss) ||
      iss.endsWith("." + registrable)
    );
  }

  // ---------------------------------------------------------------- data

  function todayISO() {
    return new Date().toISOString().slice(0, 10);
  }

  function daysUntil(iso) {
    const then = new Date(iso + "T00:00:00Z").getTime();
    const now = new Date(todayISO() + "T00:00:00Z").getTime();
    return Math.round((then - now) / 86400000);
  }

  function isCouponRelevant(item) {
    if (!item) return false;
    if (item.is_used === true) return false;
    if (String(item.type || "").toLowerCase() === "loyaltycard") return false;
    const expiry = String(item.expiry_date || "").slice(0, 10);
    if (!expiry) return false;
    return expiry >= todayISO();
  }

  async function fetchCoupons(force) {
    const config = await getConfig();
    if (!config) throw new Error("not configured");

    const cached = await GMAPI.get("vv_cache", null);
    if (!force && cached && Date.now() - cached.ts < CACHE_TTL_MS) {
      return cached.data;
    }

    return new Promise((resolve, reject) => {
      GMAPI.xhr({
        method: "GET",
        url: config.url + "/api/get/stats?user=" + encodeURIComponent(config.user),
        headers: { Authorization: "Bearer " + config.token },
        timeout: FETCH_TIMEOUT_MS,
        onload: (res) => {
          try {
            const data = JSON.parse(res.responseText);
            GMAPI.set("vv_cache", { ts: Date.now(), data });
            resolve(data);
          } catch (e) {
            reject(e);
          }
        },
        onerror: () => reject(new Error("request failed")),
        ontimeout: () => reject(new Error("timeout")),
      });
    });
  }

  function matchingCoupons(data) {
    const items = (data && (data.item_details || data.items)) || [];
    return items.filter((item) => isCouponRelevant(item) && issuerMatches(item.issuer));
  }

  // ---------------------------------------------------------------- ui

  function formatValue(item) {
    const value = item.value;
    const type = String(item.type || "").toLowerCase();
    if (value === null || value === undefined || value === "") return "";
    if (type === "percent" || type === "percentage") {
      return value + " %";
    }
    return value + " " + (item.currency || "EUR");
  }

  function applyTheme(root, dark) {
    const css = dark
      ? { bg: "#1e1f24", fg: "#e8e8ea", muted: "#a0a2ab", border: "#3a3c44", code: "#111216", accent: "#7aa2f7" }
      : { bg: "#ffffff", fg: "#1c1e21", muted: "#60636a", border: "#d8dae0", code: "#f2f3f5", accent: "#1a73e8" };
    root.style.setProperty("--vv-bg", css.bg);
    root.style.setProperty("--vv-fg", css.fg);
    root.style.setProperty("--vv-muted", css.muted);
    root.style.setProperty("--vv-border", css.border);
    root.style.setProperty("--vv-code-bg", css.code);
    root.style.setProperty("--vv-accent", css.accent);
  }

  function render(coupons) {
    if (!coupons.length) return;
    const dismissedKey = "vv-dismissed:" + site + ":" + todayISO();
    if (sessionStorage.getItem(dismissedKey)) return;

    const host = document.createElement("div");
    host.id = "vv-checkout-reminder-host";
    host.style.cssText =
      "position:fixed;bottom:16px;right:16px;z-index:2147483647;all:initial;font-family:system-ui,-apple-system,sans-serif;";
    const shadow = host.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      .panel {
        background: var(--vv-bg);
        color: var(--vv-fg);
        border: 1px solid var(--vv-border);
        border-radius: 10px;
        box-shadow: 0 4px 16px rgba(0,0,0,.25);
        max-width: 320px;
        font-size: 13px;
        line-height: 1.45;
        overflow: hidden;
      }
      .pill {
        display: flex;
        align-items: center;
        gap: 6px;
        background: var(--vv-bg);
        color: var(--vv-fg);
        border: 1px solid var(--vv-border);
        border-radius: 999px;
        padding: 7px 14px;
        cursor: pointer;
        font-size: 13px;
        box-shadow: 0 2px 10px rgba(0,0,0,.2);
        user-select: none;
      }
      .head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 9px 12px;
        border-bottom: 1px solid var(--vv-border);
        font-weight: 600;
        cursor: pointer;
      }
      .dismiss {
        cursor: pointer;
        color: var(--vv-muted);
        border: none;
        background: none;
        font-size: 15px;
        line-height: 1;
        padding: 2px 4px;
      }
      .coupon { padding: 10px 12px; border-bottom: 1px solid var(--vv-border); }
      .coupon:last-child { border-bottom: none; }
      .name { font-weight: 600; margin-bottom: 3px; }
      .row { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
      .code {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        background: var(--vv-code-bg);
        border: 1px solid var(--vv-border);
        border-radius: 5px;
        padding: 3px 7px;
        font-size: 12px;
      }
      .copy {
        cursor: pointer;
        border: 1px solid var(--vv-border);
        background: transparent;
        color: var(--vv-fg);
        border-radius: 5px;
        font-size: 11px;
        padding: 3px 8px;
      }
      .meta { color: var(--vv-muted); font-size: 12px; margin-top: 4px; }
      .meta .soon { color: #e5484d; font-weight: 600; }
      .desc { color: var(--vv-muted); font-size: 12px; margin-top: 4px; }
      @media (prefers-color-scheme: dark) {
        .panel, .pill { box-shadow: 0 4px 18px rgba(0,0,0,.55); }
      }
    `;
    shadow.appendChild(style);

    const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const panel = document.createElement("div");
    applyTheme(panel, dark);

    const pill = document.createElement("div");
    pill.className = "pill";
    pill.textContent = "\uD83C\uDF9F " + coupons.length + " coupon" + (coupons.length === 1 ? "" : "s") + " for " + registrable + " \u25BE";

    const expanded = document.createElement("div");
    expanded.className = "panel";
    expanded.style.display = "none";

    const head = document.createElement("div");
    head.className = "head";
    const headLabel = document.createElement("span");
    headLabel.textContent = coupons.length + " coupon" + (coupons.length === 1 ? "" : "s") + " for " + registrable;
    const dismiss = document.createElement("button");
    dismiss.className = "dismiss";
    dismiss.title = "Hide for today";
    dismiss.textContent = "\u00D7";
    head.appendChild(headLabel);
    head.appendChild(dismiss);
    expanded.appendChild(head);

    for (const item of coupons) {
      const box = document.createElement("div");
      box.className = "coupon";

      const name = document.createElement("div");
      name.className = "name";
      name.textContent = String(item.name || item.issuer || "Coupon");
      box.appendChild(name);

      const row = document.createElement("div");
      row.className = "row";
      const code = document.createElement("span");
      code.className = "code";
      code.textContent = String(item.code || "");
      const copy = document.createElement("button");
      copy.className = "copy";
      copy.textContent = "Copy";
      copy.addEventListener("click", () => {
        GMAPI.clipboard(String(item.code || ""));
        copy.textContent = "\u2713";
        setTimeout(() => { copy.textContent = "Copy"; }, 1200);
      });
      row.appendChild(code);
      row.appendChild(copy);
      box.appendChild(row);

      const value = formatValue(item);
      const days = daysUntil(String(item.expiry_date || "").slice(0, 10));
      const meta = document.createElement("div");
      meta.className = "meta";
      const valueText = value ? value + " \u00B7 " : "";
      const expiryText = "expires " + String(item.expiry_date || "").slice(0, 10);
      meta.textContent = valueText + expiryText;
      if (days !== null && !isNaN(days)) {
        const daysSpan = document.createElement("span");
        daysSpan.textContent = " (" + days + "d left)";
        if (days <= 7) daysSpan.className = "soon";
        meta.appendChild(daysSpan);
      }
      box.appendChild(meta);

      const desc = String(item.description || "").trim();
      if (desc) {
        const descEl = document.createElement("div");
        descEl.className = "desc";
        descEl.textContent = desc;
        box.appendChild(descEl);
      }

      expanded.appendChild(box);
    }

    let open = false;
    const toggle = () => {
      open = !open;
      pill.style.display = open ? "none" : "flex";
      expanded.style.display = open ? "block" : "none";
    };
    pill.addEventListener("click", toggle);
    headLabel.addEventListener("click", toggle);
    dismiss.addEventListener("click", () => {
      sessionStorage.setItem(dismissedKey, "1");
      host.remove();
    });

    shadow.appendChild(pill);
    shadow.appendChild(expanded);
    document.documentElement.appendChild(host);
  }

  // ---------------------------------------------------------------- boot

  GMAPI.menu("Configure VoucherVault", () => {
    (async () => {
      const config = await promptForConfig();
      if (!config) return;
      fetchCoupons(true).then((data) => render(matchingCoupons(data))).catch(() => {});
    })();
  });

  GMAPI.menu("Refresh coupon data now", () => {
    fetchCoupons(true).then((data) => {
      document.getElementById("vv-checkout-reminder-host")?.remove();
      render(matchingCoupons(data));
    }).catch(() => {});
  });

  (async () => {
    const config = await getConfig();
    if (!config) {
      // First run: collect config once, then proceed
      const first = await promptForConfig();
      if (!first) return;
    }

    fetchCoupons(false)
      .then((data) => render(matchingCoupons(data)))
      .catch(() => {
        /* silent: no codes in logs, no noise on unrelated sites */
      });
  })();
})();
