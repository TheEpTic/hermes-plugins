# Hermes Desktop Operations Console Implementation Plan

> **For Hermes:** use direct tools by default; use agent workflows only when Jack explicitly asks.

**Goal:** Add opt-in Hermes Desktop integrations for `hermes-ssh` and `hermes-sfw` without duplicating their existing Python execution logic or exposing credentials.

**Architecture:** Each plugin keeps its current tool manager as the source of truth. A small `dashboard/plugin_api.py` exposes bounded, redacted FastAPI routes under the plugin namespace, and a plain-JavaScript ESM desktop plugin calls those routes through `ctx.rest`. SSH starts with read-only inventory/session status plus explicitly confirmed session controls; SFW starts with dependency-guard health plus explicitly confirmed runs. Desktop enablement remains separate from the gateway's `plugins.enabled` gate and is documented as such.

**Tech Stack:** Python 3.11+, FastAPI/APIRouter, pytest, existing `SSHManager`/`SFWManager`, Hermes Desktop Plugin SDK, plain ESM React hooks, and bounded polling.

---

## Scope and safety boundaries

- Ship both integrations opt-in with `defaultEnabled: false`.
- Never return SSH passwords, private keys, connection strings, environment values, control paths, or raw configuration from dashboard responses.
- Keep SSH inventory profile-awareness honest: the current manager storage is global under `~/.hermes/ssh-tools`, so the UI labels it as shared inventory rather than implying profile isolation.
- Require `confirm: true` for SSH command execution, machine writes, session killing, cleanup, and SFW command execution. The backend re-validates commands through the existing manager/approval paths.
- Do not add transfer UI or arbitrary gateway RPC in this first slice.
- Poll status at a bounded interval and retain a non-socket fallback. No gateway restart is part of installation or deployment.

## Implementation tasks

### Task 1: Establish the dashboard response contracts

**Files:**
- Create: `hermes-ssh/src/ssh_tools/dashboard/manifest.json`
- Create: `hermes-ssh/src/ssh_tools/dashboard/plugin_api.py`
- Create: `hermes-sfw/src/hermes_sfw/dashboard/manifest.json`
- Create: `hermes-sfw/src/hermes_sfw/dashboard/plugin_api.py`
- Test: `hermes-ssh/tests/test_dashboard_api.py`
- Test: `hermes-sfw/tests/test_dashboard_api.py`

1. Write tests for redacted SSH machine/session projections, bounded audit results, rejected unconfirmed mutations, and SFW status/run confirmation behavior.
2. Run the focused tests and confirm they fail because the dashboard modules do not exist.
3. Implement only adapter helpers and routes. Reuse `get_manager()`, `SSHManager`, `SFWManager`, existing command validation, approval checks, and result serialization.
4. Run the focused tests again and confirm they pass.

### Task 2: Make managers safely reusable by the dashboard adapters

**Files:**
- Modify: `hermes-ssh/src/ssh_tools/__init__.py`
- Modify: `hermes-sfw/src/hermes_sfw/__init__.py`
- Test: existing registration/tool suites plus dashboard tests

1. Add a lazy public manager accessor that returns the registration-owned manager when available and creates one for dashboard-only discovery when needed.
2. Keep the existing tool registration lifecycle and disposal behavior unchanged.
3. Run the existing focused suites and the new dashboard tests.

### Task 3: Add the SSH desktop plugin

**Files:**
- Create: `hermes-ssh/desktop-plugins/hermes-ssh/plugin.js`
- Test: `hermes-ssh/tests/test_desktop_assets.py`

1. Add a native status-bar chip with active/idle counts and an error state.
2. Add an `/ssh-operations` route with a sidebar entry and a page showing shared machines, sessions, bounded output polling, audit metadata, and explicit poll/kill/terminal controls. Cleanup remains available through the confirmed API for a later UI action.
3. Add a palette command for opening SSH. Use only `@hermes/plugin-sdk` and `react` (the runtime loader's supported React shim), plus theme variables.
4. Keep action handlers explicit and use `host.notify` for bounded success/error feedback.
5. Test asset existence, manifest wiring, and the no-secret/no-compiled-React import contract.

### Task 4: Add the SFW desktop plugin

**Files:**
- Create: `hermes-sfw/desktop-plugins/hermes-sfw/plugin.js`
- Test: `hermes-sfw/tests/test_desktop_assets.py`

1. Add a status-bar chip that reports guard availability and direct-terminal enforcement.
2. Add an `/sfw` route with status, a command/workdir form, explicit execution confirmation, and bounded stdout/stderr/result rendering.
3. Describe this surface as a dependency guard, not a sandbox, because accepted package operations can execute lifecycle scripts/build backends.
4. Test asset existence, manifest wiring, and the no-secret/no-compiled-React import contract.

### Task 5: Wire packaging, source deployment, and documentation

**Files:**
- Modify: `hermes-ssh/pyproject.toml`
- Modify: `hermes-sfw/pyproject.toml`
- Modify: `hermes-ssh/deploy.sh`
- Modify: `hermes-sfw/deploy.sh`
- Modify: package READMEs and root `README.md`
- Test: packaging/deployment asset tests

1. Include dashboard manifests/APIs and desktop plugin files in source distributions and wheels where supported by the package layout.
2. Extend source deployment to install/symlink both the Python plugin and its dashboard/desktop assets without copying secrets.
3. Document the separate gateway plugin and live desktop enablement steps, shared SSH inventory, polling behavior, and confirmation boundaries.
4. Build both packages and inspect the resulting archives for the expected assets.

### Task 6: Review, verify, and publish

1. Run focused SSH/SFW tests first.
2. Run both package suites, formatting/type checks that exist in each package, and wheel/sdist builds.
3. Run `git diff --check`, inspect the complete diff, and scan added content for credentials, shell injection, arbitrary command execution, and unbounded response fields.
4. Commit the focused change, push `feat/desktop-operations-console`, create the PR against `main`, and verify the PR head SHA, mergeability, and CI checks.

## Acceptance criteria

- Both manifests discover valid API modules and both desktop plugins load as plain ESM using the SDK plus the runtime loader's supported React shim.
- SSH and SFW dashboard tests cover positive and negative confirmation paths and prove sensitive fields are not serialized.
- Existing tool behavior and registration tests remain green.
- Deployment/build artifacts contain the dashboard and desktop assets.
- The PR contains no credentials, unrelated Hermes Agent changes, transfer UI, or gateway restart side effects.
