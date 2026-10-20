#!/usr/bin/env python3
"""检查独立副本的来源摘要、全部文档链接和常见敏感形态，不联网。"""
import json
from pathlib import Path
import subprocess
import sys
from sync_from_hiveos import MANIFEST, validate_export

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/hiveflow'))
from check_docs import check


def main():
    try:
        manifest = json.loads((ROOT / MANIFEST).read_text())
        errors = validate_export(ROOT, manifest)
    except (OSError, ValueError, TypeError) as error:
        print(f'来源清单无法检查：{type(error).__name__}', file=sys.stderr)
        return 1
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    markdown, links, found = check(sorted({ROOT / n for n in names if n}))
    errors += found
    for message in errors:
        print(message, file=sys.stderr)
    if errors:
        print(f'检查失败：{len(errors)}项', file=sys.stderr)
        return 1
    print(f"检查通过：{len(manifest['files'])}份来源文件，{markdown}份Markdown，{links}个本地链接与锚点。")
    print('离线摘要不能证明上游身份、外链可访问、全部历史安全或真实业务已通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
