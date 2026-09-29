#!/usr/bin/env bash
# Build the three images and push them to SWR.
# First log in once:  docker login -u <region>@<AK> -p <login-key> swr.<region>.<domain>
#   (copy the exact command from SWR console -> "Generate Login Command")
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f deploy.env ] || { echo "deploy.env not found (cp deploy.env.example deploy.env)" >&2; exit 1; }
# shellcheck disable=SC1091
source deploy.env

for svc in backend-api order-service frontend; do
  image="${REGISTRY}/pardis-shop-${svc}:${TAG}"
  echo ">>> building ${image}"
  docker build --pull -t "${image}" "./${svc}"
  docker push "${image}"
done
echo "All images pushed with tag ${TAG}."
