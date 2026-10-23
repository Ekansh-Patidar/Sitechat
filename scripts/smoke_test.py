"""Check a deployed service is up, after a deploy.

    python -m scripts.smoke_test https://sitechat-api-uukevi3qpq-uc.a.run.app pk_pyramid_yogshala_001

Calls /health (the process runs) and /config with the site key (routing,
the tenant lookup in Firestore and the widget's settings all work). Sends
no chat message, so it makes no model calls and stores nothing. Exits
non-zero on failure, so it can run as a final step in cloudbuild.yaml.
"""

import json
import sys
import urllib.request


def check(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    # The scheme is checked in main(): only http(s), never file: or others.
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
        if response.status != 200:
            raise RuntimeError(f"{url} returned {response.status}")
        return json.loads(response.read() or b"{}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Usage: python -m scripts.smoke_test <base_url> <site_key>")
        return 2
    base, site_key = args[0].rstrip("/"), args[1]
    if not base.startswith(("https://", "http://")):
        print("The base URL must start with https:// (or http:// locally).")
        return 2

    try:
        check(f"{base}/health")
        print("ok    /health")
        config = check(f"{base}/config", {"X-Site-Key": site_key})
        if not config.get("display_name"):
            raise RuntimeError("/config returned no display_name")
        print(f"ok    /config ({config['display_name']})")
    except Exception as exc:
        print(f"FAIL  {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
