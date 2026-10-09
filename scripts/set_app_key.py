"""Switch the deployed app onto a Model Access Key you supply.

DigitalOcean retired programmatic creation of model access keys (the create
endpoint returns HTTP 410: "Creating model API keys through this endpoint is
retired. Go to manage page in the control panel"). So the key has to be minted in
the browser. This script does everything after that, and is designed so the secret
never enters a chat transcript, a file, or your shell history.

STEP 1 — in the DigitalOcean console, create the key:

    https://cloud.digitalocean.com/model-studio/manage-keys

    Name:  hanzi-miner-app
    Models: select ONLY what the app calls (qwen3.8-max) rather than "All models".
            Least privilege: if the key leaks, it can't reach your other models.
    VPC:   leave unrestricted for now. The app has no explicit VPC binding, so a
           VPC-scoped key may simply 403. Add that later — see the note below.
    Copy the key when it is shown; it is displayed exactly once.

STEP 2 — in your terminal, hand it to this script without recording it:

    read -rs DO_INFERENCE_KEY && export DO_INFERENCE_KEY
    python scripts/set_app_key.py <app-id>
    unset DO_INFERENCE_KEY

`read -rs` keeps the value out of shell history (a plain `export KEY=...` lands in
~/.zsh_history). The script prints only a sha256 fingerprint, and tests the key
against the inference endpoint BEFORE repointing the app, so a bad key cannot take
down the running deployment.

Do NOT delete the old doo_... credential: Hermes uses the same one.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import configure_app  # noqa: E402  (one place owns the spec-edit logic)
from rotate_app_key import fingerprint, probe  # noqa: E402

APP_ID = "e5d0d85c-5824-4b44-82d8-a541314b7989"


def main(app_id: str) -> int:
    key = os.environ.get("DO_INFERENCE_KEY", "").strip()
    if not key:
        print("DO_INFERENCE_KEY is not set.\n")
        print(__doc__)
        return 2
    if not os.environ.get("DIGITALOCEAN_API_TOKEN", "").strip():
        print("DIGITALOCEAN_API_TOKEN is not set (needed to edit the app spec).")
        return 2

    print(f"new key: {fingerprint(key)}")

    print("testing it against the inference endpoint (app untouched so far) ...")
    ok, why = probe(key)
    if not ok:
        print(f"  FAIL: {why}")
        print("\nThe app is unchanged and still running on the old credential.")
        return 1
    print(f"  ok: {why}")

    print("\nrepointing the app ...")
    configure_app.KEY = key
    out = configure_app.configure(app_id)
    dep = out.get("app", {}).get("pending_deployment", {})
    print(f"  app updated; deployment {dep.get('id')}")

    print("\nNext:")
    print("  1. unset DO_INFERENCE_KEY in your shell")
    print(f"  2. wait ~2 min, then: python scripts/verify_live.py <live-url>")
    print("  3. keep the old doo_... credential — Hermes still uses it")
    print("  4. revoke 'hanzi-miner-app' in the console whenever you like;")
    print("     doing so cannot affect Hermes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else APP_ID))
