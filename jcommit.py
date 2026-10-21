#!/usr/bin/env python3
"""jcommit - catch Jenkins' commit-message rejections before you push.

Required format (one commit = exactly these three lines):

    <Type>: <JIRA-ID>
    Title: <exact JIRA summary>
    Description: <your own words, >= 200 characters>

Commands:
    jcommit check <msg-file>   validate a message (used by the git hook)
    jcommit new                compose a valid message and run `git commit`
    jcommit install            install the commit-msg hook in this repo
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

TYPES = ("Bug", "Feat", "TechDebt", "Compliance")
JIRA_PREFIXES = ("SMSD", "SC", "SD")
MIN_DESCRIPTION = 200

JIRA_RE = re.compile(r"^(%s)-(\d+)$" % "|".join(JIRA_PREFIXES))
BRANCH_JIRA_RE = re.compile(r"(?<![A-Za-z0-9])((?:%s)-\d+)" % "|".join(JIRA_PREFIXES))
HOOK_MARKER = "# installed by jcommit"
CACHE_PATH = Path(os.environ.get("JCOMMIT_CACHE", Path.home() / ".config/jcommit/titles.json"))


def validate(message):
    """Return (errors, warnings) for a commit message string."""
    lines = [l.rstrip() for l in message.splitlines() if not l.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    errors, warnings = [], []

    # git-generated merge/revert commits carry no JIRA context; don't block them.
    if lines and re.match(r"^(Merge |Revert \")", lines[0]):
        return errors, ["merge/revert commit, format not enforced"]
    if len(lines) < 3:
        return ["need 3 lines: '<Type>: <JIRA-ID>', 'Title: ...', 'Description: ...' (got %d)" % len(lines)], warnings

    head = re.match(r"^(\w+):\s*(\S+)\s*$", lines[0])
    if not head:
        errors.append("line 1 must look like '<Type>: <JIRA-ID>', e.g. 'Bug: SC-991'")
    else:
        ctype, jira = head.groups()
        if ctype not in TYPES:
            errors.append("Type '%s' is invalid, use one of: %s" % (ctype, ", ".join(TYPES)))
        m = JIRA_RE.match(jira)
        if not m:
            errors.append("JIRA-ID '%s' must be %s-<number>" % (jira, "/".join(JIRA_PREFIXES)))
        elif int(m.group(2)) == 0:
            errors.append("JIRA-ID '%s' is a placeholder" % jira)

    title = re.match(r"^Title:\s*(.+?)\s*$", lines[1])
    if not title:
        errors.append("line 2 must be 'Title: <exact JIRA summary>'")

    desc = re.match(r"^Description:\s*(.*)$", lines[2])
    if not desc:
        errors.append("line 3 must start with 'Description: '")
    elif len(desc.group(1).strip()) < MIN_DESCRIPTION:
        n = len(desc.group(1).strip())
        errors.append("Description is %d chars, needs >= %d (%d more)" % (n, MIN_DESCRIPTION, MIN_DESCRIPTION - n))

    extra = [l for l in lines[3:] if l.strip()]
    if extra:
        warnings.append("%d extra line(s) after Description (e.g. trailers); Jenkins may not accept them" % len(extra))
    return errors, warnings


# --- title cache: Jenkins needs the *exact* JIRA summary, so remember it once ---

def load_cache():
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def jira_from_branch(branch):
    m = BRANCH_JIRA_RE.search(branch or "")
    return m.group(1) if m else None


def build_message(ctype, jira, title, description):
    return "%s: %s\nTitle: %s\nDescription: %s\n" % (ctype, jira, title.strip(), " ".join(description.split()))


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


# --- commands ---

def cmd_check(args):
    errors, warnings = validate(Path(args.file).read_text())
    for w in warnings:
        print("jcommit: warning: " + w, file=sys.stderr)
    if errors:
        print("jcommit: commit message would fail Jenkins:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        print("Tip: `jcommit new` builds a valid message for you.", file=sys.stderr)
        return 1
    return 0


def ask(prompt, default=None):
    suffix = " [%s]" % default if default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        sys.exit("jcommit: no input for '%s'; pass it as a flag" % prompt)
    return value or default


def cmd_new(args):
    cache = load_cache()
    jira = args.jira or jira_from_branch(git("symbolic-ref", "--short", "HEAD")) or ask("JIRA ID")
    ctype = args.type or ask("Type (%s)" % "/".join(TYPES), "Bug")
    title = args.title or ask("Title (exact JIRA summary)", cache.get(jira))
    description = args.description
    if description is None:
        print("Description (>= %d chars), then Enter:" % MIN_DESCRIPTION)
        description = input("> ")
    message = build_message(ctype, jira, title or "", description)
    errors, _ = validate(message)
    if errors:
        print("Not committing:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        return 1
    cache[jira] = title.strip()
    save_cache(cache)
    if args.print:
        print(message, end="")
        return 0
    return subprocess.run(["git", "commit", "-F", "-", *args.git_args], input=message, text=True).returncode


HOOK_BODY = """#!/bin/sh
%s
exec python3 "%s" check "$1"
"""


def cmd_install(args):
    hooks = git("rev-parse", "--git-path", "hooks")
    if not hooks:
        print("jcommit: not inside a git repository", file=sys.stderr)
        return 1
    hook = Path(hooks) / "commit-msg"
    if hook.exists() and HOOK_MARKER not in hook.read_text() and not args.force:
        print("jcommit: %s already exists and isn't ours; use --force to replace" % hook, file=sys.stderr)
        return 1
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_BODY % (HOOK_MARKER, Path(__file__).resolve()))
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("jcommit: installed %s" % hook)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="jcommit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate a commit message file")
    c.add_argument("file")
    c.set_defaults(fn=cmd_check)

    n = sub.add_parser("new", help="compose a valid message and commit")
    n.add_argument("-t", "--type", choices=TYPES)
    n.add_argument("-j", "--jira", help="default: parsed from the branch name")
    n.add_argument("--title", help="default: remembered from last time")
    n.add_argument("-d", "--description")
    n.add_argument("--print", action="store_true", help="print the message instead of committing")
    n.add_argument("git_args", nargs="*", help="extra args for git commit (after --)")
    n.set_defaults(fn=cmd_new)

    i = sub.add_parser("install", help="install the commit-msg hook")
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_install)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
