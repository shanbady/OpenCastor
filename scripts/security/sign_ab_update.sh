#!/usr/bin/env bash
set -euo pipefail

# Build a signed A/B update manifest for OpenCastor devices.
#
# Usage:
#   scripts/security/sign_ab_update.sh \
#     --slot-a rootfsA.img --slot-b rootfsB.img \
#     --kernel Image --initramfs initramfs.img \
#     --out dist/update-manifest.json --key keys/update_signing.pem

SLOT_A=""
SLOT_B=""
KERNEL=""
INITRAMFS=""
OUT=""
KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slot-a) SLOT_A="$2"; shift 2 ;;
    --slot-b) SLOT_B="$2"; shift 2 ;;
    --kernel) KERNEL="$2"; shift 2 ;;
    --initramfs) INITRAMFS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

for required in "$SLOT_A" "$SLOT_B" "$KERNEL" "$INITRAMFS" "$OUT" "$KEY"; do
  [[ -n "$required" ]] || { echo "Missing required arguments" >&2; exit 1; }
done

hash_file() {
  sha256sum "$1" | awk '{print $1}'
}

mkdir -p "$(dirname "$OUT")"

TMP_MANIFEST="$(mktemp)"
cat > "$TMP_MANIFEST" <<EOF
{
  "version": "$(date -u +%Y%m%d%H%M%S)",
  "artifacts": {
    "slot_a": {"path": "$SLOT_A", "sha256": "$(hash_file "$SLOT_A")"},
    "slot_b": {"path": "$SLOT_B", "sha256": "$(hash_file "$SLOT_B")"},
    "kernel": {"path": "$KERNEL", "sha256": "$(hash_file "$KERNEL")"},
    "initramfs": {"path": "$INITRAMFS", "sha256": "$(hash_file "$INITRAMFS")"}
  }
}
EOF

SIGNATURE="$(openssl dgst -sha256 -sign "$KEY" "$TMP_MANIFEST" | base64 -w 0)"

python - <<PY
import json
from pathlib import Path
manifest = json.loads(Path("$TMP_MANIFEST").read_text())
manifest["signature"] = {
    "alg": "rsa-sha256",
    "value_base64": "$SIGNATURE"
}
Path("$OUT").write_text(json.dumps(manifest, indent=2) + "\n")
PY

rm -f "$TMP_MANIFEST"
echo "Signed manifest written: $OUT"
