import { startBridge } from 'wecom-slack-bridge/runtime';
import { createIssueReviewApplication, loadManualExecutionEnabled } from './issue-review.js';

// Only the explicit service entry starts networking. Manual protocol is opt-in; no executor or scanner is started here.
await startBridge({ createApplication: ({ env }) => createIssueReviewApplication(env, { manualExecutionEnabled: loadManualExecutionEnabled(env) }) });
