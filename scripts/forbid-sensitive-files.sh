#!/usr/bin/env bash
set -euo pipefail

status=0

reject() {
  echo "forbidden file: $1 ($2)" >&2
  status=1
}

for file in "$@"; do
  name="${file##*/}"
  lower="${name,,}"
  case "$lower" in
    *.m4a | *.mp3 | *.opus | *.ogg | *.aac | *.wav | *.flac)
      reject "$file" "audio"
      continue
      ;;
    *.bin | *.gguf | ggml-*)
      reject "$file" "model"
      continue
      ;;
    transcript*.txt | *.raw.txt | *.anon.txt | entities.json | mapping.json | alerts.json)
      reject "$file" "transcript or mapping"
      continue
      ;;
    .env | *.env)
      reject "$file" "secrets"
      continue
      ;;
  esac
  case "$file" in
    tests/* | data/*) ;;
    *.txt | *.TXT)
      reject "$file" "text file outside tests/ and data/"
      ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_* | */[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*)
      reject "$file" "named like a meeting recording"
      ;;
  esac
done

exit "$status"
