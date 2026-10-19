#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Build the public BCD tree as a commit, locally (the open-fork plan, docs/YOL-HARITASI-FORK.md).

The full repository stays private on the Mac. A release gets one commit in a separate
local repository, ~/madeira-local/kaynak/bcd-public.git, whose history is upstream
Madeira's followed by BCD's release commits. Nothing here talks to GitHub: pushing that
repository is a separate, deliberate step at release time.

    tools/export-public.py --version 0.1.570              export auto-tune's tip
    tools/export-public.py --version 0.1.570 --source <commit> --dry-run
    tools/export-public.py --version 0.1.570 --dry-run --source-archive /tmp/bcd-0.1.570-source.tar.xz

- The tree is the source commit's with the private paths removed (PRIVATE below: the
  agents' notes, docs/ but what the build and the notices need, research/ but the headers
  DXMT compiles, the log intake, private tools, Apple's converter dylib) and .gitmodules
  pointing at BCD's public mirrors (MIRRORS). Submodule pins stay exactly as they are.
- README-BCD.md becomes the public README.md (upstream's README.md stays private, so
  upstream merges never conflict on it).
- No documentation is published (owner, 2026-10-18): docs/ stays private, and so does every
  other Markdown file except the public README, the licences, the third-party notices and
  CONTRIBUTING.md (the contribution terms). The build procedure the LGPL notice points to is
  the workflow and its scripts, which are published.
- Complete source (GPL/LGPL; legal audit F-2): research/remote-metal, whose headers DXMT's
  winemetal compiles, is kept (its README is not). Each release also needs the source archive with every submodule at its pin
  (--source-archive), since GitHub's own archives leave submodules out.
- Every file of the result is scanned, case-insensitively, for the private needles
  (~/madeira-local/state/alpha-needles.txt, never in the repository) and for PATTERNS.
  One hit and nothing is written; hits are reported by file and number only.
- Refuses while LICENSE-BCD.md is not adopted (its "Adopted on:" line is blank), unless
  --draft (then the commit is marked DRAFT and must not be published).
- Parents: the previous public commit, if any, and the upstream commit the source last
  merged (a merge when upstream moved), so the public history shows both.
