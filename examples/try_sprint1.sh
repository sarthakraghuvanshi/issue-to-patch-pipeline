#!/usr/bin/env bash
# Hands-on Sprint 1 demo: builds a tiny buggy repo, then fixes it with the pipeline.
# Run from the project root:  bash examples/try_sprint1.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> 1. create a buggy repo at $WORK/repo"
mkdir -p "$WORK/repo"
cd "$WORK/repo"
cat > calculator.py <<'EOF'
def add(a, b):
    return a - b  # BUG: should be a + b
EOF
git init -q -b main
git -c user.name=demo -c user.email=demo@example.com add -A
GIT_AUTHOR_DATE=2020-01-01T00:00:00Z GIT_COMMITTER_DATE=2020-01-01T00:00:00Z \
  git -c user.name=demo -c user.email=demo@example.com commit -q -m "initial commit (with bug)"
cd - >/dev/null

echo "==> 2. run the pipeline (no LLM, no network)"
export ITP_ARTIFACTS_DIR="$WORK/artifacts"
export ITP_DATABASE_URL="sqlite+pysqlite:///$WORK/runs.db"
export ITP_LOG_JSON=false

set +e
uv run issue-to-patch run \
  --issue "$HERE/issue.json" \
  --repo "$WORK/repo" \
  --edit-plan "$HERE/plan.json" \
  --scope 'calculator.py'
echo "==> exit code: $?  (0=validated 10=needs-human 20=rejected 30=inconclusive)"
set -e

echo
echo "==> 3. the generated patch:"
cat "$WORK"/artifacts/*/fix.patch

echo
echo "==> 4. artifacts written:"
ls -1 "$WORK"/artifacts/*/
