"""Create a dedicated Model Access Key for the app and switch the app onto it.

Order matters, and it is the whole point of this script: the new key is TESTED
against the inference endpoint *before* the running app is repointed. If the new
key does not authenticate, it is deleted and the app is left exactly as it was —
a failed rotation must never take down a working deployment.

The secret is only ever held in this process's memory. It is never printed, never
written to a file, and never passed as a command-line argument. Output is limited
to the key's name, uuid and a masked fingerprint.

    DIGITALOCEAN_API_TOKEN=... python scripts/rotate_app_key.py <app-id> [key-name]

Leave the old credential alone: on this account the app was sharing the same
`doo_...` inference credential Hermes uses. Rotating the APP's key must not revoke
Hermes's, so this script never deletes a key it did not create.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import configure_app  # noqa: E402  (reused so the spec-edit logic lives in one place)

API = "https://api.digitalocean.com/v2"
TOKEN = os.environ.get("DIGITALOCEAN_API_TOKEN", "").strip()
PROBE_URL = os.environ.get("DO_INFERENCE_URL", "https://inference.do-ai.run/v1/chat/completions")
PROBE_MODEL = os.environ.get("DO_INFERENCE_MODEL", "qwen3.8-max")


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{method} {url} -> {e.code}: {detail}") from None


def fingerprint(secret: str) -> str:
    """A non-reversible label for logs — enough to match, useless to steal."""
    return f"sha256:{hashlib.sha256(secret.encode()).hexdigest()[:12]} len={len(secret)}"


def probe(secret: str) -> tuple[bool, str]:
    """Cheapest possible real inference call, to prove the key authenticates."""
    req = urllib.request.Request(
        PROBE_URL,
        data=json.dumps({
            "model": PROBE_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {secret}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()
        return True, "authenticated"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def main(app_id: str, name: str) -> int:
    if not TOKEN:
        raise SystemExit("DIGITALOCEAN_API_TOKEN is not set")

    print(f"creating model access key {name!r} ...")
    try:
        created = call("POST", f"{API}/gen-ai/models/api_keys", {"name": name})
    except RuntimeError as exc:
        if "410" in str(exc) or "retired" in str(exc):
            print("  DigitalOcean has RETIRED programmatic creation of model access keys:")
            print(f"    {exc}")
            print("\nThere is no API/MCP path left — the key must be minted in the console.")
            print("Use scripts/set_app_key.py instead, which does everything after that.")
            return 2
        raise
    info = created.get("api_key_info") or created.get("model_api_key") or created
    uuid = info.get("uuid")
    secret = info.get("secret_key") or ""
    if not secret:
        print("  FAIL: no secret_key in the create response — nothing to rotate to")
        print("  response keys:", list(created.keys()))
        return 1
    print(f"  created: name={info.get('name')!r} uuid={uuid}")
    print(f"  secret:  {fingerprint(secret)}  (not shown, not stored)")

    print(f"testing it against {PROBE_URL} ...")
    ok, why = probe(secret)
    if not ok:
        print(f"  FAIL: the new key does not authenticate ({why})")
        print("  deleting it and leaving the app untouched")
        call("DELETE", f"{API}/gen-ai/models/api_keys/{uuid}")
        return 1
    print(f"  ok: {why}")

    print(f"repointing app {app_id} at the new key ...")
    configure_app.KEY = secret          # configure_app reads this module global
    out = configure_app.configure(app_id)
    print(f"  app updated; deployment {out.get('app', {}).get('pending_deployment', {}).get('id')}")

    print("\nverifying the key is now listed (i.e. it is revocable later) ...")
    for k in call("GET", f"{API}/gen-ai/models/api_keys").get("api_key_infos", []):
        print(f"  name={k.get('name')!r} uuid={k.get('uuid')} created={k.get('created_at')}")

    print("\nDone. The app has its own key.")
    print("The old doo_... credential was NOT touched — Hermes still uses it.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "hanzi-miner-app"))
