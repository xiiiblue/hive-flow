# HiveFlow boundaries

- HiveFlow owns Issue parsing, immutable identity/revisions, proposal and decision schema, business configuration and authorization. It also owns dormant execution controls and future orchestration.
- The public WeCom–Slack bridge owns authentication, transport, routing, outbox, deduplication, and ephemeral owner-question correlation. It must not depend on HiveFlow or interpret business configuration.
- Compose one process through the bridge exported runtime and trusted application hooks. Use one WeCom connection, the existing service identity/environment file, and the existing absolute state path. Never start a second bridge against the same Bot ID.
- Retain the shared SQLite file so decisions and outbox writes remain atomic. Extraction changes logical ownership, not data location. Preserve all historical business records, stable IDs and cross-references; do not invent an export/import requirement or delete state.
- Workflow-control remains dormant with execution readiness false. Do not enable automation, connect services, deploy, or reinterpret record-only decisions as execution grants.
- Future automatic dispatch uses the dormant AutoQueue and the same Store/ManualDispatch slot as manual work. Initialize its restart recovery before exposing future control ingress. Do not wire the older standalone WorkflowControl database as a second production scheduler.
- Trusted adapters must authenticate actors, verify quota provenance, enforce repository/file scope, and check authorization before every execution step. Actor strings are not authentication.
- Preserve fail-closed revision, quota, expiry, claim, restart and checkpoint behavior. Unknown custom kinds must remain preserved and non-deliverable without the matching application.
- Use Node.js 24+, ES modules and built-in APIs where practical. The bridge runtime is a justified local dependency (`file:../wecom-slack-bridge`); fixed releases deploy both sibling directories together.
- Never copy credentials, private runtime identifiers, production databases, environments or unapproved third-party private source into this repository. Do not select a license without the owner’s decision.
- Run `npm run check` before reporting completion. Production actions and publishing require separate authorization.
