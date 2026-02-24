#!/usr/bin/env bash
set -euo pipefail

OUT_PATH="${1:-/run/opencastor/attestation.json}"
PROFILE="${OPENCASTOR_SECURITY_PROFILE:-minimum-viable}"
TOKEN="${OPENCASTOR_ATTESTATION_TOKEN:-}"

secure_boot=false
measured_boot=false
signed_updates=false

if [[ -d /sys/firmware/efi/efivars ]]; then
  secure_boot=true
fi

if [[ -r /sys/kernel/security/tpm0/binary_bios_measurements || -r /sys/class/tpm/tpm0/device/pcrs ]]; then
  measured_boot=true
fi

if [[ -f /etc/opencastor/update-trust.pub ]]; then
  signed_updates=true
fi

verified=false
if [[ "$secure_boot" == true && "$measured_boot" == true && "$signed_updates" == true ]]; then
  verified=true
fi

mkdir -p "$(dirname "$OUT_PATH")"
python - <<PY
import json
from pathlib import Path
payload = {
    "profile": "$PROFILE",
    "secure_boot": "$secure_boot" == "true",
    "measured_boot": "$measured_boot" == "true",
    "signed_updates": "$signed_updates" == "true",
    "verified": "$verified" == "true",
}
if "$TOKEN":
    payload["token"] = "$TOKEN"
Path("$OUT_PATH").write_text(json.dumps(payload, indent=2) + "\n")
PY

echo "Attestation payload written: $OUT_PATH"
