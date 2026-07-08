// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
//
// End-to-end smoke test for the spud-router Web UI (issue #178). Drives a
// real headless Firefox against a real running backend (see
// .github/workflows/ci.yml's "e2e" job for how it's started in CI — a
// plain `uvicorn backend.main:app` serving the built frontend, exactly
// the same backend+static-file setup used in production, just without
// TLS, matching this repo's own documented local-dev instructions in
// README.md's Development section).
//
// Uses the bare `playwright` package (not @playwright/test) with Firefox,
// the same combination already used by
// .claude/skills/update-docs/screenshot.js, rather than introducing a
// second Playwright setup/dependency for one CI job.
//
// Scope (per the issue's own proposal): log in, visit every tab and fail
// on any console/page error, then exercise one successful save (VLAN add
// -> pending-changes banner) and one failed save (duplicate VLAN ->
// inline error) — the two concrete regression classes #126 and #176 were
// about. Deliberately does not click Apply/Reboot: those require real
// root privileges to touch netplan/iptables/systemd, which is out of
// scope for a UI smoke test (see tests/test_iptables_behavioral.py in
// backend/ for the netns-isolated tier that already covers real firewall
// application).
const { firefox } = require("playwright");

const BASE_URL = process.argv[2] || "http://127.0.0.1:8080";
const PASSWORD = process.argv[3] || "spudrouter";
const USERNAME = process.argv[4] || "admin";

const TABS = [
  "VLANs", "WAN", "DNS", "Routes", "Firewall", "VPN", "Wireless",
  "Diagnostics", "Logging", "SNMP", "Status", "Preview", "Update", "Settings",
];

function fail(msg) {
  console.error(`✗ ${msg}`);
  process.exitCode = 1;
  throw new Error(msg);
}

async function main() {
  const browser = await firefox.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));

  try {
    console.log(`→ ${BASE_URL}`);
    await page.goto(BASE_URL, { waitUntil: "networkidle" });

    console.log("→ signing in");
    await page.locator('input[autocomplete="username"]').fill(USERNAME);
    await page.locator('input[type="password"]').fill(PASSWORD);
    await page.getByRole("button", { name: /Sign In/ }).click();
    await page.locator("nav button", { hasText: "VLANs" }).first().waitFor({ timeout: 10000 });

    for (const label of TABS) {
      console.log(`→ tab: ${label}`);
      await page.locator("nav button", { hasText: label }).first().click();
      // Let the tab's own effects (data fetches, renders) settle before
      // moving on — generous but bounded, no tab does anything long-running.
      await page.waitForTimeout(400);
    }

    if (consoleErrors.length > 0) {
      fail(`console/page errors while visiting tabs:\n  ${consoleErrors.join("\n  ")}`);
    }
    console.log("✓ every tab rendered with no console/page errors");

    // ── Flow 1: successful save -> pending-changes banner ──────────────────
    await page.locator("nav button", { hasText: "VLANs" }).first().click();
    const vlanId = "3999";
    await page.getByPlaceholder("30", { exact: true }).fill(vlanId);
    await page.getByPlaceholder("Trusted", { exact: true }).fill("E2E Smoke Test");
    await page.getByPlaceholder("192.168.30.1", { exact: true }).first().fill("10.250.0.1");
    await page.getByRole("button", { name: "Add VLAN" }).click();

    // The pending-changes banner is refreshed on tab change (App.jsx polls
    // /api/apply/status when `tab` changes), not on every state-mutating
    // save — switching tabs and back is what a real user does next anyway.
    await page.locator("nav button", { hasText: "WAN" }).first().click();
    await page.locator(":text('Unapplied changes')").waitFor({ timeout: 5000 });
    console.log("✓ saving a VLAN surfaces the pending-changes banner");
    await page.locator("nav button", { hasText: "VLANs" }).first().click();

    // ── Flow 2: failed save -> inline error, not silence ────────────────────
    // Re-submitting the exact same VLAN ID/interface the backend just
    // persisted must be rejected (400 "already exists") and — the class of
    // bug #176 fixed — that rejection must be visible, not silently dropped.
    await page.getByPlaceholder("30", { exact: true }).fill(vlanId);
    await page.getByPlaceholder("Trusted", { exact: true }).fill("E2E Smoke Test Dup");
    await page.getByPlaceholder("192.168.30.1", { exact: true }).first().fill("10.250.0.2");
    await page.getByRole("button", { name: "Add VLAN" }).click();
    const errLocator = page.locator('[class*="_errMsg_"]', { hasText: /already exists/i });
    await errLocator.first().waitFor({ timeout: 5000 }).catch(() => {
      fail(`expected a visible "already exists" error after re-submitting a duplicate VLAN, but none appeared`);
    });
    console.log("✓ a rejected save surfaces a visible error, not silence");

    // ── Cleanup: remove the VLAN this run created ───────────────────────────
    await page.locator("nav button", { hasText: "VLANs" }).first().click();
    await page.getByRole("button", { name: "✕" }).last().click();
    await page.locator(`:text("VLAN ${vlanId} removed")`).waitFor({ timeout: 5000 });

    console.log("\n✓ e2e smoke passed");
  } catch (e) {
    console.error(e);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
