# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
#
# Nix flake for spud-router (issue #191).
#
# STATUS: UNBUILT / UNTESTED — hand-written starting-point draft. There is no
# Nix environment available in the session that authored this, so nothing
# here has been evaluated, built, or run. Known gaps, tracked as TODOs below:
#   - npmDepsHash is a placeholder (pkgs.lib.fakeHash) — needs a real hash
#     computed in an actual Nix environment before `nix build` can work.
#   - No flake.lock committed yet — a maintainer runs `nix flake lock` once
#     this is validated.
#
# This flake is PURELY ADDITIVE and does not replace install.sh, which
# remains the single source of truth for real Debian/Ubuntu device
# deployment (see README.md's "Install" section). Nothing in backend/,
# frontend/, or install.sh was touched to accommodate this file — anything
# Nix needed to work around lives here instead.
#
# Layout mirrored from install.sh / release.yml: the running app expects
# backend/main.py's STATIC_DIR (Path(__file__).parent.parent / "static") to
# resolve to a `static/` directory that is a *sibling* of `backend/`, i.e.
#   <install root>/backend/...
#   <install root>/static/index.html
#   <install root>/static/assets/...
# `nix build`'s packages.default reproduces that same sibling layout under
# $out/lib/spud-router/.
{
  description = "spud-router — router-on-a-stick management app (FastAPI + React SPA)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # VERSION is the repo's authoritative version file (written by the
        # release workflow); backend/pyproject.toml's version = "0.0.0" is a
        # placeholder and intentionally not used here — see that file's own
        # comment.
        version = pkgs.lib.removeSuffix "\n" (builtins.readFile ./VERSION);

        # ── Frontend: Vite build via buildNpmPackage ────────────────────────
        # frontend/vite.config.js sets `build.outDir: '../dist'`, which lands
        # outside the npm package's own source root. We don't touch that
        # file — the installPhase below just reaches one directory up to
        # collect what vite already wrote, same as install.sh/release.yml do
        # with the plain `dist/` directory in a non-Nix build.
        frontend = pkgs.buildNpmPackage {
          pname = "spud-router-ui";
          inherit version;
          src = ./frontend;

          # nodejs_22 to match .github/workflows/release.yml's Node version.
          nodejs = pkgs.nodejs_22;

          # TODO: placeholder — there is no Nix environment available to
          # compute the real hash yet. Once one exists, replace with the
          # output of building once and copying the hash Nix reports
          # (or `prefetch-npm-deps frontend/package-lock.json`).
          npmDepsHash = pkgs.lib.fakeHash;

          installPhase = ''
            runHook preInstall
            mkdir -p $out
            cp -r ../dist/. $out/
            runHook postInstall
          '';
        };

        # ── Backend: pinned interpreter + runtime deps ──────────────────────
        # Mirrors backend/pyproject.toml's [project.dependencies]. The
        # uvicorn[standard] extras are listed explicitly since nixpkgs'
        # plain `uvicorn` package may not pull them in automatically —
        # TODO: verify these attribute names still exist against whichever
        # nixpkgs revision this ends up pinned to; drop any that don't.
        pythonEnv = pkgs.python312.withPackages (ps: with ps; [
          fastapi
          uvicorn
          pydantic
          uvloop
          httptools
          websockets
          watchfiles
          python-dotenv
          pyyaml
        ]);

        devPythonEnv = pkgs.python312.withPackages (ps: with ps; [
          fastapi
          uvicorn
          pydantic
          pytest
          pytest-cov
          httpx # required by FastAPI's TestClient, per backend/pyproject.toml's dev deps
        ]);
      in
      {
        # ── nix build ──────────────────────────────────────────────────────
        # Stages backend/ + the built frontend into the same sibling layout
        # install.sh produces at /opt/spud-router, and wraps `uvicorn` so
        # `nix run` / ./result/bin/spud-router starts the app from there.
        # NOTE (design intent, not verified): this has never been built —
        # see the STATUS note at the top of this file.
        packages.default = pkgs.stdenv.mkDerivation {
          pname = "spud-router";
          inherit version;
          dontUnpack = true;
          nativeBuildInputs = [ pkgs.makeWrapper ];

          installPhase = ''
            runHook preInstall

            mkdir -p $out/lib/spud-router
            cp -r ${./backend} $out/lib/spud-router/backend
            cp -r ${frontend} $out/lib/spud-router/static
            # Store paths copied in via cp come back read-only; the running
            # app doesn't need to write into these, but chmod defensively so
            # a future update.py-style in-place mutation doesn't silently fail.
            chmod -R u+w $out/lib/spud-router

            mkdir -p $out/bin
            makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/spud-router \
              --add-flags "backend.main:app" \
              --chdir "$out/lib/spud-router"

            runHook postInstall
          '';

          meta = {
            description = "spud-router — router-on-a-stick management app";
            license = pkgs.lib.licenses.agpl3Plus;
          };
        };

        # ── nix develop ────────────────────────────────────────────────────
        devShells.default = pkgs.mkShell {
          packages = [
            devPythonEnv
            pkgs.nodejs_22
          ];

          shellHook = ''
            echo "spud-router dev shell (Python 3.12 + Node 22 — unvalidated, see flake.nix header)"
            echo "  backend tests:  cd backend && python -m pytest tests/ -q"
            echo "  backend dev:    uvicorn backend.main:app --reload --port 8080"
            echo "  frontend dev:   cd frontend && npm install && npm run dev"
          '';
        };
      });
}
