"""既有私有控制分支的单文件CAS；不执行Issue中的命令。"""
import base64
import json
import re
import subprocess
from urllib.parse import quote
from adopted_issue import BRANCH, REPOSITORY, require


class GitHubError(RuntimeError):
    def __init__(self, method, status=None):
        super().__init__(f'GitHub {method} failed or result unknown; read original state before retry')
        self.status = status


class GitHubStore:
    def __init__(self):
        repo = self.api(f'repos/{REPOSITORY}')
        require(repo['full_name'] == REPOSITORY and repo['private'] is True, 'private repository mismatch')

    def api(self, endpoint, method='GET', payload=None):
        raise RuntimeError('Public reference export disables live GitHub transport; use authorized business repository')
        args = ['gh', 'api', '--hostname', 'github.com', '--method', method, endpoint]
        if payload is not None:
            args += ['--input', '-']
        try:
            result = subprocess.run(args, input=None if payload is None else json.dumps(payload),
                                    capture_output=True, text=True, check=False, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('GitHub result unknown after timeout; verify original state') from error
        if result.returncode:
            # 写入失败可能已经生效；不从错误正文推定可重试，也不泄露API正文。
            match = re.search(r'\(HTTP ([0-9]{3})\)', result.stderr)
            raise GitHubError(method, int(match[1]) if match else None)
        return json.loads(result.stdout)

    def read(self, path):
        require(path.startswith('.hiveflow/control/') and '..' not in path, 'control path')
        try:
            obj = self.api(f'repos/{REPOSITORY}/contents/{quote(path, safe="/")}?ref={BRANCH}')
        except GitHubError as error:
            if error.status != 404:
                raise
            # 只有404才补读真实分支和完整树；不把权限不足当缺文件。
            ref = self.api(f'repos/{REPOSITORY}/git/ref/heads/{BRANCH}')
            commit = self.api(f'repos/{REPOSITORY}/git/commits/{ref["object"]["sha"]}')
            tree = self.api(f'repos/{REPOSITORY}/git/trees/{commit["tree"]["sha"]}?recursive=1')
            require(tree.get('truncated') is False, 'incomplete control tree')
            require(not any(row['path'] == path for row in tree['tree']), 'content unavailable or raced; stop')
            return None, None
        require(obj['type'] == 'file' and obj['encoding'] == 'base64'
                and type(obj['sha']) is str and re.fullmatch('[0-9a-f]{40}', obj['sha']), 'control file identity')
        return json.loads(base64.b64decode(obj['content'])), obj['sha']

    def write(self, path, record, sha):
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + '\n'
        payload = {'branch': BRANCH, 'message': 'chore(hiveflow): 更新受控执行记录',
                   'content': base64.b64encode(raw.encode()).decode()}
        if sha is not None:
            payload['sha'] = sha
        # 只提交一次。冲突不获取新SHA覆盖赢家，未知不重发。
        self.api(f'repos/{REPOSITORY}/contents/{quote(path, safe="/")}', 'PUT', payload)
        actual, actual_sha = self.read(path)
        require(actual == record, 'write readback mismatch; stop')
        return actual_sha
