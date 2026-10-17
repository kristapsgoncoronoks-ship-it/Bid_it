#!/usr/bin/env bash
# Final images, including installed OCR/native/runtime packages; no blanket ignores.
set -euo pipefail
scanner='aquasec/trivy@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222'
if [ "$#" -eq 0 ]; then
  echo 'No images supplied; refusing an empty image audit.' >&2
  exit 2
fi
extra=()
if [ -n "${TRIVY_CA_BUNDLE:-}" ]; then
  extra+=(-v "$TRIVY_CA_BUNDLE:/etc/ssl/certs/ca-certificates.crt:ro")
fi
if [ -n "${TRIVY_CACHE_DIR:-}" ]; then
  extra+=(-v "$TRIVY_CACHE_DIR:/root/.cache/trivy")
fi
mkdir -p image-security
failed=0
for image in "$@"; do
  name=${image//[^a-zA-Z0-9_.-]/_}
  if ! docker run --rm "${extra[@]}" -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/image-security:/reports" \
    "$scanner" image --image-src docker --scanners vuln --format cyclonedx \
    --output "/reports/${name}.cdx.json" "$image"; then
    failed=1
  fi
  if ! docker run --rm "${extra[@]}" -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/image-security:/reports" \
    "$scanner" image --image-src docker --scanners vuln --severity HIGH,CRITICAL --exit-code 1 \
    --format json --output "/reports/${name}.audit.json" "$image"; then
    failed=1
  fi
done
exit "$failed"
