#!/usr/bin/env python3
"""Keep both manifest snapshots reproducible, gated on downstream runtime drift."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def latest_ref(source):
    branch, separator, pin = source["ref"].partition("@")
    if not separator or not re.fullmatch(r"[0-9a-f]{40}", pin):
        raise ValueError("Expected a branch@<40-character SHA> manifest pin")
    remote_ref = f"refs/heads/{branch}"
    output = run("git", "ls-remote", "--exit-code", source["repository"], remote_ref)
    sha, resolved_ref = output.split()
    if resolved_ref != remote_ref or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"Could not resolve {remote_ref}")
    return f"{branch}@{sha}"


def tree_contents(root):
    if not root.is_dir():
        raise ValueError(f"Missing manifest snapshot: {root}")
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mode & 0o111)
        for path in root.rglob("*")
        if path.is_file()
    }


def load_sources(root):
    root = Path(root).resolve()
    sources_file = root / "hack/manifests-sources.yaml"
    yq = str(Path(os.environ.get("YQ", root / "bin/yq")).resolve())
    config = json.loads(run(yq, "-o=json", ".components.kuberay", str(sources_file)))
    # These paths are also the workflow's explicit allowlist of PR changes.
    for platform in ("odh", "rhoai"):
        if config[platform]["snapshot"] != f"opt/manifests/{platform}/kuberay":
            raise ValueError(f"Unexpected {platform} snapshot path")

    return sources_file, yq, config


def prepare(root, staging, platform, sources_file, yq):
    subprocess.run(
        ["bash", str(root / "hack/scripts/get-manifests.sh")],
        cwd=root,
        env={
            **os.environ,
            "ODH_PLATFORM_TYPE": platform,
            "USE_LOCAL": "false",
            "MANIFESTS_SOURCES_FILE": str(sources_file),
            "DST_MANIFESTS_DIR": str(staging / platform),
            "YQ": yq,
        },
        check=True,
    )
    return staging / platform / "kuberay"


def verify(root=ROOT):
    root = Path(root).resolve()
    sources_file, yq, config = load_sources(root)
    with tempfile.TemporaryDirectory(prefix="verify-kuberay-") as temporary:
        for platform in ("odh", "rhoai"):
            fetched = prepare(root, Path(temporary), platform, sources_file, yq)
            snapshot = root / config[platform]["snapshot"]
            if tree_contents(fetched) != tree_contents(snapshot):
                raise ValueError(f"{platform} snapshot differs from its pinned source commit")
            print(f"Verified {platform} snapshot matches {config[platform]['ref']}.")


def sync(root=ROOT):
    root = Path(root).resolve()
    sources_file, yq, config = load_sources(root)
    with tempfile.TemporaryDirectory(prefix="sync-kuberay-") as temporary:
        staging = Path(temporary)
        refs = {}

        def prepare_latest(platform):
            refs[platform] = latest_ref(config[platform])
            staged_sources = staging / f"{platform}-sources.yaml"
            updated = run(
                yq, f".components.kuberay.{platform}.ref = strenv(MANIFEST_REF)",
                str(sources_file), env={**os.environ, "MANIFEST_REF": refs[platform]},
            )
            staged_sources.write_text(updated + "\n")
            return prepare(root, staging, platform, staged_sources, yq)

        downstream = prepare_latest("rhoai")
        if tree_contents(downstream) == tree_contents(root / config["rhoai"]["snapshot"]):
            print("No downstream runtime manifest drift; leaving both snapshots and pins unchanged.")
            return False

        prepare_latest("odh")
        # Finish both downloads and validate the new provenance before replacing either tree.
        updated_sources = run(
            yq,
            ".components.kuberay.odh.ref = strenv(ODH_REF) | "
            ".components.kuberay.rhoai.ref = strenv(RHOAI_REF)",
            str(sources_file),
            env={**os.environ, "ODH_REF": refs["odh"], "RHOAI_REF": refs["rhoai"]},
        )
        for platform in ("odh", "rhoai"):
            snapshot = root / config[platform]["snapshot"]
            shutil.rmtree(snapshot)
            shutil.copytree(staging / platform / "kuberay", snapshot)
        sources_file.write_text(updated_sources + "\n")
        print(f"Updated ODH to {refs['odh']} and RHOAI to {refs['rhoai']}.")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Check snapshots against their pins without updating files")
    args = parser.parse_args()
    if args.verify:
        verify()
    else:
        sync()
