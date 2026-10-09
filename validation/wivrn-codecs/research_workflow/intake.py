#!/usr/bin/env python3
"""Commit-pinned branch intake; --fetch discovers remote heads and fetches objects only."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
REFERENCE = "feature/custom-codec"
DEFAULT_PREFIX = "research/sub15-20261009/"
SOURCES = {"remote", "cached_remote_refs", "cached_after_failed_fetch", "github_api"}


class GitError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git(repo, *args, timeout=60):
    # Never surface raw stderr: it may contain credentials from configured URLs.
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"})
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from None
    if result.returncode:
        raise GitError(f"git {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def safe_url(value):
    if "://" not in value:
        scp = re.fullmatch(r"[^@/]+@([^/:]+):(.+)", value)
        return ":".join(scp.groups()) if scp else value
    parts = urlsplit(value)
    host = parts.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if parts.port:
        host += ":" + str(parts.port)
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def identity(value):
    if "://" in value:
        parts = urlsplit(value)
        host, path = parts.hostname or "", parts.path
    else:
        match = re.fullmatch(r"(?:[^@/]+@)?([^/:]+):(.+)", value)
        if not match:
            return str(Path(value).resolve())
        host, path = match.groups()
    name = host.lower() + "/" + path.strip("/").removesuffix(".git")
    return name.lower() if host.lower() == "github.com" else name


def validate_snapshot(data):
    """Validate the fields consumed by comparison; reject ambiguous branch pins."""
    def require(condition):
        if not condition:
            raise ValueError("invalid snapshot field")
    try:
        require(type(data["schema_version"]) is int and data["schema_version"] == 1)
        repo, scope, collection = data["repository"], data["scope"], data["collection"]
        require(all(isinstance(repo[k], str) and repo[k] for k in ("remote", "remote_url")))
        require(repo["object_format"] in {"sha1", "sha256"})
        length = 40 if repo["object_format"] == "sha1" else 64
        is_sha = lambda value: isinstance(value, str) and bool(
            re.fullmatch("[0-9a-f]{" + str(length) + "}", value))
        require(isinstance(scope["prefix"], str) and bool(scope["prefix"]))
        require(scope["reference_branches"] == [REFERENCE])
        require(collection["source"] in SOURCES)
        require(type(collection["remote_discovery_complete"]) is bool)
        require(type(collection["graph_complete"]) is bool)
        require(isinstance(collection["errors"], list))
        require(all(isinstance(item, str) for item in collection["errors"]))
        for field in ("started_at_utc", "finished_at_utc"):
            require(isinstance(collection[field], str) and collection[field].endswith("Z"))
            datetime.fromisoformat(collection[field].replace("Z", "+00:00"))
        require(isinstance(data["branches"], list))
        names = set()
        for branch in data["branches"]:
            name = branch["name"]
            require(isinstance(name, str) and bool(name) and name not in names)
            require(name.startswith(scope["prefix"]) or name == REFERENCE)
            require(is_sha(branch["commit_sha"]))
            names.add(name)
        if "comparison_baseline" in data:
            baseline = data["comparison_baseline"]
            require(isinstance(baseline, dict))
            require(all(isinstance(name, str) and (name.startswith(scope["prefix"]) or name == REFERENCE)
                        and is_sha(sha) for name, sha in baseline.items()))
            if collection["remote_discovery_complete"]:
                require(baseline == {b["name"]: b["commit_sha"] for b in data["branches"]})
        require(isinstance(data["commits"], dict))
        for sha, commit in data["commits"].items():
            require(is_sha(sha) and isinstance(commit["parents"], list))
            require(all(is_sha(parent) for parent in commit["parents"]))
        if collection["source"].startswith("cached"):
            require(not collection["remote_discovery_complete"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("malformed or unsupported intake snapshot") from None
    return data

def classify(name):
    if "research-workflow" in name:
        return "workflow", False, False
    leaf = name.rsplit("/", 1)[-1]
    if name == REFERENCE or leaf in {"base", "integration"}:
        return "reference", False, False
    if "differential-fuzz" in leaf:
        return "validation", False, False
    families = {
        "compositor": ("compositor", "pacer", "fence", "submit"),
        "transport": ("datagram", "transport", "packet", "queue", "udp-"),
        "gpu": ("gpu", "vulkan", "arc"),
        "codec": ("rgb", "rpt", "lz4", "codec", "rowfilter", "neural"),
    }
    for family, tokens in families.items():
        if any(token in leaf.lower() for token in tokens):
            return family, False, True
    return "unknown", True, True


def parse_refs(text, ref_prefix, prefix, sha_length):
    found = {}
    for line in text.splitlines():
        sha, ref = line.split()
        if not ref.startswith(ref_prefix):
            continue
        name = ref[len(ref_prefix):]
        if name.startswith(prefix) or name == REFERENCE:
            if not re.fullmatch("[0-9a-f]{" + str(sha_length) + "}", sha):
                raise ValueError("Git returned an invalid commit pin")
            found[name] = sha
    return found


def prior_pins(previous):
    if not previous:
        return {}
    if "comparison_baseline" in previous:
        return previous["comparison_baseline"]
    return ({b["name"]: b["commit_sha"] for b in previous["branches"]}
            if previous["collection"]["remote_discovery_complete"] else {})


def delta(branches, previous, authoritative):
    before = prior_pins(previous)
    after = {b["name"]: b["commit_sha"] for b in branches}
    added = sorted(after.keys() - before.keys())
    moved = [{"name": name, "previous_sha": before[name], "commit_sha": after[name]}
             for name in sorted(before.keys() & after.keys()) if before[name] != after[name]]
    absent = sorted(before.keys() - after.keys())
    return {
        "new": added if authoritative else [],
        "moved": moved if authoritative else [],
        "removed": absent if authoritative else [],
        "unconfirmed_new": [] if authoritative else added,
        "unconfirmed_moved": [] if authoritative else moved,
        "unconfirmed_missing": [] if authoritative else absent,
    }

def collect(repo, remote="origin", prefix=DEFAULT_PREFIX, fetch=False, previous=None,
            timeout=60, max_graph_commits=5000):
    if timeout <= 0 or max_graph_commits <= 0 or not prefix:
        raise ValueError("timeout, graph bound and prefix must be positive/nonempty")
    started = utc_now()
    if remote.startswith("-") or remote not in git(repo, "remote", timeout=timeout).splitlines():
        raise ValueError("remote must name a configured Git remote")
    url = safe_url(git(repo, "remote", "get-url", remote, timeout=timeout))
    object_format = git(repo, "rev-parse", "--show-object-format", timeout=timeout)
    scope = {"prefix": prefix, "reference_branches": [REFERENCE]}
    repository = {"remote": remote, "remote_url": url, "object_format": object_format}
    if previous is not None:
        validate_snapshot(previous)
        if (identity(previous["repository"]["remote_url"]) != identity(url)
                or previous["scope"] != scope
                or previous["repository"]["object_format"] != object_format):
            raise ValueError("previous snapshot has a different repository or scope")
    length = 40 if object_format == "sha1" else 64
    errors, refs = [], {}
    complete = False
    source = "cached_remote_refs"
    if fetch:
        try:
            advertised = git(repo, "ls-remote", "--heads", remote, timeout=timeout)
            refs = parse_refs(advertised, "refs/heads/", prefix, length)
            if refs:
                # Exact advertised objects, no checkout, ref updates, pruning, or FETCH_HEAD.
                git(repo, "fetch", "--no-tags", "--no-write-fetch-head",
                    "--no-recurse-submodules", remote, *sorted(set(refs.values())), timeout=timeout)
            complete, source = True, "remote"
        except (GitError, ValueError) as error:
            errors.append(str(error))
            source = "cached_after_failed_fetch"
    if not complete:
        try:
            cached = git(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                         f"refs/remotes/{remote}/", timeout=timeout)
            refs = parse_refs(cached, f"refs/remotes/{remote}/", prefix, length)
        except (GitError, ValueError) as error:
            refs = {}
            errors.append(str(error))
    branches, aliases = [], defaultdict(list)
    for name, sha in sorted(refs.items()):
        family, review, candidate = classify(name)
        branches.append({"name": name, "commit_sha": sha, "family": family,
                         "classification": "advisory", "review_needed": review, "candidate": candidate})
        aliases[sha].append(name)
    commits, graph_complete, shallow = {}, True, False
    try:
        shallow = git(repo, "rev-parse", "--is-shallow-repository", timeout=timeout) == "true"
        lines = git(repo, "rev-list", "--parents", f"--max-count={max_graph_commits + 1}",
                    *sorted(aliases), "--", timeout=timeout).splitlines() if aliases else []
        for line in lines[:max_graph_commits]:
            sha, *parents = line.split()
            commits[sha] = {"parents": parents}
        graph_complete = (not shallow and len(lines) <= max_graph_commits
                          and all(sha in commits for sha in aliases)
                          and all(p in commits for c in commits.values() for p in c["parents"]))
        if not graph_complete:
            errors.append("ancestry graph incomplete: shallow repository or graph collection bound")
    except GitError as error:
        graph_complete = False
        errors.append(str(error))
    pairs = []
    for descendant in sorted(aliases):
        seen, pending = set(), list(commits.get(descendant, {}).get("parents", []))
        while pending:
            sha = pending.pop()
            if sha in seen:
                continue
            seen.add(sha)
            pending.extend(commits.get(sha, {}).get("parents", []))
        pairs.extend({"ancestor": sha, "descendant": descendant}
                     for sha in sorted(seen.intersection(aliases)))
    return {
        "schema_version": SCHEMA_VERSION, "repository": repository, "scope": scope,
        "collection": {"started_at_utc": started, "finished_at_utc": utc_now(),
                       "source": source, "remote_discovery_complete": complete,
                       "graph_complete": graph_complete, "shallow_repository": shallow,
                       "command_timeout_seconds": timeout, "max_graph_commits": max_graph_commits,
                       "errors": errors},
        "branches": branches, "commits": commits, "tip_aliases": dict(aliases),
        "ancestor_pairs": pairs, "changes": delta(branches, previous, complete),
        "comparison_baseline": dict(refs if complete else prior_pins(previous)),
    }


def write_snapshot(path, data):
    """Publish a complete JSON inode atomically; an existing receipt is never replaced."""
    validate_snapshot(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".intake-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.link(temporary, path)  # Exclusive creation, including against concurrent writers.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--repo", required=True)
    snapshot.add_argument("--remote", default="origin")
    snapshot.add_argument("--prefix", default=DEFAULT_PREFIX)
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--previous")
    snapshot.add_argument("--fetch", action="store_true", help="opt in to remote discovery/object fetch")
    snapshot.add_argument("--timeout", type=int, default=60)
    snapshot.add_argument("--max-graph-commits", type=int, default=5000)
    args = parser.parse_args(argv)
    try:
        if Path(args.output).exists():
            raise FileExistsError("output snapshot already exists")
        previous = json.loads(Path(args.previous).read_text()) if args.previous else None
        data = collect(args.repo, args.remote, args.prefix, args.fetch, previous,
                       args.timeout, args.max_graph_commits)
        write_snapshot(args.output, data)
        return 2 if data["collection"]["errors"] else 0
    except (OSError, ValueError, GitError) as error:
        parser.exit(2, f"intake: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
