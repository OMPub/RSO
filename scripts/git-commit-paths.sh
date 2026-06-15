#!/usr/bin/env bash
#
# Idempotently stage, commit, and push specific paths to a branch.
#
# Safe to re-run by design:
#   - clean no-op when the given paths have no changes (exit 0, no commit),
#   - never crashes on an unborn or unparseable HEAD (uses `git status
#     --porcelain` on the paths, not `git diff --cached` which needs HEAD),
#   - tolerates paths that do not exist (e.g. an index dir a skipped step
#     never produced),
#   - one fetch + rebase + retry on a rejected push, to ride over a
#     concurrent advance of the branch instead of failing the whole job.
#
# Usage: git-commit-paths.sh <branch> <message> <path> [<path> ...]
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: git-commit-paths.sh <branch> <message> <path> [<path> ...]" >&2
  exit 2
fi

branch="$1"
message="$2"
shift 2

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Stage the requested paths. `|| true` so an absent path is not fatal; `--all`
# so deletions within tracked paths are staged too.
git add --all -- "$@" 2>/dev/null || true

# `git status --porcelain` reports staged changes even on an unborn branch and
# never parses HEAD, so it cannot throw "could not parse HEAD". Scoping it to
# the paths avoids false positives from unrelated untracked files.
if [ -z "$(git status --porcelain -- "$@")" ]; then
  echo "git-commit-paths: nothing to commit for: ${message}"
  exit 0
fi

git commit -m "${message}"

if git push origin "HEAD:${branch}"; then
  exit 0
fi

echo "git-commit-paths: push rejected; rebasing on origin/${branch} and retrying" >&2
git fetch origin "${branch}"
git rebase "origin/${branch}"
git push origin "HEAD:${branch}"
