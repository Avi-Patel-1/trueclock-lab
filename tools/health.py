"""Record a small, public, nonphysical runner health receipt."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPO = "Avi-Patel-1/trueclock-lab"


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def capture():
    env = os.environ
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
    repository = event["repository"]
    if not (
        env["GITHUB_REPOSITORY"] == repository["full_name"] == REPO
        and repository["owner"]["id"] == 137302645
        and repository["private"] is False
        and env["GITHUB_EVENT_NAME"] == "workflow_dispatch"
        and env["GITHUB_REF"] == "refs/heads/main"
        and env["GITHUB_SHA"] == git("rev-parse", "HEAD")
        and env["RUNNER_ENVIRONMENT"] == "github-hosted"
        and env["RUNNER_OS"] == platform.system() == "Linux"
        and env["GITHUB_RUN_ATTEMPT"] == "1"
    ):
        raise RuntimeError("Health job received an unexpected repository, runner, ref or attempt")
    files = git("ls-files").splitlines()
    disk = shutil.disk_usage(".")
    mem = {key: int(value.split()[0]) * 1024 for key, value in
           (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
           if key in ("MemAvailable", "MemTotal")}
    result = {
        "kind": "runner_health", "repository": REPO, "commit": env["GITHUB_SHA"],
        "run_id": int(env["GITHUB_RUN_ID"]), "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "observed_utc": datetime.now(timezone.utc).isoformat(), "os": env["RUNNER_OS"],
        "os_image": env.get("ImageVersion"), "machine": platform.machine(),
        "runner_environment": env["RUNNER_ENVIRONMENT"], "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "disk_free_bytes": disk.free, "disk_total_bytes": disk.total,
        "memory_available_bytes": mem["MemAvailable"], "memory_total_bytes": mem["MemTotal"],
        "source_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files},
        "physical_jobs": 0,
    }
    output = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if len(output.encode()) > 4096:
        raise RuntimeError("Runner receipt exceeds the upload limit")
    path = Path("evidence/health.json")
    path.parent.mkdir(exist_ok=True)
    with path.open("x") as out:
        out.write(output)
    print(output, end="")


if __name__ == "__main__":
    capture()
