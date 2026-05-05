#!/bin/bash
set -euo pipefail
charm=$( awk '/^name:/{ print $2 }' < metadata.yaml )
target_base="${CHARM_TARGET_BASE:-ubuntu-24.04}"
echo -n "pwd: "
pwd
ls -al

shopt -s nullglob
preferred=( "${charm}_${target_base}-"*.charm )
fallbacks=( "${charm}_"*.charm )
shopt -u nullglob

if (( ${#preferred[@]} > 0 )); then
  src="${preferred[0]}"
elif (( ${#fallbacks[@]} > 0 )); then
  src="${fallbacks[0]}"
else
  echo "No ${charm}_*.charm artifact found to rename." >&2
  exit 1
fi

echo "renaming ${src} to ${charm}.charm"
mv "${src}" "${charm}.charm"
