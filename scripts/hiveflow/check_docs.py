#!/usr/bin/env python3
"""Offline checks for the current documentation tree; no product code is run."""
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = {
    "credential-shaped token": r"\b(?:xox[baprs]-[A-Za-z0-9-]{12,}|sk-[A-Za-z0-9]{20,})\b",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "private network address": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
    "personal machine path": r"/(?:Users|home)/[A-Za-z0-9_.-]+/",
    "private chat link": r"https://app\.slack\.com/client/",
}


def prose(text):
    return re.sub(r"^```[^\n]*\n.*?^```[^\n]*$", "", text, flags=re.M | re.S)


def anchors(text):
    result, seen = set(), {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(text), re.M):
        heading = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
        slug = "".join(char for char in heading.lower().replace(" ", "-")
                       if char in "_-" or unicodedata.category(char)[0] in "LN")
        duplicate = seen.get(slug, 0)
        seen[slug] = duplicate + 1
        result.add(f"{slug}-{duplicate}" if duplicate else slug)
    return result


def check(paths):
    errors, links, markdown = [], 0, 0
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            errors.append(f"{path.relative_to(ROOT)}: file leaves repository or is a symlink")
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            errors.append(f"{path.relative_to(ROOT)}: unexpected binary file")
            continue
        label = path.relative_to(ROOT)
        for name, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, text):
                errors.append(f"{label}: review possible {name} (value withheld)")
        if path.suffix.lower() != ".md":
            continue
        markdown += 1
        for raw in re.findall(r"!?\[[^\n]*?\]\(([^)]+)\)", prose(text)):
            target = raw.strip().removeprefix("<").removesuffix(">")
            if urlsplit(target).scheme or target.startswith("//"):
                continue
            relative, _, fragment = unquote(target).partition("#")
            destination = (path.parent / relative).resolve() if relative else path
            links += 1
            if not destination.is_relative_to(ROOT):
                errors.append(f"{label}: link leaves repository")
            elif not destination.is_file():
                errors.append(f"{label}: missing local link {target}")
            elif fragment and destination.suffix.lower() == ".md" and fragment not in anchors(destination.read_text(encoding="utf-8")):
                errors.append(f"{label}: missing heading {target}")
    return markdown, links, errors


def main():
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    paths = sorted({ROOT / name for name in names if name and (name.startswith('docs/06-自动化/') or name.startswith('scripts/hiveflow/'))})
    markdown, links, errors = check(paths)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"FAILED: {len(errors)} issue(s)", file=sys.stderr)
        return 1
    print(f"OK: {markdown} Markdown files, {links} local links/anchors; common sensitive patterns not found.")
    print("Offline checks only; no external links, Git history or live integration verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
