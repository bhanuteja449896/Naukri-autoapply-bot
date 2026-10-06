#!/usr/bin/env python3
"""
register_telegram.py — Automatically capture Chat IDs when friends send 'hi' to the bot.
========================================================================================
Usage:
    python3 register_telegram.py
    python3 register_telegram.py --user kiran
    python3 register_telegram.py --user rahul
"""

import sys
import os
import json
import time
import urllib.request
import urllib.parse
import subprocess

BOT_TOKEN = "8914103380:AAEEZxk-zNgH2VLhtL4PXirU9v5BfQxafow"

def delete_webhook():
    """Ensure webhook is removed so getUpdates receives messages."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            pass
    except Exception:
        pass

def get_bot_info():
    """Get bot username."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
            return data.get("result", {}).get("username", "your bot")
    except Exception:
        return "your bot"

def fetch_recent_messages():
    """Fetch all unread messages from Telegram."""
    delete_webhook()
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
            if not data.get("ok"):
                return []
            updates = data.get("result", [])
            messages = []
            for u in updates:
                msg = u.get("message") or u.get("callback_query", {}).get("message")
                if not msg:
                    continue
                chat = msg.get("chat", {})
                from_user = msg.get("from", {})
                chat_id = str(chat.get("id"))
                first_name = from_user.get("first_name", "")
                last_name = from_user.get("last_name", "")
                username = from_user.get("username", "")
                full_name = f"{first_name} {last_name}".strip() or username or f"User {chat_id}"
                text = msg.get("text", "")
                messages.append({
                    "chat_id": chat_id,
                    "name": full_name,
                    "username": username,
                    "text": text
                })
            return messages
    except Exception as e:
        print(f"Error fetching updates: {e}")
        return []

def update_env_file(bot_name: str, chat_id: str):
    """Write the new chat ID into .env.<bot_name> and sync to Cloud Run."""
    env_file = f".env.{bot_name}"
    if not os.path.exists(env_file):
        print(f"❌ Error: {env_file} does not exist!")
        return False

    with open(env_file, "r") as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("TELEGRAM_CHAT_ID="):
            new_lines.append(f"TELEGRAM_CHAT_ID={chat_id}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"TELEGRAM_CHAT_ID={chat_id}\n")

    with open(env_file, "w") as f:
        writelines = f.writelines(new_lines)

    print(f"✅ Updated {env_file} with TELEGRAM_CHAT_ID={chat_id}")
    print(f"🚀 Deploying change to Cloud Run...")
    subprocess.run(["./sync_env.sh", bot_name], check=True)
    return True

def main():
    target_user = None
    if len(sys.argv) > 1 and sys.argv[1] in ("--user", "-u") and len(sys.argv) > 2:
        target_user = sys.argv[2].lower()

    bot_username = get_bot_info()
    print("=" * 65)
    print(f"📲 Telegram Chat ID Auto-Detector")
    print(f"   Bot: @{bot_username}")
    print("=" * 65)
    print(f"Ask your friend to open Telegram, search @{bot_username}, and send 'hi' or '/start'.\n")

    print("Listening for incoming messages...")
    seen_ids = set()
    found_messages = []

    for _ in range(30):  # Poll for up to 60 seconds
        msgs = fetch_recent_messages()
        for m in msgs:
            cid = m["chat_id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                found_messages.append(m)
                print(f"\n🎉 Message received from: {m['name']} (@{m.get('username') or 'no_handle'})")
                print(f"   Chat ID : {cid}")
                print(f"   Message : '{m['text']}'")
        if found_messages:
            break
        time.sleep(2)

    if not found_messages:
        print("\n⏳ No new messages received yet. Please ask them to send a message and run this script again.")
        return

    latest = found_messages[-1]
    cid = latest["chat_id"]
    sender = latest["name"]

    if target_user in ("bhanu", "kiran", "rahul"):
        chosen_user = target_user
    else:
        print("\nWhich bot profile does this Chat ID belong to?")
        print(f"  [1] Kiran Kumar  (.env.kiran)")
        print(f"  [2] Rahul RP     (.env.rahul)")
        print(f"  [3] Bhanu Teja   (.env.bhanu)")
        choice = input("\nEnter choice (1/2/3): ").strip()
        mapping = {"1": "kiran", "2": "rahul", "3": "bhanu"}
        chosen_user = mapping.get(choice)

    if chosen_user:
        update_env_file(chosen_user, cid)
        print(f"\n🎉 SUCCESS! {sender} (Chat ID: {cid}) will now receive job application alerts for {chosen_user.capitalize()}!")
    else:
        print("Invalid selection. Exiting without modifying files.")

if __name__ == "__main__":
    main()
