"""Exercise manifest syncing against local Git repositories; no network required."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
YQ = str(Path(os.environ.get("YQ", REPO / "bin/yq")).resolve())


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class SyncManifestsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="test-manifest-sync-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "module"
        scripts = self.root / "hack/scripts"
        scripts.mkdir(parents=True)
        for name in ("get-manifests.sh", "sync-manifests.py"):
            shutil.copy2(REPO / "hack/scripts" / name, scripts / name)

        self.remotes = {}
        self.config = {"sourcePath": "ray-operator/config"}
        for platform, branch in (("odh", "dev"), ("rhoai", "main")):
            remote = Path(self.temporary.name) / platform
            subprocess.run(
                ["git", "init", "-q", f"--initial-branch={branch}", str(remote)], check=True
            )
            git(remote, "config", "user.name", "Sync Test")
            git(remote, "config", "user.email", "sync-test@example.invalid")
            git(remote, "config", "commit.gpgsign", "false")
            git(remote, "config", "core.hooksPath", "/dev/null")
            self.remotes[platform] = remote
            self.change(platform, {"ray-operator/config/manager.yaml": f"{platform}: old\n"})
            snapshot = self.root / f"opt/manifests/{platform}/kuberay"
            snapshot.mkdir(parents=True)
            (snapshot / "manager.yaml").write_text(f"{platform}: old\n")
            self.config[platform] = {
                "repository": str(remote),
                "ref": f"{branch}@{git(remote, 'rev-parse', 'HEAD')}",
                "snapshot": f"opt/manifests/{platform}/kuberay",
            }
        self.sources = self.root / "hack/manifests-sources.yaml"
        self.write_config()

    def write_config(self):
        self.sources.write_text(json.dumps({"components": {"kuberay": self.config}}))

    def change(self, platform, changes):
        remote = self.remotes[platform]
        for name, content in changes.items():
            path = remote / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        git(remote, "add", "--all")
        git(remote, "commit", "-qm", "Update fixture")

    def run_sync(self, success=True, verify=False):
        result = subprocess.run(
            ["python3", str(self.root / "hack/scripts/sync-manifests.py"), *(["--verify"] if verify else [])],
            cwd=self.root,
            env={**os.environ, "YQ": YQ},
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def refresh(self, platform, local=False):
        destination = Path(self.temporary.name) / f"refreshed-{platform}"
        result = subprocess.run(
            ["bash", str(self.root / "hack/scripts/get-manifests.sh")],
            cwd=self.root,
            env={
                **os.environ,
                "ODH_PLATFORM_TYPE": platform,
                "USE_LOCAL": "true" if local else "false",
                "YQ": "/nonexistent/yq" if local else YQ,
                "DST_MANIFESTS_DIR": str(destination),
            },
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return contents(destination / "kuberay")

    def test_midstream_change_waits_for_downstream_runtime_drift(self):
        self.change("odh", {"ray-operator/config/manager.yaml": "odh: new\n"})
        # The downstream SHA advances, but none of these files are runtime manifests.
        self.change("rhoai", {
            "operator.go": "package main\n",
            "ray-operator/config/README.md": "new docs\n",
            "ray-operator/config/samples/sample.yaml": "sample: new\n",
        })
        before = contents(self.root)
        self.run_sync()
        self.assertEqual(contents(self.root), before)

    def test_downstream_change_updates_both_sources_and_is_idempotent(self):
        for platform in ("odh", "rhoai"):
            self.change(platform, {
                "ray-operator/config/manager.yaml": None,
                "ray-operator/config/added.yaml": f"source: {platform}\n",
                "ray-operator/config/README.md": "excluded\n",
                "ray-operator/config/samples/sample.yaml": "excluded\n",
            })
        original_getter = (self.root / "hack/scripts/get-manifests.sh").read_bytes()
        self.run_sync()
        updated = json.loads(subprocess.check_output(
            [YQ, "-o=json", ".components.kuberay", str(self.sources)], text=True
        ))
        for platform in ("odh", "rhoai"):
            self.assertEqual(
                contents(self.root / self.config[platform]["snapshot"]),
                {"added.yaml": f"source: {platform}\n".encode()},
            )
            branch = self.config[platform]["ref"].split("@")[0]
            self.assertEqual(
                updated[platform]["ref"],
                f"{branch}@{git(self.remotes[platform], 'rev-parse', 'HEAD')}",
            )
        self.assertEqual((self.root / "hack/scripts/get-manifests.sh").read_bytes(), original_getter)
        after = contents(self.root)
        self.run_sync()
        self.assertEqual(contents(self.root), after)

    def test_second_source_failure_leaves_both_snapshots_and_pins_unchanged(self):
        self.change("rhoai", {"ray-operator/config/manager.yaml": "rhoai: new\n"})
        self.config["odh"]["ref"] = "missing@" + "0" * 40
        self.write_config()
        before = contents(self.root)
        self.run_sync(success=False)
        self.assertEqual(contents(self.root), before)

    def test_manual_refresh_reproduces_new_pins_after_both_remotes_advance_again(self):
        for platform in ("odh", "rhoai"):
            self.change(platform, {"ray-operator/config/manager.yaml": f"{platform}: reviewed\n"})
        self.run_sync()
        for platform in ("odh", "rhoai"):
            self.change(platform, {"ray-operator/config/manager.yaml": f"{platform}: future\n"})
            self.assertEqual(self.refresh(platform), {"manager.yaml": f"{platform}: reviewed\n".encode()})
        self.run_sync(verify=True)

    def test_local_materialization_requires_neither_network_nor_yq(self):
        for platform in ("odh", "rhoai"):
            self.config[platform]["repository"] = "/nonexistent/repository"
        self.write_config()
        for platform in ("odh", "rhoai"):
            self.assertEqual(self.refresh(platform, local=True), {"manager.yaml": f"{platform}: old\n".encode()})

    def test_verification_rejects_snapshot_that_does_not_match_its_pin(self):
        self.run_sync(verify=True)
        (self.root / "opt/manifests/odh/kuberay/manager.yaml").write_text("unreviewed: change\n")
        before = contents(self.root)
        result = self.run_sync(verify=True, success=False)
        self.assertIn("odh snapshot differs from its pinned source commit", result.stderr)
        self.assertEqual(contents(self.root), before)


if __name__ == "__main__":
    unittest.main()
