"""
Last.fm one-time authentication helper.

Generates a session key for write operations (love/unlove tracks, scrobbling,
now-playing updates). Writes LASTFM_SESSION_KEY and LASTFM_USERNAME directly
into .env, creating it from .env.example if it doesn't exist yet.

Usage:
    uv run lastfm_auth.py

Requires LASTFM_API_KEY and LASTFM_API_SECRET to already be in .env
(or in the environment).
"""

import hashlib
import os
import re
import sys
import webbrowser
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests not installed — run: uv pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.environ.get("LASTFM_API_KEY", "")
API_SECRET = os.environ.get("LASTFM_API_SECRET", "")
BASE = "https://ws.audioscrobbler.com/2.0/"
HERE = Path(__file__).parent


def sign(params: dict) -> str:
    pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in ("format", "callback"))
    return hashlib.md5((pairs + API_SECRET).encode()).hexdigest()


def set_env_vars(updates: dict[str, str]) -> None:
    """Write key=value pairs into .env, updating in-place or appending."""
    env_path = HERE / ".env"

    if not env_path.exists():
        example = HERE / ".env.example"
        if example.exists():
            env_path.write_text(example.read_text())
            print(f"Created .env from .env.example")
        else:
            env_path.write_text("")

    lines = env_path.read_text().splitlines(keepends=True)
    remaining = set(updates.keys())

    for i, line in enumerate(lines):
        for key in list(remaining):
            if re.match(rf"^{re.escape(key)}\s*=", line):
                lines[i] = f"{key}={updates[key]}\n"
                remaining.discard(key)
                break

    # Append any keys that weren't already in the file
    if remaining:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        for key in sorted(remaining):
            lines.append(f"{key}={updates[key]}\n")

    env_path.write_text("".join(lines))


def main():
    if not API_KEY or not API_SECRET:
        print("Error: LASTFM_API_KEY and LASTFM_API_SECRET must be set in .env or your environment.")
        sys.exit(1)

    # Step 1: get a token
    resp = requests.get(BASE, params={"method": "auth.getToken", "api_key": API_KEY, "format": "json"})
    token = resp.json().get("token")
    if not token:
        print(f"Failed to get token: {resp.text}")
        sys.exit(1)

    # Step 2: open browser for user to approve
    auth_url = f"https://www.last.fm/api/auth/?api_key={API_KEY}&token={token}"
    print(f"\nOpening Last.fm authorisation page in your browser...")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    input("After approving access in the browser, press Enter to continue...")

    # Step 3: exchange token for session key
    params = {"method": "auth.getSession", "api_key": API_KEY, "token": token}
    params["api_sig"] = sign(params)
    params["format"] = "json"
    resp = requests.get(BASE, params=params)
    data = resp.json()

    if "error" in data:
        print(f"Error getting session: {data['error']} — {data.get('message', '')}")
        print("Make sure you approved access before pressing Enter.")
        sys.exit(1)

    session_key = data["session"]["key"]
    username = data["session"]["name"]

    set_env_vars({"LASTFM_SESSION_KEY": session_key, "LASTFM_USERNAME": username})

    print(f"\nAuthenticated as: {username}")
    print(f".env updated with LASTFM_SESSION_KEY and LASTFM_USERNAME.")


if __name__ == "__main__":
    main()
