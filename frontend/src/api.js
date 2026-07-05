// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
/**
 * api.js — thin fetch wrapper for the spud-router backend.
 * Authentication relies solely on the httpOnly "spud_token" cookie set by
 * the backend on login. credentials: "same-origin" ensures the browser
 * sends it automatically; the token is never stored in JavaScript-accessible
 * storage (no sessionStorage, no localStorage).
 */

// Registered by App so a 401 can route the SPA back to the login screen
// without a full page reload (which would silently discard in-progress edits).
let unauthorizedHandler = null;
export function setUnauthorizedHandler(fn) { unauthorizedHandler = fn; }

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
    },
    body: body != null ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 && path !== "/api/auth/status") {
    // Session expired/invalid. Route to login via the app (preserves the SPA)
    // instead of window.location.reload(), and throw a marked error so callers
    // can skip their own "save failed" toast — the app shows one instead.
    if (unauthorizedHandler) unauthorizedHandler();
    const err = new Error("Session expired — please sign in again.");
    err.isAuthError = true;
    throw err;
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }

  return res.json();
}

export const GET    = (path)        => request("GET",    path);
export const POST   = (path, body)  => request("POST",   path, body);
export const PUT    = (path, body)  => request("PUT",    path, body);
export const DELETE = (path)        => request("DELETE", path);

/**
 * exportConfig — streams the backup zip directly to a browser download.
 * Cannot go through the JSON wrapper since the response is binary.
 */
export async function exportConfig() {
  const res = await fetch("/api/config/export", {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Export failed");
  const blob = await res.blob();
  const cd   = res.headers.get("Content-Disposition") || "";
  const name = cd.match(/filename="([^"]+)"/)?.[1] || "spud-router-backup.zip";
  const a    = document.createElement("a");
  a.href     = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}
