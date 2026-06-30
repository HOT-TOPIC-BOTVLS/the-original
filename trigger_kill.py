"""
trigger_kill.py — run this from YOUR machine (not the bot, not the watchdog)
to fire the kill switch.

Usage:
    python trigger_kill.py --reason "something went wrong"
    
Set these env vars or hardcode them LOCALLY ONLY (never commit):
    WATCHDOG_URL    = https://your-watchdog-service.onrender.com
    WATCHDOG_SECRET = your_watchdog_secret_here
"""
import os
import sys
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

WATCHDOG_URL    = os.environ.get("WATCHDOG_URL", "")
WATCHDOG_SECRET = os.environ.get("WATCHDOG_SECRET", "")


def fire(reason: str):
    if not WATCHDOG_URL or not WATCHDOG_SECRET:
        print("ERROR: WATCHDOG_URL and WATCHDOG_SECRET must be set.")
        sys.exit(1)

    print(f"Firing kill switch. Reason: {reason}")
    confirm = input("Type CONFIRM to proceed: ")
    if confirm.strip() != "CONFIRM":
        print("Aborted.")
        sys.exit(0)

    resp = requests.post(
        f"{WATCHDOG_URL.rstrip('/')}/kill",
        headers={"X-Kill-Secret": WATCHDOG_SECRET, "Content-Type": "application/json"},
        json={"reason": reason},
        timeout=15,
    )
    print(f"Response [{resp.status_code}]: {resp.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fire the kill switch.")
    parser.add_argument("--reason", default="manual trigger", help="Why you're killing it")
    args = parser.parse_args()
    fire(args.reason)
