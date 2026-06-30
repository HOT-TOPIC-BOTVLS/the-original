"""
watchdog.py — The kill switch. 

This is a completely separate service from the bot.
It must be deployed on separate infrastructure with separate credentials.
The bot NEVER sees the RENDER_API_KEY or WATCHDOG_SECRET stored here.

Trigger via:
    POST /kill
    Header: X-Kill-Secret: your_watchdog_secret_here
    
That's it. One endpoint. One job.
"""
import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

app = Flask(__name__)

RENDER_API_KEY    = os.environ["RENDER_API_KEY"]       # Render API key — NEVER share with bot
RENDER_SERVICE_ID = os.environ["RENDER_SERVICE_ID"]    # The bot's Render service ID (srv-xxxx)
WATCHDOG_SECRET   = os.environ["WATCHDOG_SECRET"]      # Password to trigger the kill
NOTIFY_URL        = os.environ.get("NOTIFY_URL", "")   # Optional: webhook to notify you when kill fires


def suspend_bot() -> dict:
    """Calls Render's API to hard-suspend the bot service."""
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/suspend"
    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, timeout=10)
    return {"status_code": resp.status_code, "body": resp.text}


def notify(reason: str):
    """Optional: fire a webhook to tell you the kill switch was used."""
    if not NOTIFY_URL:
        return
    try:
        requests.post(NOTIFY_URL, json={
            "content": f"🛑 KILL SWITCH TRIGGERED\nReason: {reason}\nTime: {datetime.utcnow().isoformat()} UTC"
        }, timeout=5)
    except Exception:
        pass  # notification is best-effort, never block the actual kill


@app.route("/kill", methods=["POST"])
def kill():
    # Validate secret
    provided = request.headers.get("X-Kill-Secret", "")
    if not provided or provided != WATCHDOG_SECRET:
        return jsonify({"error": "unauthorized"}), 403

    reason = request.json.get("reason", "no reason provided") if request.is_json else "no reason provided"

    # Fire the kill
    result = suspend_bot()

    # Notify (best-effort)
    notify(reason)

    # Log locally
    print(f"[KILL FIRED] {datetime.utcnow().isoformat()} | reason: {reason} | render response: {result}")

    if result["status_code"] in (200, 202, 204):
        return jsonify({"status": "suspended", "render_response": result}), 200
    else:
        return jsonify({"status": "render_call_failed", "render_response": result}), 500


@app.route("/health", methods=["GET"])
def health():
    """Simple liveness check — no credentials exposed."""
    return jsonify({"status": "watchdog alive"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
