#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_MODEL="ggml-large-v3-turbo-q5_0.bin"
readonly REPOSITORY_URL="${WHISPER_MODEL_BASE_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/main}"

declare -A PINNED_SHA256=(
  [ggml-large-v3-turbo-q5_0.bin]=394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2
  [ggml-tiny.bin]=be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21
)

usage() {
  cat >&2 <<USAGE
usage: ${0##*/} [-m MODEL] [-s SHA256] TARGET_DIR

Downloads a whisper.cpp ggml model from ${REPOSITORY_URL}
into TARGET_DIR and verifies its SHA-256 checksum.

  -m MODEL   model file name (default: ${DEFAULT_MODEL})
  -s SHA256  expected checksum; defaults to the pinned value for known models,
             otherwise to the checksum published by the repository
USAGE
  exit 2
}

fail() {
  echo "${0##*/}: $*" >&2
  exit 1
}

published_sha256() {
  curl --fail --silent --show-error --head "${REPOSITORY_URL}/$1" \
    | tr -d '\r' \
    | awk 'tolower($1) == "x-linked-etag:" { gsub(/"/, "", $2); print $2 }'
}

matches() {
  [[ "$(sha256sum "$1" | cut -d ' ' -f 1)" == "$2" ]]
}

model="${DEFAULT_MODEL}"
expected=""
while getopts ":m:s:h" option; do
  case "${option}" in
    m) model="${OPTARG}" ;;
    s) expected="${OPTARG,,}" ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -eq 1 ]] || usage
target_dir="$1"

[[ "${model}" =~ ^ggml-[A-Za-z0-9._-]+\.bin$ ]] || fail "unexpected model name: ${model}"
command -v curl > /dev/null || fail "curl is required"
command -v sha256sum > /dev/null || fail "sha256sum is required"

if [[ -z "${expected}" ]]; then
  expected="${PINNED_SHA256[${model}]:-}"
fi
if [[ -z "${expected}" ]]; then
  expected="$(published_sha256 "${model}")"
fi
[[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || fail "no valid SHA-256 checksum available for ${model}"

mkdir -p "${target_dir}"
destination="${target_dir}/${model}"

if [[ -f "${destination}" ]] && matches "${destination}" "${expected}"; then
  echo "${destination} is already present and verified"
  exit 0
fi

partial="${destination}.partial"
trap 'rm -f "${partial}"' EXIT
curl --fail --location --show-error --progress-bar --output "${partial}" "${REPOSITORY_URL}/${model}"
matches "${partial}" "${expected}" || fail "checksum mismatch for ${model}; download discarded"
mv "${partial}" "${destination}"
echo "${destination} downloaded and verified (sha256 ${expected})"
