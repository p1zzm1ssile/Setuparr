#!/usr/bin/env bash

version=$(awk -F\" '/^VERSION/ {print $2}' ./src/app_info.py | head -n1)
base_tag='ghcr.io/p1zzm1ssile/setuparr'

echo "$GITHUB_TOKEN" | docker login ghcr.io -u p1zzm1ssile --password-stdin
# A released version's image is never replaced: bump VERSION in src/app_info.py first
if docker buildx imagetools inspect "$base_tag:$version" >/dev/null 2>&1; then
  echo "$base_tag:$version is already published. Bump VERSION in src/app_info.py to release a new one." >&2
  docker logout
  exit 1
fi
docker build -t "$base_tag:latest" -t "$base_tag:$version" .
docker push "$base_tag:latest"
docker push "$base_tag:$version"
docker logout
