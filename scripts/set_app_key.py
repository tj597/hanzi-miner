"""Switch the deployed app onto a Model Access Key you supply.

DigitalOcean retired programmatic creation of model access keys (the create
endpoint returns HTTP 410: "Creating model API keys through this endpoint is
retired. Go to manage page in the control panel"). So the key has to be minted in
the browser. This script does everything after that.

STEP 1 - in the DigitalOcean console, create the key:

    https://cloud.digitalocean.com/model-studio/manage-keys

    Name:   hanzi-miner-app
    Models: select ONLY what the app calls (qwen3.8-max), not "All models".
            Least privilege: a leaked key then cannot reach your other models.
    VPC:    leave unrestricted for now. The app has no explicit VPC binding, so a
            VPC-scoped key may simply 403.
    Copy the key when it is shown; it is displayed exactly once.

STEP 2 - ONE command, no shell gymnastics:

    ~/dev/hanzi-miner/.venv/bin/python ~/dev/hanzi-miner/scripts/set_app_key.py

It PROMPTS for the key (hidden input, nothing echoed, nothing in your shell
history). Do not pass the key as an argument and do not `export` it first: both
record it where you don't want it.

WHY IT PROMPTS INSTEAD OF USING `read -rs` FROM THE SHELL: the first version of
this instruction was two lines - `read -rs KEY && export KEY`, then the command.
Pasting them together makes `read` swallow the SECOND line as the key, because it
reads from the very stdin the paste is being typed into. (It also silently set the
"key" to the text of the next command.) Prompting from inside the script removes
the failure mode for good.

For automation (CI, a scheduler) DO_INFERENCE_KEY is still honoured when it is
already set in the environment; the prompt only appears when it is absent.

Safety property: the key is tested against the inference endpoint BEFORE the app is
repointed, so a bad key cannot take down a running deployment.

Do NOT delete the old doo_... credential: Hermes uses the same one.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import configure_app  # noqa: E402  (one place owns the spec-edit logic)
from rotate_app_key import fingerprint, probe  # noqa: E402

DEFAULT_APP_ID = "956b5b55-c550-46aa-88f0-de4e8f35e26c"


def get_key() -> str:
    """Prefer the environment (automation); otherwise prompt without echoing."""
    key = os.environ.get("DO_INFERENCE_KEY", "").strip()
    if key:
        print("using DO_INFERENCE_KEY from the environment")
        return key

    import getpass

    print("Paste the model access key and press Enter (input is hidden):", flush=True)
    return getpass.getpass("key: ").strip()


def main(app_id: str) -> int:
    if not os.environ.get("DIGITALOCEAN_API_TOKEN", "").strip():
        print("DIGITALOCEAN_API_TOKEN is not set (needed to edit the app spec).")
        return 2

    key = get_key()
    if not key:
        print("no key supplied - nothing changed.")
        return 2

    print(f"\nnew key: {fingerprint(key)}")

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

    live = out.get("app", {}).get("live_url", "<live-url>")
    print("\nNext:")
    print("  1. wait ~2 minutes for the redeploy")
    print(f"  2. python3 scripts/verify_live.py {live}")
    print("  3. keep the old doo_... credential - Hermes still uses it")
    print("  4. revoke 'hanzi-miner-app' in the console whenever you like;")
    print("     doing so cannot affect Hermes")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_APP_ID))
