// ==UserScript==
// @name         VoucherVault Checkout Reminder
// @namespace    https://curiositystream.stream/
// @version      1.5.0
// @description  Shows VoucherVault coupon codes matching the merchant you are currently visiting (checkout reminder)
// @license      MIT
// @match        https://*/*
// @exclude      https://vouchervault.curiositystream.stream/*
// @exclude      https://pocketid.curiositystream.stream/*
// @updateURL    https://vouchervault.curiositystream.stream/tools/vouchervault-checkout.user.js
// @downloadURL  https://vouchervault.curiositystream.stream/tools/vouchervault-checkout.user.js
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @grant        GM.getValue
// @grant        GM.setValue
// @grant        GM.deleteValue
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
  const DRAG_THRESHOLD_PX = 6;    // pointer must move this far before it counts as a drag
  const VIEWPORT_MARGIN_PX = 8;   // pill never closer than this to a viewport edge
  const DEFAULT_POS = { right: 16, bottom: 16 };
  const INTRO_AUTOCOLLAPSE_MS = 4000;
  const POS_KEY = "vv-pos:";
  const INTRO_KEY = "vv-intro:";

  // --------------------------------------------------- GM API compatibility
  // Greasemonkey 4 renamed the sync GM_* APIs to async GM.* ones. Support both.
  const GMAPI = {
    get: (k, d) => (typeof GM !== "undefined" && GM.getValue ? GM.getValue(k, d) : Promise.resolve(GM_getValue(k, d))),
    set: (k, v) => (typeof GM !== "undefined" && GM.setValue ? GM.setValue(k, v) : Promise.resolve(GM_setValue(k, v))),
    del: (k) => (typeof GM !== "undefined" && GM.deleteValue ? GM.deleteValue(k) : (typeof GM_deleteValue === "function" ? Promise.resolve(GM_deleteValue(k)) : Promise.resolve())),
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

  // ------------------------------------------------- checkout URL detection

  // URL keyword prefixes that identify cart / checkout / payment pages.
  const CHECKOUT_PREFIXES = [
    "cart", "basket", "cesta", "carrito", "checkout", "payment", "pago",
    "pay", "billing", "pedido", "order", "onepage", "onestep", "pasarela",
    "factura",
  ];

  function startsWithCheckoutPrefix(s) {
    return CHECKOUT_PREFIXES.some((p) => s.startsWith(p));
  }

  // True when the current URL looks like a cart/checkout/payment page.
  // Internal helper — checks the split path segments and, additionally, the
  // raw pathname with the same prefix logic so glued routes like
  // /onepagecheckout are caught regardless of segmentation.
  function isCheckoutUrl() {
    const pathname = location.pathname.toLowerCase();
    if (startsWithCheckoutPrefix(pathname.replace(/^\//, ""))) return true;
    const segments = pathname.split(/[^a-z0-9-]+/);
    return segments.some(startsWithCheckoutPrefix);
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
          if (res.status !== 200) {
            reject(Object.assign(new Error("HTTP " + res.status), {
              status: res.status,
              bodySnippet: String(res.responseText || "").slice(0, 200),
            }));
            return;
          }
          try {
            const data = JSON.parse(res.responseText);
            GMAPI.set("vv_cache", { ts: Date.now(), data });
            resolve(data);
          } catch (e) {
            reject(e);
          }
        },
        onerror: () => reject(new Error("network error")),
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
    if (value === null || value === undefined || value === "") return "";
    const valueType = String(item.value_type || "").toLowerCase();
    if (valueType === "percent" || valueType === "percentage") {
      return value + " %";
    }
    if (valueType === "multiplier") {
      return value + "x";
    }
    return value + " " + (item.currency || "EUR");
  }

  function applyTheme(root, dark) {
    const css = dark
      ? { bg: "#1e1f24", fg: "#e8e8ea", muted: "#a0a2ab", border: "#3a3c44", code: "#111216", accent: "#4c8dff", accent2: "#2d5fd3", glow: "rgba(76,141,255,.5)" }
      : { bg: "#ffffff", fg: "#1c1e21", muted: "#60636a", border: "#d8dae0", code: "#f2f3f5", accent: "#1a73e8", accent2: "#1558b8", glow: "rgba(26,115,232,.42)" };
    root.style.setProperty("--vv-bg", css.bg);
    root.style.setProperty("--vv-fg", css.fg);
    root.style.setProperty("--vv-muted", css.muted);
    root.style.setProperty("--vv-border", css.border);
    root.style.setProperty("--vv-code-bg", css.code);
    root.style.setProperty("--vv-accent", css.accent);
    root.style.setProperty("--vv-accent-2", css.accent2);
    root.style.setProperty("--vv-accent-glow", css.glow);
  }

  const UI_CSS = `
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
      touch-action: none; /* the pill is a drag handle — keep taps/drags, no scroll hijack */
    }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 9px 12px;
      border-bottom: 1px solid var(--vv-border);
      font-weight: 600;
      cursor: pointer;
      user-select: none;
      touch-action: none; /* drag handle for the expanded panel */
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
    /* Accent-gradient attention pill (coupon reminder only; the error pill
       keeps the plain dark styling). */
    .pill-cta {
      background: linear-gradient(135deg, var(--vv-accent), var(--vv-accent-2));
      border: 1px solid rgba(255,255,255,.22);
      color: #ffffff;
      font-weight: 700;
      box-shadow: 0 2px 14px var(--vv-accent-glow), 0 4px 20px rgba(0,0,0,.25);
    }
    .chev { color: var(--vv-muted); font-size: 12px; }
    .foot {
      display: flex;
      justify-content: flex-end;
      padding: 8px 12px;
      border-top: 1px solid var(--vv-border);
      background: var(--vv-code-bg);
    }
    .hide-today {
      background: none;
      border: none;
      padding: 0;
      color: var(--vv-muted);
      font-size: 11px;
      cursor: pointer;
      text-decoration: underline;
    }
    .hide-today:hover { color: var(--vv-fg); }
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
    .dpanel {
      background: var(--vv-bg);
      color: var(--vv-fg);
      border: 1px solid var(--vv-border);
      border-radius: 10px;
      box-shadow: 0 4px 16px rgba(0,0,0,.25);
      width: 360px;
      max-width: calc(100vw - 32px);
      font-size: 13px;
      line-height: 1.45;
      overflow: hidden;
    }
    .dhead {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 9px 12px;
      border-bottom: 1px solid var(--vv-border);
      font-weight: 600;
    }
    .dsec { padding: 9px 12px; border-bottom: 1px solid var(--vv-border); }
    .dsec-title {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .4px;
      color: var(--vv-muted);
      margin-bottom: 5px;
    }
    .kv { display: flex; justify-content: space-between; gap: 12px; margin-top: 3px; }
    .kv .k { color: var(--vv-muted); flex-shrink: 0; }
    .kv .v { text-align: right; word-break: break-word; }
    .match { margin-top: 4px; font-size: 12px; }
    .snippet {
      margin-top: 5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 11px;
      color: var(--vv-muted);
      word-break: break-all;
    }
    .verdict { padding: 10px 12px; font-weight: 600; }
    .verdict.ok { color: #2da44e; }
    .verdict.info { color: var(--vv-accent); }
    .verdict.bad { color: #e5484d; }
    @media (prefers-color-scheme: dark) {
      .panel, .dpanel, .pill:not(.pill-cta) { box-shadow: 0 4px 18px rgba(0,0,0,.55); }
    }
    /* Panel entrance: slide up + fade with a tiny overshoot bounce. */
    @keyframes vv-enter {
      0% { opacity: 0; transform: translateY(12px); }
      70% { opacity: 1; transform: translateY(-3px); }
      100% { opacity: 1; transform: translateY(0); }
    }
    .vv-enter { animation: vv-enter .5s cubic-bezier(.2,.8,.3,1.15) both; }
    /* Subtle attention ripple for the collapsed pill (once per browser session). */
    @keyframes vv-pulse {
      0% { box-shadow: 0 0 0 0 var(--vv-accent-glow); }
      100% { box-shadow: 0 0 0 14px rgba(0,0,0,0); }
    }
    .vv-pulse { animation: vv-pulse .9s ease-out 2; }
    @media (prefers-reduced-motion: reduce) {
      .vv-enter, .vv-pulse { animation: none; }
    }
    /* Compact mobile sizing (v1.5): smaller pill, panel never wider than the
       viewport. Desktop styling is untouched — the vv-mobile class is
       recomputed on every render and on window resize. */
    .vv-mobile .pill { font-size: 11.5px; padding: 5px 10px; }
    .vv-mobile .panel { max-width: min(320px, calc(100vw - 24px)); }
  `;

  // Shared shadow host for every UI surface. Creates the host, injects the
  // shared stylesheet and a theme-carrier root, and removes any previous
  // instance with the same id first. The custom properties MUST be set on
  // `root` (inside the shadow tree) so they inherit into the content —
  // setting them on a detached element leaves every var() unset, which made
  // the v1.1 pill render invisible.
  function makeShadowHost(id) {
    document.getElementById(id)?.remove();
    const host = document.createElement("div");
    host.id = id;
    host.style.cssText =
      "all:initial;position:fixed;bottom:16px;right:16px;z-index:2147483647;display:block;width:auto;height:auto;margin:0;padding:0;font-family:system-ui,-apple-system,sans-serif;";
    const shadow = host.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = UI_CSS;
    shadow.appendChild(style);

    const root = document.createElement("div");
    const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(root, dark);
    shadow.appendChild(root);

    return { host, shadow, root };
  }

  function mk(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function kvRow(label) {
    const row = mk("div", "kv");
    row.appendChild(mk("span", "k", label));
    const val = mk("span", "v");
    row.appendChild(val);
    return { row, val };
  }

  function setVerdict(el, cls, text) {
    el.className = "verdict " + cls;
    el.textContent = text;
  }

  async function cacheAgeText() {
    const cached = await GMAPI.get("vv_cache", null);
    if (!cached || !cached.ts) return "empty";
    return ((Date.now() - cached.ts) / 3600000).toFixed(1) + " h";
  }

  // ------------------------------------------------------- pill positioning

  function parsePx(v, fallback) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : fallback;
  }

  // Clamp right/bottom offsets so the host stays fully inside the viewport
  // with at least VIEWPORT_MARGIN_PX on every edge.
  function clampHostPosition(host, right, bottom) {
    const w = host.offsetWidth || 0;
    const h = host.offsetHeight || 0;
    const maxRight = Math.max(VIEWPORT_MARGIN_PX, window.innerWidth - w - VIEWPORT_MARGIN_PX);
    const maxBottom = Math.max(VIEWPORT_MARGIN_PX, window.innerHeight - h - VIEWPORT_MARGIN_PX);
    return {
      right: Math.min(Math.max(right, VIEWPORT_MARGIN_PX), maxRight),
      bottom: Math.min(Math.max(bottom, VIEWPORT_MARGIN_PX), maxBottom),
    };
  }

  // Apply (clamped) right/bottom offsets to the host; returns the clamped pair.
  function setHostPosition(host, right, bottom) {
    const p = clampHostPosition(host, right, bottom);
    host.style.right = p.right + "px";
    host.style.bottom = p.bottom + "px";
    return p;
  }

  // Coarse pointer (phones/tablets) or a narrow viewport → compact sizing.
  function isMobileViewport() {
    const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    return Boolean(coarse) || window.innerWidth < 768;
  }

  // ----------------------------------------------------------- drag support

  // Turns `handle` into a drag surface for the fixed-position `host` using
  // Pointer Events (works for touch and mouse alike). A press that moves more
  // than DRAG_THRESHOLD_PX drags the host (clamped to the viewport, final
  // position persisted per site under POS_KEY + site) and does NOT activate;
  // a press that stays within the threshold counts as a click and calls
  // onActivate (the pill/header toggle). Suppressing activation this way —
  // instead of intercepting the synthetic click — keeps drag vs. tap
  // deterministic on both input types.
  function makeDraggable(handle, host, onActivate) {
    let active = false;
    let moved = false;
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let startRight = DEFAULT_POS.right;
    let startBottom = DEFAULT_POS.bottom;

    const persistPosition = () => {
      const p = setHostPosition(
        host,
        parsePx(host.style.right, DEFAULT_POS.right),
        parsePx(host.style.bottom, DEFAULT_POS.bottom)
      );
      GMAPI.set(POS_KEY + site, { right: Math.round(p.right), bottom: Math.round(p.bottom) });
    };

    handle.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      active = true;
      moved = false;
      pointerId = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      startRight = parsePx(host.style.right, DEFAULT_POS.right);
      startBottom = parsePx(host.style.bottom, DEFAULT_POS.bottom);
      try { handle.setPointerCapture(e.pointerId); } catch (_) { /* capture is best-effort */ }
    });

    handle.addEventListener("pointermove", (e) => {
      if (!active || e.pointerId !== pointerId) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (!moved && Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) {
        moved = true;
        handle.style.cursor = "grabbing";
      }
      if (moved) setHostPosition(host, startRight - dx, startBottom - dy);
    });

    const release = (e) => {
      try { handle.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    };

    handle.addEventListener("pointerup", (e) => {
      if (!active || e.pointerId !== pointerId) return;
      active = false;
      handle.style.cursor = "";
      release(e);
      if (moved) {
        persistPosition();
      } else if (onActivate) {
        onActivate();
      }
    });

    handle.addEventListener("pointercancel", (e) => {
      if (!active || e.pointerId !== pointerId) return;
      active = false;
      handle.style.cursor = "";
      release(e);
      // A cancelled gesture keeps a partial drag's position but never toggles.
      if (moved) persistPosition();
    });
  }

  // ------------------------------------------------------- SPA URL watching

  // Controller for the live coupon panel, replaced on every render().
  let panelCtl = null;

  // Poll interval handle + state; cleared whenever the panel host leaves the
  // DOM (hide-until-tomorrow, re-render via makeShadowHost, manual cleanup).
  let urlWatchTimer = null;
  let urlWatchState = null;

  function stopUrlWatch() {
    if (urlWatchTimer !== null) {
      clearInterval(urlWatchTimer);
      urlWatchTimer = null;
    }
    if (urlWatchState && urlWatchState.onResize) {
      window.removeEventListener("resize", urlWatchState.onResize);
    }
    urlWatchState = null;
  }

  // SPA frameworks rewrite the URL without a page load. While the coupon
  // host is in the DOM, poll location.href and expand a collapsed panel when
  // the user moves from a non-checkout to a checkout URL on the same
  // hostname. Leaving checkout never force-collapses (user keeps control).
  // The same watcher owns the window resize listener that re-clamps the live
  // pill position and refreshes compact mobile mode; both clean up when the
  // host leaves the DOM (hide-until-tomorrow, re-render, manual removal).
  function startUrlWatch(host, root) {
    stopUrlWatch();
    const onResize = () => {
      if (!host.isConnected) { stopUrlWatch(); return; }
      // Re-clamp the live position in place (no re-render needed) and
      // re-evaluate compact mobile sizing.
      setHostPosition(
        host,
        parsePx(host.style.right, DEFAULT_POS.right),
        parsePx(host.style.bottom, DEFAULT_POS.bottom)
      );
      root.classList.toggle("vv-mobile", isMobileViewport());
    };
    window.addEventListener("resize", onResize);
    urlWatchState = {
      host,
      onResize,
      lastHref: location.href,
      lastHostname: location.hostname,
      lastCheckout: isCheckoutUrl(),
    };
    urlWatchTimer = setInterval(() => {
      const st = urlWatchState;
      if (!st) { stopUrlWatch(); return; }
      // Host removed (hide-until-tomorrow, re-render) → stop polling and
      // drop the resize listener with it.
      if (!st.host.isConnected) { stopUrlWatch(); return; }
      const href = location.href;
      if (href === st.lastHref) return;
      st.lastHref = href;
      if (location.hostname !== st.lastHostname) {
        // Cross-host change means a full page load is imminent; resync and ignore.
        st.lastHostname = location.hostname;
        st.lastCheckout = isCheckoutUrl();
        return;
      }
      const nowCheckout = isCheckoutUrl();
      if (nowCheckout && !st.lastCheckout) {
        const ctl = panelCtl;
        if (ctl && ctl.host.isConnected && !ctl.isOpen()) ctl.expand();
      }
      st.lastCheckout = nowCheckout;
    }, 800);
  }

  async function render(coupons) {
    if (!coupons.length) return;
    const dismissedKey = "vv-dismissed:" + site + ":" + todayISO();
    if (sessionStorage.getItem(dismissedKey)) return;

    // Saved pill position for this site (persisted by dragging); falls back
    // to the default 16/16 corner offsets. Read before the host is built so
    // it can be applied the moment the host hits the DOM.
    const savedPos = await GMAPI.get(POS_KEY + site, null);
    const savedRight = savedPos && Number.isFinite(savedPos.right) ? savedPos.right : DEFAULT_POS.right;
    const savedBottom = savedPos && Number.isFinite(savedPos.bottom) ? savedPos.bottom : DEFAULT_POS.bottom;

    const { host, root } = makeShadowHost("vv-checkout-reminder-host");

    // Attention model (v1.5): on checkout pages the panel always opens
    // expanded with the entrance animation — checkout visits are rare and
    // that is the moment this tool exists for. Anywhere else the FIRST
    // render of the browser session gets the expanded intro (auto-collapses
    // after 4s, any click cancels — gated per site via vv-intro:<site>);
    // later renders stay a collapsed pill that pulses once per session so
    // the eye learns it exists. Day-dismiss suppresses everything.
    const introKey = INTRO_KEY + site;
    const onCheckout = isCheckoutUrl();
    const introPending = !onCheckout && !sessionStorage.getItem(introKey);
    const startExpanded = onCheckout || introPending;
    if (introPending) sessionStorage.setItem(introKey, "1");
    let introTimer = null;
    const cancelIntroTimer = () => {
      if (introTimer !== null) {
        clearTimeout(introTimer);
        introTimer = null;
      }
    };

    const pill = document.createElement("div");
    pill.className = "pill pill-cta";
    pill.textContent = "\uD83C\uDF9F " + coupons.length + " coupon" + (coupons.length === 1 ? "" : "s") + " for " + registrable + " \u25BE";

    const expanded = document.createElement("div");
    expanded.className = "panel" + (startExpanded ? " vv-enter" : "");
    expanded.style.display = startExpanded ? "block" : "none";

    const head = document.createElement("div");
    head.className = "head";
    head.title = "Collapse to the pill";
    const headLabel = document.createElement("span");
    headLabel.textContent = coupons.length + " coupon" + (coupons.length === 1 ? "" : "s") + " for " + registrable;
    const chev = mk("span", "chev", "\u25B4");
    head.appendChild(headLabel);
    head.appendChild(chev);
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
      code.textContent = String(item.redeem_code || item.code || "");
      const copy = document.createElement("button");
      copy.className = "copy";
      copy.textContent = "Copy";
      copy.addEventListener("click", () => {
        GMAPI.clipboard(String(item.redeem_code || item.code || ""));
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

    // Footer: explicit per-day hide, decoupled from collapsing to the pill.
    const foot = mk("div", "foot");
    const hideToday = mk("button", "hide-today", "Hide until tomorrow");
    hideToday.title = "Hide the reminder on this site until tomorrow";
    foot.appendChild(hideToday);
    expanded.appendChild(foot);

    let open = startExpanded;
    pill.style.display = open ? "none" : "flex"; // match initial state
    const setExpanded = (v) => {
      open = v;
      pill.style.display = open ? "none" : "flex";
      expanded.style.display = open ? "block" : "none";
    };
    // A click cancels the pending 4s intro auto-collapse, then toggles.
    const toggle = () => {
      cancelIntroTimer();
      setExpanded(!open);
    };

    // Expand with a replayed entrance animation (used by the SPA watcher and
    // any future collapse → expand cycle).
    const expandWithAnimation = () => {
      if (open) return;
      setExpanded(true);
      expanded.classList.remove("vv-enter");
      void expanded.offsetWidth; // force reflow so the animation restarts
      expanded.classList.add("vv-enter");
    };

    // Pill and panel header are drag handles; a press that doesn't move
    // beyond the drag threshold toggles, a real drag repositions + persists
    // and never toggles (see makeDraggable).
    makeDraggable(pill, host, toggle);
    makeDraggable(head, host, toggle);

    hideToday.addEventListener("click", () => {
      cancelIntroTimer();
      sessionStorage.setItem(dismissedKey, "1");
      host.remove(); // the watcher's isConnected check clears the interval + resize listener
    });

    // First non-checkout render of the session: auto-collapse the intro
    // after 4s unless the user already interacted.
    if (introPending) {
      introTimer = setTimeout(() => {
        introTimer = null;
        if (open) setExpanded(false);
      }, INTRO_AUTOCOLLAPSE_MS);
    }

    root.appendChild(pill);
    root.appendChild(expanded);
    document.documentElement.appendChild(host);
    // OffsetWidth is only measurable once attached — apply (and clamp) the
    // saved position immediately after the host enters the DOM.
    setHostPosition(host, savedRight, savedBottom);
    root.classList.toggle("vv-mobile", isMobileViewport());

    // One subtle pulse per browser session, collapsed non-checkout pill
    // only — on checkout pages and during the intro the expanded panel is
    // attention enough.
    if (!startExpanded) {
      const noticedKey = "vv-noticed:" + site;
      if (!sessionStorage.getItem(noticedKey)) {
        sessionStorage.setItem(noticedKey, "1");
        pill.classList.add("vv-pulse");
      }
    }

    panelCtl = { host, isOpen: () => open, expand: expandWithAnimation };
    startUrlWatch(host, root);
  }

  // ------------------------------------------------------------ diagnostics

  function renderDiagnostics() {
    const { host, root } = makeShadowHost("vv-diagnostics-host");

    // Entrance animation only (shared .vv-enter keyframes, reduced-motion
    // aware) — diagnostics never auto-collapses and is unaffected by the
    // coupon panel's attention/checkout-expansion logic.
    const card = mk("div", "dpanel vv-enter");

    const head = mk("div", "dhead");
    head.appendChild(mk("span", "", "VoucherVault diagnostics"));
    const close = mk("button", "dismiss", "\u00D7");
    close.title = "Close";
    close.addEventListener("click", () => host.remove());
    head.appendChild(close);
    card.appendChild(head);

    // Config section
    const cfgSec = mk("div", "dsec");
    cfgSec.appendChild(mk("div", "dsec-title", "Config"));
    const urlRow = kvRow("Base URL");
    const userRow = kvRow("Username");
    const tokenRow = kvRow("Token");
    cfgSec.appendChild(urlRow.row);
    cfgSec.appendChild(userRow.row);
    cfgSec.appendChild(tokenRow.row);
    card.appendChild(cfgSec);

    // API check section
    const apiSec = mk("div", "dsec");
    apiSec.appendChild(mk("div", "dsec-title", "API check"));
    const endpointRow = kvRow("Endpoint");
    const statusRow = kvRow("Status");
    statusRow.val.textContent = "checking\u2026";
    apiSec.appendChild(endpointRow.row);
    apiSec.appendChild(statusRow.row);
    card.appendChild(apiSec);

    // Data section
    const dataSec = mk("div", "dsec");
    dataSec.style.display = "none";
    dataSec.appendChild(mk("div", "dsec-title", "Data"));
    const totalRow = kvRow("Items for user");
    const relevantRow = kvRow("Relevant (unused, unexpired)");
    const matchedRow = kvRow("Matched for " + registrable);
    dataSec.appendChild(totalRow.row);
    dataSec.appendChild(relevantRow.row);
    dataSec.appendChild(matchedRow.row);
    const matchList = mk("div");
    dataSec.appendChild(matchList);
    card.appendChild(dataSec);

    // Cache section
    const cacheSec = mk("div", "dsec");
    cacheSec.appendChild(mk("div", "dsec-title", "Cache"));
    const ageRow = kvRow("Age");
    const refreshedRow = kvRow("Force-refreshed");
    cacheSec.appendChild(ageRow.row);
    cacheSec.appendChild(refreshedRow.row);
    card.appendChild(cacheSec);

    // Layout section: pill position recovery (per-site drag position).
    const posSec = mk("div", "dsec");
    posSec.appendChild(mk("div", "dsec-title", "Layout"));
    const resetPos = mk("button", "hide-today", "Reset pill position");
    resetPos.title = "Clear the saved pill position for " + registrable + " and re-render";
    resetPos.addEventListener("click", async () => {
      await GMAPI.del(POS_KEY + site);
      // If the coupon pill is currently visible, snap it back to the corner.
      if (panelCtl && panelCtl.host.isConnected) {
        setHostPosition(panelCtl.host, DEFAULT_POS.right, DEFAULT_POS.bottom);
      }
      renderDiagnostics();
    });
    posSec.appendChild(resetPos);
    card.appendChild(posSec);

    // Verdict
    const verdict = mk("div", "verdict");
    verdict.textContent = "checking\u2026";
    card.appendChild(verdict);

    root.appendChild(card);
    document.documentElement.appendChild(host);

    (async () => {
      const config = await getConfig();
      if (!config) {
        const url = await GMAPI.get("vv_url", "");
        const user = await GMAPI.get("vv_user", "");
        const token = await GMAPI.get("vv_token", "");
        urlRow.val.textContent = url ? String(url) : "missing";
        userRow.val.textContent = user ? String(user) : "missing";
        tokenRow.val.textContent = token ? "set" : "missing";
        endpointRow.row.style.display = "none";
        statusRow.val.textContent = "skipped (not configured)";
        ageRow.val.textContent = await cacheAgeText();
        refreshedRow.val.textContent = "no";
        setVerdict(verdict, "bad", "\u2716 not configured \u2014 use \u201CConfigure VoucherVault\u201D in the userscript menu");
        return;
      }

      urlRow.val.textContent = config.url;
      userRow.val.textContent = config.user;
      tokenRow.val.textContent = "set";
      endpointRow.val.textContent = config.url + "/api/get/stats?user=" + config.user;

      try {
        const data = await fetchCoupons(true);
        statusRow.val.textContent = "HTTP 200";
        const items = (data && (data.item_details || data.items)) || [];
        const relevant = items.filter(isCouponRelevant);
        const matched = matchingCoupons(data);
        totalRow.val.textContent = String(items.length);
        relevantRow.val.textContent = String(relevant.length);
        matchedRow.val.textContent = String(matched.length);
        for (const m of matched) {
          matchList.appendChild(mk("div", "match",
            "\u2022 " + String(m.name || m.issuer || "?") + " \u2014 " + String(m.issuer || "?") + " \u2014 expires " + String(m.expiry_date || "").slice(0, 10)));
        }
        dataSec.style.display = "";
        ageRow.val.textContent = await cacheAgeText();
        refreshedRow.val.textContent = "yes";
        if (matched.length) {
          setVerdict(verdict, "ok", "\u2714 Pill shows: " + matched.length + " coupon" + (matched.length === 1 ? "" : "s"));
        } else {
          setVerdict(verdict, "info", "\u2139 Everything OK \u2014 no coupons match " + registrable + " (pill stays hidden by design)");
        }
      } catch (e) {
        statusRow.val.textContent = String((e && e.message) || e);
        if (e && e.status === 404 && e.bodySnippet) {
          const snippet = mk("div", "snippet", String(e.bodySnippet));
          apiSec.appendChild(snippet);
        }
        dataSec.style.display = "none";
        ageRow.val.textContent = await cacheAgeText();
        refreshedRow.val.textContent = "no";
        const msg = String((e && e.message) || "");
        if (e && (e.status === 401 || e.status === 403)) {
          setVerdict(verdict, "bad", "\u2716 token wrong \u2014 use the API Settings token");
        } else if (e && e.status === 404) {
          setVerdict(verdict, "bad", "\u2716 username must be the VoucherVault account name");
        } else if (msg === "timeout" || msg === "network error") {
          setVerdict(verdict, "bad", "\u2716 can't reach the URL");
        } else if (e && e.status) {
          setVerdict(verdict, "bad", "\u2716 API error (HTTP " + e.status + ")");
        } else {
          setVerdict(verdict, "bad", "\u2716 " + msg);
        }
      }
    })();
  }

  // Non-silent failure pill: shown only for auth/user API errors (401/403/404)
  // from the background boot fetch. Dismissed for the rest of the day per site.
  function renderErrorPill(status) {
    const dismissedKey = "vv-errpill:" + site + ":" + todayISO();
    if (sessionStorage.getItem(dismissedKey)) return;

    const { host, root } = makeShadowHost("vv-errpill-host");

    const pill = document.createElement("div");
    pill.className = "pill";
    pill.textContent = "\uD83C\uDF9F\u26A0 VoucherVault: API error (" + status + ") \u2014 click for diagnostics";
    pill.addEventListener("click", () => {
      sessionStorage.setItem(dismissedKey, "1");
      host.remove();
      renderDiagnostics();
    });

    root.appendChild(pill);
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
      render(matchingCoupons(data));
    }).catch(() => {});
  });

  GMAPI.menu("VV diagnostics", () => {
    renderDiagnostics();
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
      .catch((err) => {
        // Visible, non-silent failure for auth/user errors; everything else
        // (first-run "not configured", timeouts, unrelated-site noise) stays
        // quiet. No codes or tokens are ever logged.
        if (err && (err.status === 401 || err.status === 403 || err.status === 404)) {
          renderErrorPill(err.status);
        }
      });
  })();
})();
