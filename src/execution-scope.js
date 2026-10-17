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
