#!/usr/bin/env python3
"""Generate a CycloneDX SBOM. Uses cyclonedx-bom if available, else writes minimal SBOM."""
import json, datetime, subprocess, sys, os

tag = os.environ.get("RELEASE_TAG", "unknown")
outfile = f"opencastor-{tag}-sbom.cyclonedx.json"

success = False
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "cyclonedx-bom>=4,<5", "-q"], check=True)
    result = subprocess.run(
        [sys.executable, "-m", "cyclonedx_bom", "-e", "-o", outfile],
        capture_output=True, timeout=60
    )
    if result.returncode == 0:
        print(f"SBOM generated via cyclonedx-bom: {outfile}")
        success = True
    else:
        print(f"cyclonedx-bom failed: {result.stderr.decode()}", file=sys.stderr)
except Exception as e:
    print(f"cyclonedx-bom unavailable ({e}), writing minimal SBOM", file=sys.stderr)

if not success:
    sbom = {
        "bomFormat": "CycloneDX", "specVersion": "1.4", "version": 1,
        "metadata": {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "component": {"type": "library", "name": "opencastor", "version": tag}
        },
        "components": []
    }
    with open(outfile, "w") as f:
        json.dump(sbom, f, indent=2)
    print(f"Minimal SBOM written: {outfile}")

d = json.load(open(outfile))
print(f"Components: {len(d.get('components', []))}")
print(f"SBOM_FILE={outfile}")
