"""Guards on the deploy pipeline (backlog 83, 84): tests run before anything
is built, the service runs as its own account, and every secret it is given
is one the setup notes grant that account."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STEPS = yaml.safe_load((ROOT / "cloudbuild.yaml").read_text(encoding="utf-8"))["steps"]
SETUP = (ROOT / "notes" / "runtime-sa-setup.md").read_text(encoding="utf-8")


def deploy_args():
    (step,) = [s for s in STEPS if "deploy" in s.get("args", [])]
    return step["args"]


def test_tests_run_before_the_image_is_built():
    first = STEPS[0]
    assert "pytest" in " ".join(first["args"])
    assert "build" not in first["args"]


def test_the_service_runs_as_its_own_account():
    assert "--service-account=sitechat-runtime@$PROJECT_ID.iam.gserviceaccount.com" in deploy_args()


def test_every_secret_is_granted_in_the_setup_notes():
    (flag,) = [a for a in deploy_args() if a.startswith("--set-secrets=")]
    secrets = [pair.split("=")[1].split(":")[0] for pair in flag.removeprefix("--set-secrets=").split(",")]
    granted = set(re.findall(r"gcloud secrets add-iam-policy-binding (\S+)", SETUP))
    assert secrets and set(secrets) == granted
