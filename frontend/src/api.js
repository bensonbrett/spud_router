/**
 * api.js — thin fetch wrapper for the spud-router backend.
 * Authentication relies solely on the httpOnly "spud_token" cookie set by
 * the backend on login. credentials: "same-origin" ensures the browser
 * sends it automatically; the token is never stored in JavaScript-accessible
 * storage (no sessionStorage, no localStorage).
 */

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
    window.location.reload();
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }

  return res.json();
}

export const GET    = (path)        => request("GET",    path);
export const POST   = (path, body)  => request("POST",   path, body);
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