"""
import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

KAYNAK = Path.home() / "madeira-local" / "kaynak"
SOURCE_REPO = KAYNAK / "madeira-bcd.git"
UPSTREAM_REPO = KAYNAK / "upstream" / "Madeira.git"
PUBLIC_REPO = KAYNAK / "bcd-public.git"
NEEDLES = Path.home() / "madeira-local" / "state" / "alpha-needles.txt"

# Paths that stay private. A directory removes everything under it.
PRIVATE = ["CLAUDE.md", "AGENTS.md", "docs", "bcd-logs", "research", "tools/pull-logs.py",
           ".github/workflows/cleanup-artifacts.yml", ".github/workflows/inspect-ipa.yml",
           ".github/workflows/protect-refs.yml", ".github/workflows/sdk-probe.yml",
           # Apple's Metal Shader Converter: only inside IPAs (its agreement, section 2.B).
           "app/Madeira/d3d12/libmetalirconverter.dylib",
           # Upstream's README; README-BCD.md is published as README.md instead.
           "README.md", "README-BCD.md"]
# ...except these, which the build and the shipped notices need (a directory keeps all of it).
KEEP = ["docs/local-build/BUILD-NUMBER", "research/remote-metal"]
# Markdown that is published: the README (from README-BCD.md), the licences and notices, and
# the contribution terms. Every other .md file in the tree is documentation and stays private.
MARKDOWN_PUBLIC = {"README.md", "LICENSE-BCD.md", "LICENSE-EXCEPTION.md", "THIRD-PARTY-NOTICES.md",
                   "CONTRIBUTING.md", "app/Madeira/legal/THIRD-PARTY-NOTICES.md"}
# The submodules' public homes (created at the first release; GPL: the source of every
# shipped pin must stay available, so BCD hosts it rather than relying on upstream).
# rpmalloc is FEX's own submodule: its pin and URL live in the FEX mirror; the source
# archive carries it either way.
MIRRORS = {"FEX": "https://github.com/thebcd69/bcd-fex.git",
           "wine": "https://github.com/thebcd69/bcd-wine.git",
           "dxmt": "https://github.com/thebcd69/bcd-dxmt.git",
           "madeira-dock": "https://github.com/thebcd69/bcd-dock.git"}
# Submodules the build never compiles (FEX's test binaries, range-v3's web pages): left out
# of the source archive, which carries every other submodule at its pin, nested ones too.
ARCHIVE_SKIP = ["FEX/External/fex-posixtest-bins", "FEX/External/fex-gvisor-tests-bins",
                "FEX/External/fex-gcc-target-tests-bins", "FEX/External/range-v3/doc/gh-pages"]
# Text edits in the published source, made at a contributor's request and noted in the file
# (LGPL-2.1 section 2(a)). Neither file is a build input: the binaries are the same.
def _contributor_handle(content, keys):
    """The owner's old handle in Wine's contribution record becomes the current one (owner,
    2026-10-18). Which handle is read from the private needles file, never written here."""
    text = content.decode("utf-8")
    for n in keys:
        text = re.sub(r"\w*" + re.escape(n.decode("utf-8")) + r"\w*", "thebcd69", text, flags=re.I)
    note = ("\n\nChanged by BCD on 2026-10-18: a contributor's GitHub handle above is shown as the "
            "contributor's current one (thebcd69), at the contributor's request.\n")
    return (text.rstrip("\n") + note).encode("utf-8")


PUBLIC_EDITS = {"wine/LICENSE-MADEIRA.md": _contributor_handle}
# Things that must never be public, whatever the needles file says.
PATTERNS = [r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"X-Amz-(Signature|Credential)=",
            r"[a-f0-9]{32}\.r2\.cloudflarestorage\.com", r"\bAKIA[0-9A-Z]{16}\b",
            r"ghp_[A-Za-z0-9]{36}", r"github_pat_[A-Za-z0-9_]{40,}"]


def git(repo, *args, env=None, input=None, text=True):
    r = subprocess.run(["git", "--git-dir", str(repo), *args], capture_output=True, text=text,
                       env={**os.environ, **(env or {})}, input=input)
    if r.returncode:
        sys.exit(f"export-public: git {' '.join(args[:3])} failed: {r.stderr.strip()[:400]}")
    return r.stdout.strip() if text else r.stdout


def needles():
    if not NEEDLES.exists():
        sys.exit(f"export-public: {NEEDLES} is missing; refusing to export without it")
    lines = [l.strip() for l in NEEDLES.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    if not lines:
        sys.exit("export-public: the needles file is empty")
    return [n.lower().encode() for n in lines]


def rewrite_gitmodules(text):
    out, current = [], None
    for line in text.splitlines():
        m = re.match(r'\[submodule "(.+)"\]', line.strip())
        if m:
            current = m.group(1)
        if line.strip().startswith("url = ") and current in MIRRORS:
            line = line[:line.index("url = ")] + "url = " + MIRRORS[current]
        out.append(line)
    return "\n".join(out) + "\n"


def local_repo(url, parent_repo):
    """The local mirror of a submodule URL (the same mapping as ~/madeira-local/yerel-git.sh)."""
    if url.startswith("../"):
        return (parent_repo.parent / url[3:]).resolve()
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(\.git)?/?$", url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if owner == "willfaust":
        return KAYNAK / f"{repo}.git"
    return KAYNAK / "ucuncu" / f"{owner}--{repo}.git"


def submodule_urls(repo, commit):
    r = subprocess.run(["git", "--git-dir", str(repo), "show", f"{commit}:.gitmodules"], capture_output=True, text=True)
    urls, current = {}, None
    for line in (r.stdout if r.returncode == 0 else "").splitlines():
        line = line.strip()
        if line.startswith("path = "):
            current = line[7:]
        elif line.startswith("url = ") and current:
            urls[current] = line[6:]
    # A [submodule] block can name its url before its path.
    if r.returncode == 0:
        blocks = re.split(r"\n(?=\[submodule )", r.stdout)
        for b in blocks:
            path = re.search(r"^\s*path = (.+)$", b, re.M)
            url = re.search(r"^\s*url = (.+)$", b, re.M)
            if path and url:
                urls[path.group(1).strip()] = url.group(1).strip()
    return urls


def write_source_archive(out, tree, version, keys):
    """Every file of the public tree plus every submodule at its pin (nested ones too), as one
    tar.xz under bcd-<version>/. GitHub's own archives leave submodules out; this one is the
    complete corresponding source of a release (legal audit F-2)."""
    prefix = f"bcd-{version}/"
    hits, missing, count = [], [], 0
    with tarfile.open(out, "w:xz", preset=6) as tar:
        def add(repo, treeish, base, gitmodules_commit):
            nonlocal count
            # Streamed: wine alone is hundreds of megabytes.
            proc = subprocess.Popen(["git", "--git-dir", str(repo), "archive", "--format=tar", treeish],
                                    stdout=subprocess.PIPE)
            with tarfile.open(fileobj=proc.stdout, mode="r|") as part:
                for member in part:
                    data = part.extractfile(member) if member.isfile() else None
                    content = data.read() if data else b""
                    if (base + member.name) in PUBLIC_EDITS:
                        content = PUBLIC_EDITS[base + member.name](content, keys)
                        member.size = len(content)
                    low = content.lower()
                    hits.extend((base + member.name, f"needle #{i}") for i, n in enumerate(keys, 1) if n in low)
                    member.name = prefix + base + member.name
                    tar.addfile(member, io.BytesIO(content) if data else None)
                    count += 1
            if proc.wait():
                sys.exit(f"export-public: git archive {treeish[:12]} in {repo.name} failed")
            urls = submodule_urls(repo, gitmodules_commit)
            listing = subprocess.run(["git", "--git-dir", str(repo), "ls-tree", "-r", treeish],
                                     capture_output=True, text=True).stdout.splitlines()
            for line in listing:
                meta, path = line.split("\t", 1)
                mode, kind, sha = meta.split()
                if kind != "commit" or (base + path) in ARCHIVE_SKIP:
                    continue
                sub_repo = local_repo(urls.get(path, ""), repo)
                ok = sub_repo and subprocess.run(["git", "--git-dir", str(sub_repo), "cat-file", "-e", sha + "^{commit}"],
                                                 capture_output=True).returncode == 0
                if not ok:
                    missing.append(f"{base}{path} @ {sha[:12]} ({urls.get(path, 'no url')})")
                    continue
                add(sub_repo, sha, base + path + "/", sha)
        add(SOURCE_REPO, tree, "", SOURCE_COMMIT)
    return count, hits, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True, help="the release, e.g. 0.1.570")
    ap.add_argument("--source", default="auto-tune", help="commit or branch in the private repository")
    ap.add_argument("--dry-run", action="store_true", help="build and scan, write nothing")
    ap.add_argument("--draft", action="store_true", help="allow an unadopted LICENSE-BCD.md (never publish)")
    ap.add_argument("--source-archive", type=Path, help="also write the release's complete source archive (.tar.xz)")
    a = ap.parse_args()

    global SOURCE_COMMIT
    source = SOURCE_COMMIT = git(SOURCE_REPO, "rev-parse", "--verify", a.source + "^{commit}")
    licence = git(SOURCE_REPO, "show", f"{source}:LICENSE-BCD.md")
    adopted = re.search(r"^Adopted on: (\S.*)$", licence, re.M)
    if not adopted or "_" in adopted.group(1):
        if not a.draft:
            sys.exit("export-public: LICENSE-BCD.md is not adopted yet (owner); refusing (--draft to try)")

    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(SOURCE_REPO, "read-tree", source, env=env)
        # `git rm` wants a work tree; --index-info with mode 0 removes entries in a bare one.
        everything = git(SOURCE_REPO, "ls-tree", "-r", "--name-only", "--full-tree", source).splitlines()
        def kept(p):
            return any(p == k or p.startswith(k + "/") for k in KEEP)
        drop = [p for p in everything if (any(p == x or p.startswith(x + "/") for x in PRIVATE) and not kept(p))
                or (p.lower().endswith(".md") and p not in MARKDOWN_PUBLIC)]
        git(SOURCE_REPO, "update-index", "--index-info", env=env,
            input="".join(f"0 {'0' * 40}\t{p}\n" for p in drop))
        readme = git(SOURCE_REPO, "ls-tree", source, "README-BCD.md").split()
        if not readme:
            sys.exit("export-public: README-BCD.md is missing in the source commit")
        git(SOURCE_REPO, "update-index", "--add", "--cacheinfo", f"{readme[0]},{readme[2]},README.md", env=env)
        modules = rewrite_gitmodules(git(SOURCE_REPO, "show", f"{source}:.gitmodules"))
        blob = git(SOURCE_REPO, "hash-object", "-w", "--stdin", input=modules)
        git(SOURCE_REPO, "update-index", "--cacheinfo", f"100644,{blob},.gitmodules", env=env)
        tree = git(SOURCE_REPO, "write-tree", env=env)

    # Scan every blob of the result.
    found, keys = [], needles()
    listing = git(SOURCE_REPO, "ls-tree", "-r", tree).splitlines()
    files = [(l.split("\t", 1)[1], l.split()[2]) for l in listing if l.split()[1] == "blob"]
    for path, sha in files:
        data = git(SOURCE_REPO, "cat-file", "blob", sha, text=False)
        low = data.lower()
        found += [(path, f"needle #{i}") for i, n in enumerate(keys, 1) if n in low]
        if b"\0" not in data[:8000]:
            textual = data.decode("utf-8", "replace")
            found += [(path, f"pattern #{i}") for i, p in enumerate(PATTERNS, 1) if re.search(p, textual)]
    if found:
        for path, what in found[:30]:
            print(f"HIT {what} in {path}")
        sys.exit(f"export-public: refused, {len(found)} hit(s); nothing written")
    private_left = [p for p, _ in files if any(p == x or p.startswith(x + "/") for x in PRIVATE)
                    and not any(p == k or p.startswith(k + "/") for k in KEEP) and p != "README.md"]
    private_left += [p for p, _ in files if p.lower().endswith(".md") and p not in MARKDOWN_PUBLIC]
    assert not private_left, private_left
    names = {p for p, _ in files}
    missing = [k for k in KEEP if k not in names and not any(n.startswith(k + "/") for n in names)]
    if missing:
        sys.exit(f"export-public: needed for a complete source but missing: {missing}")
    print(f"export-public: {len(files)} files, {len(keys)} needles and {len(PATTERNS)} patterns, no hit")
    if a.source_archive:
        count, hits, missing = write_source_archive(a.source_archive, tree, a.version, keys)
        if hits:
            a.source_archive.unlink(missing_ok=True)
            for path, what in hits[:30]:
                print(f"HIT {what} in {path}")
            sys.exit(f"export-public: source archive refused, {len(hits)} hit(s); nothing kept")
        if missing:
            a.source_archive.unlink(missing_ok=True)
            sys.exit("export-public: submodule pins missing from the local mirrors:\n  " + "\n  ".join(missing))
        size = a.source_archive.stat().st_size / 1e6
        print(f"export-public: source archive {a.source_archive} ({count} entries, {size:.0f} MB)")
    if a.dry_run:
        print(f"export-public: dry run, tree {tree[:12]} (nothing written)")
        return

    # Parents: the previous public release and the upstream commit the source last merged.
    if not PUBLIC_REPO.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(PUBLIC_REPO)], check=True)
    git(SOURCE_REPO, "fetch", "-q", str(UPSTREAM_REPO), "+refs/heads/main:refs/bcd-export/upstream-main")
    base = git(SOURCE_REPO, "merge-base", source, "refs/bcd-export/upstream-main")
    parents = []
    previous = subprocess.run(["git", "--git-dir", str(PUBLIC_REPO), "rev-parse", "-q", "--verify", "refs/heads/main"],
                              capture_output=True, text=True).stdout.strip()
    if previous:
        git(SOURCE_REPO, "fetch", "-q", str(PUBLIC_REPO), "+refs/heads/main:refs/bcd-export/public-main")
        parents.append(previous)
        if git(SOURCE_REPO, "merge-base", previous, base) != base:
            parents.append(base)          # upstream moved: record the merge
    else:
        parents.append(base)
    message = (f"BCD {a.version}{' (DRAFT, do not publish)' if a.draft else ''}\n\n"
               f"BCD, a fork of Madeira. Built from the private tree's {source[:12]}; upstream base {base[:12]}.\n")
    args = ["commit-tree", tree] + sum((["-p", p] for p in parents), []) + ["-m", message]
    commit = git(SOURCE_REPO, *args, env={"GIT_AUTHOR_NAME": "BCD", "GIT_AUTHOR_EMAIL": "bcd@users.noreply.github.com",
                                          "GIT_COMMITTER_NAME": "BCD", "GIT_COMMITTER_EMAIL": "bcd@users.noreply.github.com"})
    branch = "draft" if a.draft else "main"
    git(SOURCE_REPO, "push", "-q", str(PUBLIC_REPO), f"{commit}:refs/heads/{branch}")
    if not a.draft:
        git(SOURCE_REPO, "push", "-q", str(PUBLIC_REPO), f"{commit}:refs/tags/v{a.version}")
    print(f"export-public: {commit[:12]} -> {PUBLIC_REPO} {branch}" + ("" if a.draft else f", tag v{a.version}"))


if __name__ == "__main__":
    main()
