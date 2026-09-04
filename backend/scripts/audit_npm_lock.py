#!/usr/bin/env python3
"""Fail a release when an npm lockfile contains a known OSV advisory."""

import argparse
import json
import sys
import urllib.request


OSV_QUERY_BATCH = "https://api.osv.dev/v1/querybatch"
BATCH_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 30


def coordinates_from_lock(lock):
    """Return unique, stable (name, version) coordinates from package-lock v2+.

    Nested package paths may contain ``node_modules`` more than once; the last
    segment is always the installed package name, including scoped names.
    """
    coordinates = set()
    for path, metadata in (lock.get("packages") or {}).items():
        version = metadata.get("version") if isinstance(metadata, dict) else None
        if not path.startswith("node_modules/") or not version:
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        coordinates.add((name, str(version)))
    return sorted(coordinates)


def query_osv(coordinates, opener=urllib.request.urlopen):
    query = {
        "queries": [
            {"package": {"ecosystem": "npm", "name": name},
             "version": version}
            for name, version in coordinates
        ]
    }
    request = urllib.request.Request(
        OSV_QUERY_BATCH,
        data=json.dumps(query).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(coordinates):
        raise RuntimeError("OSV returned an incomplete result set")
    return results


def audit_lock(lock, query=query_osv):
    coordinates = coordinates_from_lock(lock)
    if not coordinates:
        raise RuntimeError("lockfile contains no resolved npm packages")
    findings = []
    for start in range(0, len(coordinates), BATCH_SIZE):
        batch = coordinates[start:start + BATCH_SIZE]
        results = query(batch)
        for coordinate, result in zip(batch, results):
            advisories = sorted({
                item.get("id")
                for item in (result.get("vulns") or [])
                if item.get("id") and not item.get("withdrawn")
            })
            if advisories:
                findings.append((coordinate[0], coordinate[1], advisories))
    return coordinates, findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lockfile", help="package-lock.json to audit")
    args = parser.parse_args(argv)
    try:
        with open(args.lockfile, encoding="utf-8") as handle:
            lock = json.load(handle)
        coordinates, findings = audit_lock(lock)
    except Exception as error:
        print(f"Dependency audit could not complete: {error}", file=sys.stderr)
        return 1
    if findings:
        print(f"Dependency audit failed: {len(findings)} vulnerable package(s).",
              file=sys.stderr)
        for name, version, advisories in findings:
            print(f"{name}@{version}: {', '.join(advisories)}", file=sys.stderr)
        return 1
    print(f"Dependency audit passed: {len(coordinates)} npm package versions "
          "checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
