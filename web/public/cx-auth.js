/**
 * CX WebApp secure client
 * -----------------------
 * - Reads Telegram.WebApp.initData
 * - Fetches real balance from backend API (never trust URL ?bal=)
 *
 * Configure API base once (Cloudflare Pages can set this):
 *   window.CX_API_BASE = "https://api.your-domain.com";
 */
(function (global) {
  "use strict";

  const DEFAULT_API =
    (global.CX_API_BASE || "").replace(/\/$/, "") ||
    (global.localStorage && localStorage.getItem("CX_API_BASE")) ||
    "";

  function tg() {
    return global.Telegram && global.Telegram.WebApp
      ? global.Telegram.WebApp
      : null;
  }

  function getInitData() {
    const w = tg();
    if (!w) return "";
    return w.initData || "";
  }

  function getUnsafeUser() {
    const w = tg();
    return (w && w.initDataUnsafe && w.initDataUnsafe.user) || null;
  }

  /**
   * Authenticated fetch against CX API.
   * @param {string} path e.g. "/api/me"
   * @param {RequestInit} [opts]
   */
  async function apiFetch(path, opts) {
    const base = DEFAULT_API;
    if (!base) {
      throw new Error(
        "CX_API_BASE is not set. Set window.CX_API_BASE to your API public URL."
      );
    }
    const initData = getInitData();
    if (!initData) {
      throw new Error(
        "Telegram initData is empty. Open this page inside Telegram Mini App."
      );
    }
    const headers = Object.assign(
      {
        "X-Telegram-Init-Data": initData,
        Accept: "application/json",
      },
      (opts && opts.headers) || {}
    );
    const res = await fetch(base + path, Object.assign({}, opts, { headers }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg =
        (data && (data.detail || data.error)) || "API error " + res.status;
      throw new Error(msg);
    }
    return data;
  }

  async function fetchAccount() {
    return apiFetch("/api/me");
  }

  async function fetchTransactions(limit) {
    const q = typeof limit === "number" ? "?limit=" + limit : "";
    return apiFetch("/api/transactions" + q);
  }

  /**
   * Bind balance into DOM. Falls back to "—" if API unavailable.
   * @param {string|HTMLElement} el
   * @param {"ton"|"usdt"} [currency]
   */
  async function bindBalance(el, currency) {
    const node =
      typeof el === "string" ? document.querySelector(el) : el;
    if (!node) return null;
    currency = currency || "ton";
    try {
      const data = await fetchAccount();
      const acc = data.account || {};
      const value =
        currency === "usdt" ? acc.usdt_balance : acc.balance_ton;
      const n = Number(value) || 0;
      node.textContent = n.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      });
      node.dataset.balance = String(n);
      node.dataset.authenticated = "1";
      return data;
    } catch (err) {
      console.warn("[CX Auth]", err.message || err);
      node.textContent = "—";
      node.dataset.authenticated = "0";
      node.dataset.error = String(err.message || err);
      return null;
    }
  }

  function ready(fn) {
    const w = tg();
    if (w) {
      try {
        w.ready();
        w.expand();
      } catch (_) {}
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  global.CXAuth = {
    getInitData: getInitData,
    getUnsafeUser: getUnsafeUser,
    apiFetch: apiFetch,
    fetchAccount: fetchAccount,
    fetchTransactions: fetchTransactions,
    bindBalance: bindBalance,
    ready: ready,
    apiBase: DEFAULT_API,
  };
})(window);
