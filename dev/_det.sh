#!/bin/bash
# 确定性检查：连跑两次，report.json / mapping.json 的 sha256 必须一致。
# 用法: dev/_det.sh [game_dir] [jp_dir]
set -u

GAME="${1:-${LV_GAME:-}}"
JP="${2:-${LV_JP:-}}"

if [ -z "$GAME" ]; then
  echo "用法: dev/_det.sh <game_dir> [jp_dir]" >&2
  echo "  或设置 LV_GAME / LV_JP 环境变量" >&2
  exit 2
fi

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/out"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

run_once() {
  LV_GAME="$GAME" ${JP:+LV_JP="$JP"} \
    python -X utf8 "$HERE/linter.py" --out-dir "$OUT" --quiet
}

run_once > "$TMP/run1.txt" 2>&1
sha256sum "$OUT/report.json" "$OUT/mapping.json" > "$TMP/h1.txt"
run_once > "$TMP/run2.txt" 2>&1
sha256sum "$OUT/report.json" "$OUT/mapping.json" > "$TMP/h2.txt"

cat "$TMP/h1.txt"
echo "---"
cat "$TMP/h2.txt"

if diff -q "$TMP/h1.txt" "$TMP/h2.txt" > /dev/null; then
  echo "DETERMINISTIC=YES"
else
  echo "DETERMINISTIC=NO"
  exit 1
fi

tail -1 "$TMP/run1.txt"
tail -1 "$TMP/run2.txt"
