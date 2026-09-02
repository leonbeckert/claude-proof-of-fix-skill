---
name: proof-of-fix-setup
description: Onboard the current project for proof-of-fix captures — write .proof-of-fix/config.json, gitignore it, and validate everything with a mandatory live smoke capture. Interactive; run once per project with a human present.
---

# proof-of-fix-setup

Onboards the current project: produces a validated `.proof-of-fix/config.json`. **Not done until the smoke capture passes** — a config that was never exercised fails at the first real run, which is exactly when nobody is watching.

## Input

From the user: nothing up front — ask only what the repo can't answer (e.g., which seed roles matter, whether auto-open should stay on).

From project files (read before asking):
- App manifest (`package.json` scripts: dev command, port), health endpoint under the app's `api/` routes, auth setup (NextAuth? test-only login-as endpoint? seed user docs), existing Playwright installs (`node_modules/playwright`), established viewports in e2e configs.

## Process

1. **Scout the repo** for the facts above. Propose a draft config; confirm open points with the operator in ONE round of questions.
2. **Write `.proof-of-fix/config.json`:**
   ```json
   {
     "app_dir": "web",
     "port": 3000,
     "base_url": "http://localhost:3000",
     "health_path": "/api/health",
     "dev_cmd": "npm run dev",
     "server_env": {"APP_TEST_LOGIN_ENABLED": "true", "APP_TEST_LOGIN_SECRET": "local-dev-dummy-not-a-real-secret"},
     "playwright_root": "web",
     "auth": {
       "method": "hmac-login-as",
       "login_as_path": "/api/test/login-as",
       "secret_env": "APP_TEST_LOGIN_SECRET",
       "secret_default": "local-dev-dummy-not-a-real-secret",
       "default_role": "admin",
       "roles": {"admin": {"email": "admin@example.test"}}
     },
     "viewports": {"desktop": {"width": 1920, "height": 1080}, "mobile": {"width": 375, "height": 667}},
     "auto_open": true,
     "restart_on_after": true
   }
   ```
   Auth variants: `"method": "form"` needs `login_path`, `user_selector`, `pass_selector`, `submit_selector`, `post_login_selector`, and per-role `email` + `password_env` (never a literal password — point to an env var or the repo's documented seed-password file). `"method": "none"` for unauthenticated apps.
   Only seed/test accounts go in `roles` — this map is the mechanical boundary that keeps real user data out of captures.
3. **Gitignore:** ensure the project root `.gitignore` contains `.proof-of-fix/`. Captures are screenshots of a logged-in app; even against seed data they do not belong in git history.
4. **Permissions:** confirm the target `.claude/settings.local.json` has the allow-rules `install.sh` adds (phase.sh, selftest.sh, Read/Write/Edit on `.proof-of-fix/**`). Without them, headless runs get silently denied.
5. **Smoke capture (mandatory):** run a real end-to-end pair against a harmless route, e.g. issue `smoke-setup`, `before` on `/` with `expect` = something guaranteed visible + a machine `assert`, then `after` immediately (no fix — symptom identical, so use `expect_after`/`assert_after` matching the same state). Success criteria: both result.json `success`, deliverable exists and opens. This is where wrong ports, CSRF-origin mismatches (whatever your framework uses for canonical URL and allowed origins must match the browser origin — this is the single most common setup failure), and broken login configs surface.
6. **Clean up** the smoke issue dir after the operator has seen it open, and summarize: config path, roles configured, known limitations.

## When to clarify

- Multiple candidate app dirs or dev commands → ask, don't guess.
- No test-login endpoint and no documented seed credentials → stop and ask; never configure a real user's account.

## Rules

- The smoke capture is not skippable, not even "because the config is obviously right" — that claim is exactly what it exists to test.
- Never write secrets (passwords, tokens) into config.json; reference env vars. `secret_default` is acceptable only for documented local-dev dummy secrets. A literal `password_plain` in a role entry is refused by `record.mjs` with `AUTH_FAILED`, so this is enforced rather than merely advised.

## Good example

Scout finds a `web/` app dir, `next dev`, and `/api/health`, but no test-login endpoint → propose `method: "form"`, ask the operator which seed account to use, wire the selectors off the real login page, smoke capture fails first on a CSRF origin mismatch (the dev port in `.env` differs from the port proof-of-fix binds) → note the fix in the config, rerun green, done.

## Bad example

Writing config.json from the scout facts alone and skipping the smoke capture because "the config from the sibling project worked and this repo looks identical". Sibling projects diverge exactly where it hurts, usually in auth — the first real headless run then dies in login, unattended, and the caller polls a result.json reporting a server error instead of the config error a smoke run would have surfaced while a human was watching.
