"""
Last.fm one-time authentication helper.

Generates a session key for write operations (love/unlove tracks, scrobbling,
now-playing updates). Run this once and save the printed session key as
LASTFM_SESSION_KEY in your .env or environment.

Usage:
    uv run lastfm_auth.py

Requires LASTFM_API_KEY and LASTFM_API_SECRET to be set in the environment
(or in a .env file alongside this script).
"""

import hashlib
import os
import sys
import webbrowser

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


def sign(params: dict) -> str:
    pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in ("format", "callback"))
    return hashlib.md5((pairs + API_SECRET).encode()).hexdigest()


def main():
    if not API_KEY or not API_SECRET:
        print("Error: LASTFM_API_KEY and LASTFM_API_SECRET must be set in your environment.")
        sys.exit(1)

    # Step 1: get a token
    resp = requests.get(BASE, params={"method": "auth.getToken", "api_key": API_KEY, "format": "json"})
    token = resp.json().get("token")
    if not token:
        print(f"Failed to get token: {resp.text}")
        sys.exit(1)

    # Step 2: ask user to authorise in browser
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

    print(f"\nSuccess! Authenticated as: {username}")
    print(f"\nAdd this to your environment (or .env file):")
    print(f"\n  LASTFM_SESSION_KEY={session_key}")
    print(f"  LASTFM_USERNAME={username}")
    print()


if __name__ == "__main__":
    main()
