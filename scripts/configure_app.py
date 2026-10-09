"""Wire the database binding and the inference key onto the LIVE App Platform app.

Why a script instead of editing .do/app.yaml: DO_INFERENCE_KEY is a SECRET. App
Platform encrypts the value on first submit and thereafter stores it in the live
spec as an opaque `EV[...]` blob. If you `doctl apps update --spec .do/app.yaml`
after that, the secret is dropped. So the correct base for any change is the live
spec — which is what this script fetches, patches and PUTs back.

The key is read from the environment and never printed. Nothing secret lands in
the transcript or in git.

    DIGITALOCEAN_API_TOKEN=... \\
    DO_INFERENCE_KEY="$HERMES_CUSTOM_INFERENCE_DO_AI_RUN_API_KEY" \\
    python scripts/configure_app.py <app-id>
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

API = "https://api.digitalocean.com/v2/apps"
TOKEN = os.environ.get("DIGITALOCEAN_API_TOKEN", "").strip()
KEY = os.environ.get("DO_INFERENCE_KEY", "").strip()
RUN_COMMAND = ("gunicorn app:app --bind 0.0.0.0:8080 --workers 1 --threads 4 "
               "--timeout 180")


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def configure(app_id: str) -> dict:
    if not TOKEN:
        raise SystemExit("DIGITALOCEAN_API_TOKEN is not set")
    if not KEY:
        print("!! DO_INFERENCE_KEY is not set — generation will stay disabled")

    app = call("GET", f"{API}/{app_id}")["app"]
    spec = app["spec"]
    svc = spec["services"][0]

    # 1. dev database
    if not any(d.get("name") == "db" for d in spec.get("databases", [])):
        spec.setdefault("databases", []).append(
            {"name": "db", "engine": "PG", "production": False})
        print("+ database: db (PG, development)")

    # 2. env vars, preserving any encrypted EV[...] secrets already present
    envs = {e["key"]: e for e in svc.get("envs", [])}
    if "DATABASE_URL" not in envs:
        print("+ env: DATABASE_URL = ${db.DATABASE_URL}")
    envs["DATABASE_URL"] = {"key": "DATABASE_URL", "value": "${db.DATABASE_URL}",
                            "scope": "RUN_TIME"}
    if KEY:
        # A plaintext value is only accepted on FIRST submit; afterwards DO
        # expects the EV[...] blob it returned, so pass that back if we have it.
        existing = envs.get("DO_INFERENCE_KEY", {})
        value = existing.get("value", "")
        if value.startswith("EV[") or value.startswith("EV1["):
            print("= env: DO_INFERENCE_KEY left as the existing encrypted value")
        else:
            envs["DO_INFERENCE_KEY"] = {"key": "DO_INFERENCE_KEY", "value": KEY,
                                        "scope": "RUN_TIME", "type": "SECRET"}
            print("+ env: DO_INFERENCE_KEY (SECRET, value supplied from the environment)")
    elif "DO_INFERENCE_KEY" in envs:
        print("= env: DO_INFERENCE_KEY preserved")
    svc["envs"] = list(envs.values())

    # 3. keep the gunicorn timeout ahead of the 150s generation request
    if svc.get("run_command") != RUN_COMMAND:
        svc["run_command"] = RUN_COMMAND
        print("+ run_command: gunicorn --timeout 180")

    out = call("PUT", f"{API}/{app_id}", {"spec": spec})

    # 4. save the live spec locally as the base for future edits, secrets masked
    safe = json.loads(json.dumps(out))
    for s in safe["app"]["spec"].get("services", []):
        for e in s.get("envs", []):
            if e.get("type") == "SECRET" and e.get("value", "").startswith("EV"):
                e["value"] = "<encrypted — use doctl apps spec get to see the real spec>"
    os.makedirs(".do", exist_ok=True)
    with open(".do/app.live.yaml", "w", encoding="utf-8") as fh:
        fh.write("# Live spec snapshot, secrets masked. Base for future edits.\n")
        fh.write(json.dumps(safe["app"]["spec"], indent=2, ensure_ascii=False))
    print("wrote .do/app.live.yaml (secrets masked)")

    dep = out.get("app", {}).get("pending_deployment") or {}
    print(f"\ndeployment: {dep.get('id', '(none queued)')}")
    print(f"live_url:   {out.get('app', {}).get('live_url', '?')}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    configure(sys.argv[1])
