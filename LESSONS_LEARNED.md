# Lessons Learned

## Release System

The release system has historically suffered from:

- Detached HEAD states
- rollback_active locks
- release creation deadlocks

Before modifying release logic:

1. Audit current runtime behavior.
2. Verify actual Git state.
3. Prefer minimal fixes.
4. Avoid architectural rewrites without evidence.

## Telegram WebView

Telegram WebView behaves differently from Chrome.

Any navigation changes must be tested inside:

- Telegram Android
- Telegram iOS (if available)

Do not assume browser behavior matches Chrome.

## Restart Philosophy

The project must remain operational even when deployer is unavailable.

Current priority:

1. Working restart
2. Working healthcheck
3. Working rollback
4. Nice diagnostics

Reliability is more important than beautiful status output.

## Debugging Rules

Before changing architecture:

- inspect logs
- inspect runtime state
- inspect Git state
- inspect database state

Evidence first, assumptions later.

## Stable Components

Stable as of May 2026 validation:

- Release workflow is stable
- Rollback workflow is stable
- Restart workflow is stable
- Safe restart fallback is stable
- Version detection is stable
- Telegram navigation bug is resolved

Treat these systems as stable.

Do not redesign them unless a reproducible bug exists.

## Verify Reality Before Trusting Diagnostics

Multiple incidents occurred where diagnostics, status screens, or assumptions did not reflect the actual runtime state.

Examples:

- `rollback_active` reported states that no longer matched reality.
- The repository appeared to be on the latest version while Git was actually in Detached HEAD.
- Restart status reported technical codes that obscured successful recovery.
- Version detection reported "unknown" while the application version existed and was running correctly.

Before changing code:

1. Verify runtime behavior.
2. Verify Git state.
3. Verify database state.
4. Verify container state.
5. Only then trust diagnostics.

Runtime reality is the source of truth.
Diagnostics are evidence, not proof.

## Historical Rule: Fix Root Causes, Not Symptoms

Several incidents appeared to require large architectural changes but were ultimately caused by a small isolated bug.

Examples:

- "Version: unknown" was caused by an incorrect version lookup, not by restart infrastructure.
- Release blocking issues were caused by rollback state logic, not by Git itself.

Before redesigning any subsystem:

1. Identify the exact failing component.
2. Find the root cause.
3. Prefer the smallest possible fix.
4. Avoid architectural rewrites unless the root cause genuinely requires one.

## Historical Rule: Collect Evidence First

Before investigating release, rollback, deployment, restart, or version issues, always collect:

- git status
- git branch -vv
- git log --oneline -15
- docker ps

These commands have repeatedly revealed the actual system state faster than diagnostics screens.

Do not modify code until this information has been reviewed.

## Historical Rule: Verify AI Claims

AI statements are not evidence.

Whenever an AI reports:

- "fixed"
- "removed"
- "rewritten"
- "verified"
- "fully implemented"

verify independently using:

- git diff
- grep
- git log
- runtime output
- actual application behavior

Several historical incidents occurred where reported changes did not fully exist in the codebase or did not match runtime reality.

Trust evidence over summaries.

## Historical Incidents

**1. Detached HEAD / rollback deadlock incident**
- **Symptoms:** New releases could not be deployed, and rollbacks failed or behaved unpredictably.
- **Root cause:** The Git repository was left in a Detached HEAD state after a previous rollback or checkout, causing subsequent Git operations to fail or create detached commits instead of advancing the branch.
- **Resolution:** Reset the Git state and configured rollback operations to return the repository to an attached state, based on recent validations.
- **Lessons learned:** Verify Git state (`git status`, `git branch -vv`) before and after release/rollback operations.

**2. rollback_active release lock incident**
- **Symptoms:** The system refused to create new releases, claiming a rollback was in progress when it was not.
- **Root cause:** The `rollback_active` flag in the database was not properly cleared after a failed or interrupted rollback attempt.
- **Resolution:** Manually cleared the lock and implemented safeguards to release the lock in observed failure cases.
- **Lessons learned:** State locks require robust error handling and timeouts to prevent deadlocks. Fix root causes, not just the symptoms of the lock.

**3. Telegram WebView navigation bug**
- **Symptoms:** Users clicking `/admin` or `/stats` links inside the Telegram WebView were unexpectedly redirected to the root `/` page, whereas Chrome users navigated fine.
- **Root cause:** Telegram's internal WebView handled standard `href`-based anchor navigation differently than modern standalone browsers.
- **Resolution:** Replaced `href`-based navigation with JavaScript-based routing (similar to the `/sky` implementation).
- **Lessons learned:** Do not assume browser behavior matches Telegram WebView. UI navigation changes should be tested inside Telegram clients on iOS and Android.

**4. Restart status "Version: unknown" incident**
- **Symptoms:** The diagnostic screen reported "Version: unknown" despite the application running a defined version successfully.
- **Root cause:** The status formatting logic attempted to read `PROJECT_VERSION` from `config.py`, where it did not exist, rather than checking `app_version.py`.
- **Resolution:** Updated the diagnostics to import the version directly from `app_version.py`.
- **Lessons learned:** Diagnostics are evidence, not proof. When a diagnostic fails, verify the runtime reality before assuming the core system is broken.

**5. Diagnostic state not matching runtime reality incident**
- **Symptoms:** Various dashboard values (like total test counts or restart codes like `restart_recovered_by_healthcheck`) obscured the true runtime state of the application.
- **Root cause:** Diagnostics were reading stale cache data, using internal technical codes instead of human-readable text, or relying on outdated architectural assumptions.
- **Resolution:** Updated diagnostic outputs to query live Docker states, live Git states, and map internal codes to clear explanations.
- **Lessons learned:** Runtime reality (Docker, Git, Database) is the primary source of truth. Collect evidence directly from the system before trusting a dashboard.

## AI Working Rules

When modifying this project:

1. Audit first.
2. Gather evidence before making changes.
3. Show diffs before applying changes.
4. Prefer minimal fixes.
5. Verify runtime behavior after modifications.
6. Avoid redesigning stable systems without a reproducible bug.
7. Verify claims using `git diff`, `git log`, `grep`, and runtime checks.

## Project Priorities

When decision-making, higher priorities take precedence if priorities conflict:

1. Data safety
2. Service availability
3. Successful rollback capability
4. Successful release capability
5. Accurate diagnostics
6. Convenience features
7. Cosmetic improvements

## Architectural Oddities

Certain components may look unusual but exist for historical reasons. Avoid simplifying these components without reviewing the historical context:

- **Safe restart fallback when deployer is unavailable:** Intended to keep the project operational and restartable even if the deployer microservice crashes.
- **Release and rollback safeguards:** Implemented to mitigate observed detached HEAD states and database corruption.
- **Telegram-specific navigation handling:** `href` links were replaced with JS navigation due to WebView quirks.
- **Version detection via `app_version.py`:** Intended to decouple the version from the runtime configuration (`config.py`).

## Never Again

Practices to avoid:

- Trusting diagnostics without verifying actual runtime state.
- Assuming Chrome behavior matches Telegram WebView.
- Performing architectural rewrites before identifying the exact root cause.
- Trusting AI summaries without concrete evidence (`git diff`, `grep`, logs).
- Modifying release/rollback systems casually.
- Assuming a fix exists without verifying the actual code and runtime behavior.
