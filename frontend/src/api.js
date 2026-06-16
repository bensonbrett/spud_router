/**
 * api.js — thin fetch wrapper for the spud-router backend.
 * All routes use JSON; auth token is read from sessionStorage.
 */

async function request(method, path, body) {
  const token = sessionStorage.getItem("spud_token");
  const res = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Session-Token": token } : {}),
    },
    body: body != null ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    sessionStorage.removeItem("spud_token");
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
  const token = sessionStorage.getItem("spud_token") || "";
  const res = await fetch("/api/config/export", {
    headers: { "X-Session-Token": token },
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
