import { startBridge } from 'wecom-slack-bridge/runtime';
import { createIssueReviewApplication } from './issue-review.js';

// Only the explicit service entry starts networking. Workflow execution remains dormant.
await startBridge({ createApplication: ({ env }) => createIssueReviewApplication(env) });
