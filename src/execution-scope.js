// Exact relative paths only. This conservative syntax/credential guard is not
// a filesystem sandbox: trusted adapters must still verify real paths/symlinks.
const templateEnv = new Set(['.env.example', '.env.sample', '.env.template']);
export function isExecutionPath(path) {
  if (typeof path !== 'string' || Buffer.byteLength(path) > 300 || !/^[A-Za-z0-9_.\/-]+$/.test(path)) return false;
  return path.split('/').every(segment => {
    const name = segment.toLowerCase();
    return segment && segment !== '.' && segment !== '..' && !['.git', '.ssh', '.aws'].includes(name)
      && !(name === '.env' || (name.startsWith('.env.') && !templateEnv.has(name)));
  });
}

export const EXECUTION_OPERATIONS = Object.freeze(['edit-code', 'run-tests', 'commit', 'push', 'open-pr', 'merge', 'deploy']);
export const DELIVERY_OPERATIONS = Object.freeze(['commit', 'push', 'open-pr', 'merge', 'deploy']);
const exact = (value, fields) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === fields.length && fields.every(key => Object.hasOwn(value, key));
const branch = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/.test(value) && value !== 'HEAD' && !value.includes('..') && value.split('/').every(part => part && !part.startsWith('.') && !part.endsWith('.') && !part.endsWith('.lock'));
const label = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$/.test(value);
const description = value => typeof value === 'string' && value.trim() && Buffer.byteLength(value) <= 1000 && !/[\x00-\x1f\x7f]/.test(value);
// Delivery contains reviewed target identifiers and descriptions, never commands
// to evaluate. Actual repository/host/service identity must be verified separately.
export function isExecutionDelivery(value, operations) {
  if (!Array.isArray(operations)) return false;
  if (!operations.some(op => DELIVERY_OPERATIONS.includes(op))) return value === undefined;
  if (!exact(value, ['target_branch', 'work_branch', 'deployment']) || !branch(value.target_branch) || !branch(value.work_branch) || value.target_branch === value.work_branch) return false;
  if (!operations.includes('deploy')) return value.deployment === null;
  const d = value.deployment;
  return !!(exact(d, ['environment', 'service', 'verification', 'rollback']) && label(d.environment) && label(d.service) && description(d.verification) && description(d.rollback));
}
