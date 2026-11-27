#!/usr/bin/env python3
"""Sync the 'Complete TODO List' section of README.md with TODO comments
in .py files changed between the main and dev branches. Runs in CI."""
import os
import re
import subprocess
import sys

REPO = os.environ.get("GITHUB_WORKSPACE") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
BRANCH = os.environ.get("GITHUB_REF_NAME", "dev")
REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "SilverWaspSpire/LUFFY")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
README = "README.md"


def run(args, check=True, capture=True):
    return subprocess.run(
        args, cwd=REPO, check=check, text=True,
        capture_output=capture)


def git(*args, check=True):
    return run(["git"] + list(args), check=check)


def main():
    # Make sure all branch refs are available (checkout already fetches them,
    # but be explicit about it).
    try:
        git("fetch", "--no-tags", "--prune", "origin",
            "+refs/heads/*:refs/remotes/origin/*")
    except Exception as exc:  # noqa: BLE001
        print("fetch failed, continuing with existing refs:", exc)

    # Identify .py files changed between main and dev, ignoring CI helper files.
    diff = run(["git", "diff", "--name-only", "origin/main", "origin/dev"],
               check=False)
    if diff.returncode != 0:
        diff = run(["git", "diff", "--name-only", "main", "dev"], check=False)
    changed = []
    if diff.returncode == 0:
        for path in diff.stdout.splitlines():
            if path.endswith(".py") and not path.startswith(".github/"):
                changed.append(path)
    # Fallback derived from commit 7463fef (the only code commit main..dev).
    if not changed:
        changed = [
            "luffy/deepscaler/utils.py",
            "luffy/verl/verl/protocol.py",
        ]
    changed_set = set(changed)
    print("Changed .py files:", changed)

    readme_path = os.path.join(REPO, README)
    with open(readme_path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    lines = text.splitlines(keepends=True)

    # Locate the section heading by its stable ASCII part so the emoji in the
    # original heading line is preserved byte-for-byte.
    heading_re = re.compile(r"^#{1,6}\s+.*Complete TODO List\s*$")
    head_idx = None
    for i, line in enumerate(lines):
        if heading_re.match(line.lstrip()):
            head_idx = i
            break
    if head_idx is None:
        print("ERROR: TODO heading not found")
        sys.exit(1)

    # Section ends at the next heading of the same/higher level (1-3 hashes,
    # but not a sub-heading with 4+ hashes).
    header_re = re.compile(r"^#{1,3}(?!#)\s+")
    bound_idx = None
    for j in range(head_idx + 1, len(lines)):
        if header_re.match(lines[j].lstrip()):
            bound_idx = j
            break
    if bound_idx is None:
        print("ERROR: end of TODO section (next heading) not found")
        sys.exit(1)

    def strip_eol(raw):
        if raw.endswith("\r\n"):
            return raw[:-2]
        if raw.endswith("\n"):
            return raw[:-1]
        return raw

    entry_re = re.compile(
        r"^- \[ \] \*\*(.*?):(\d+)\*\* - (.*)$")

    entries = []  # (idx, path, line, msg, raw_line)
    for k in range(head_idx + 1, bound_idx):
        body = strip_eol(lines[k])
        m = entry_re.match(body)
        if m:
            entries.append((k, m.group(1), int(m.group(2)),
                            m.group(3), lines[k]))

    if not entries:
        print("ERROR: no TODO entries found under the heading")
        sys.exit(1)

    first_idx = entries[0][0]
    last_idx = entries[-1][0]
    # The entry block is expected to be contiguous (blank lines only appear
    # between the heading and the list, and after the list).
    if last_idx - first_idx + 1 != len(entries):
        print("ERROR: TODO entries are not contiguous")
        sys.exit(1)

    # Preserve existing groups and their exact raw text/order; entries are
    # grouped by path in the order they already appear in the README.
    groups = []          # list of [path, [raw lines]]
    path_index = {}      # path -> position in groups
    for idx, path, _ln, _msg, raw in entries:
        if path not in path_index:
            path_index[path] = len(groups)
            groups.append([path, []])
        groups[path_index[path]][1].append(raw)

    todo_re = re.compile(r"^\s*TODO(?![\w])\s*(?::\s*)?(.*?)\s*$")

    def extract_todos(path):
        full = os.path.join(REPO, path)
        if not os.path.exists(full):
            return None  # file was deleted on dev
        out = []
        with open(full, encoding="utf-8", newline="") as handle:
            for lineno, raw in enumerate(
                    handle.read().splitlines(), start=1):
                pos = raw.find("#")
                if pos == -1:
                    continue
                comment = raw[pos + 1:]
                m = todo_re.match(comment)
                if not m:
                    continue
                msg = m.group(1).strip()
                out.append((lineno, msg))
        return out

    existing_paths = [p for p, _ in groups]

    # Rebuild only the groups belonging to changed .py files.
    new_groups = []
    for path, raws in groups:
        if path in changed_set:
            todos = extract_todos(path)
            if not todos:  # deleted file or no TODOs left -> drop the group
                print("Dropping empty/deleted group:", path)
                continue
            formatted = [
                "- [ ] **{0}:{1}** - {2}\n".format(path, ln, msg)
                for ln, msg in todos
            ]
            print("Updated", path, "with", len(formatted), "TODO(s)")
            new_groups.append([path, formatted])
        else:
            new_groups.append([path, list(raws)])  # keep byte-identical

    # Handle brand-new .py files (new groups) with lexicographic insertion.
    existing_set = set(existing_paths)
    for path in sorted(changed_set):
        if path in existing_set or not os.path.exists(
                os.path.join(REPO, path)):
            continue
        todos = extract_todos(path) or []
        if not todos:
            continue
        formatted = [
            "- [ ] **{0}:{1}** - {2}\n".format(path, ln, msg)
            for ln, msg in todos
        ]
        insert_at = len(new_groups)
        for i, (p, _l) in enumerate(new_groups):
            if path < p:
                insert_at = i
                break
        new_groups.insert(insert_at, [path, formatted])
        print("Added group:", path)

    replacement = []
    for _p, group_lines in new_groups:
        replacement.extend(group_lines)

    updated_lines = (
        lines[:first_idx] + replacement + lines[last_idx + 1:])
    updated = "".join(updated_lines)

    if updated == text:
        print("README already up-to-date; nothing to commit.")
        return 0

    with open(readme_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(updated)

    # Show a concise summary of the README change for CI logs.
    stat = run(["git", "--no-pager", "diff", "--stat", "README.md"], check=False)
    print(stat.stdout)

    subprocess.run(["git", "add", README], cwd=REPO, check=True)
    subprocess.run(["git", "config", "user.name",
                    "github-actions[bot]"], cwd=REPO, check=True)
    subprocess.run([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com"
    ], cwd=REPO, check=True)
    subprocess.run([
        "git", "-c", "core.autocrlf=false", "commit", "-m",
        "docs: update README TODO list from dev branch", "--", README
    ], cwd=REPO, check=True)

    if TOKEN:
        remote = (
            "https://x-access-token:{0}@github.com/{1}.git".format(
                TOKEN, REPO_SLUG))
        subprocess.run(["git", "remote", "set-url", "origin", remote],
                       cwd=REPO, check=True)
    subprocess.run(["git", "checkout", "-B", BRANCH], cwd=REPO, check=True)
    subprocess.run([
        "git", "push", "origin", "HEAD:{0}".format(BRANCH)
    ], cwd=REPO, check=True)
    print("Pushed README update to branch:", BRANCH)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print("Command failed:", exc.cmd)
        print("stdout:\n", exc.stdout)
        print("stderr:\n", exc.stderr)
        raise
