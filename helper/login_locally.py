"""Fallback: sign in to Garmin from your own computer and hand the sign-in to the helper.

Use it only if connecting from the app fails because Garmin refuses sign-ins
from the cloud. Needs Python 3.12+ and:  pip install "garminconnect==0.3.17" curl_cffi
Run:  python3 login_locally.py
Your password goes only to Garmin. The helper receives the resulting sign-in.
"""

from __future__ import annotations

import base64
import getpass
import json
import tempfile
import urllib.request


def main() -> None:
    from garminconnect import Garmin

    code = input("Setup code (from the deploy script): ").strip()
    pad = "=" * (-len(code) % 4)
    setup = json.loads(base64.urlsafe_b64decode(code + pad))
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    g = Garmin(email=email, password=password, prompt_mfa=lambda: input("Garmin two-step code: ").strip())
    with tempfile.TemporaryDirectory() as d:
        g.login(d)
        g.client.dump(d)
        with open(f"{d}/garmin_tokens.json", encoding="utf-8") as f:
            tokens = f.read()
    req = urllib.request.Request(
        setup["u"].rstrip("/") + "/api/garmin/tokens",
        data=json.dumps({"tokens": tokens}).encode(),
        headers={"Authorization": f"Bearer {setup['k']}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print("Helper says:", r.read().decode())


if __name__ == "__main__":
    main()
