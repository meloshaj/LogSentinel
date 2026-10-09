#!/usr/bin/env python3
"""Emit unsigned in-toto/SLSA-ready metadata from a release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_attestation(manifest: dict[str, Any]) -> dict[str, Any]:
    subjects: list[dict[str, Any]] = []
    artifact_digest = manifest.get("artifacts", {}).get("artifact_sha256")
    if artifact_digest:
        subjects.append({"name": "runtime-artifact", "digest": {"sha256": artifact_digest}})
    for name, image in sorted(manifest.get("images", {}).items()):
        identity = image.get("identity") if isinstance(image, dict) else None
        if isinstance(identity, str) and identity.startswith("sha256:"):
            subjects.append({"name": name, "digest": {"sha256": identity.removeprefix("sha256:")}})
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subjects,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://logsentinel.example/provenance/release-manifest/v1",
                "externalParameters": {
                    "sourceCommit": manifest.get("source", {}).get("commit"),
                    "runtimeManifestSha256": manifest.get("source", {}).get("runtime_manifest_sha256"),
                    "migrationHead": manifest.get("migration", {}).get("head"),
                    "gateStatus": manifest.get("gates", {}),
                    "sbom": manifest.get("sbom", {}),
                },
                "resolvedDependencies": [],
            },
            "runDetails": {
                "builder": {"id": manifest.get("build", {}).get("builder")},
                "metadata": {"buildStartedOn": manifest.get("build", {}).get("timestamp_utc")},
            },
        },
        "signature_status": "NOT_APPROVED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    attestation = build_attestation(manifest)
    Path(args.output).write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "signature_status": "NOT_APPROVED"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
