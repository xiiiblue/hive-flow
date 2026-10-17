# HiveFlow boundaries

- HiveFlow owns issue lifecycle, execution approvals, manual/automatic modes, quota gates, durable claims, and future Codex orchestration.
- The separate WeCom–Slack bridge owns generic message transport, authentication, routing, retry, and deduplication. Keep workflow policy out of the bridge; do not modify it from this project.
- Current code is a dormant local persistence/gating primitive, not a web service, scanner, executor, or live integration. New state defaults to manual; execution readiness defaults to false. Do not enable automation, connect services, or deploy implicitly.
- Authenticate actors in trusted adapters; actor strings alone are not authentication. Validate provider quota provenance, enforce approved repository/file scope, and check authorization before every execution step. Review records never imply execution permission.
- Preserve fail-closed quota, revision, expiry, single-claim, restart/recovery, and checkpoint behavior. Add focused tests for any change.
- Keep this project minimal: Node.js 24+, ES modules, built-in APIs, no runtime dependencies unless explicitly justified.
- Never copy credentials, private identifiers, runtime databases/state, production configuration, dependencies, or third-party private source here. Do not select a license without the owner's decision.
- Run `npm run check` before reporting a change complete.
