#!/usr/bin/env bash
# Commit the given paths and push, rebasing on concurrent pushes (collector, sender, analyst).
set -euo pipefail
msg="$1"; shift
git config user.name "news-center-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$@"
if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
git commit -q -m "$msg"
for i in 1 2 3 4 5; do
  if git push -q origin HEAD:main; then echo "pushed"; exit 0; fi
  echo "push rejected, rebasing (attempt $i)"
  git pull -q --rebase -X theirs origin main || { git rebase --abort || true; git pull -q --rebase origin main; }
  sleep $((i * 2))
done
echo "push failed after retries"; exit 1
