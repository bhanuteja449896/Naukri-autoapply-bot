#!/usr/bin/env python3
"""
register_telegram.py — Automatically capture Chat IDs (Personal or Group) from Telegram.
========================================================================================
Usage:
    python3 register_telegram.py
    python3 register_telegram.py --group     # auto-apply to all 3 bots
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
            return data.get("result", {}).get("username", "GmailAgent001Bot")
    except Exception:
        return "GmailAgent001Bot"

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
                msg = u.get("message") or u.get("callback_query", {}).get("message") or u.get("channel_post")
                if not msg:
                    continue
                chat = msg.get("chat", {})
                from_user = msg.get("from", {})
                chat_id = str(chat.get("id"))
                chat_type = chat.get("type", "private")
                is_group = chat_type in ("group", "supergroup", "channel")
                
                if is_group:
                    group_title = chat.get("title", "Telegram Group")
                    name = f"Group '{group_title}'"
                    username = chat.get("username", "")
                else:
                    first_name = from_user.get("first_name", "")
                    last_name = from_user.get("last_name", "")
                    username = from_user.get("username", "")
                    name = f"{first_name} {last_name}".strip() or username or f"User {chat_id}"
                
                text = msg.get("text", "")
                messages.append({
                    "chat_id": chat_id,
                    "name": name,
                    "username": username,
                    "text": text,
                    "is_group": is_group
                })
            return messages
    except Exception as e:
        print(f"Error fetching updates: {e}")
        return []

def update_env_file(bot_name: str, chat_id: str):
    """Write the new chat ID into .env.<bot_name>."""
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
        f.writelines(new_lines)

    print(f"✅ Updated {env_file} with TELEGRAM_CHAT_ID={chat_id}")
    return True

def main():
    target_all = "--group" in sys.argv or "-g" in sys.argv
    target_user = None
    if len(sys.argv) > 1 and sys.argv[1] in ("--user", "-u") and len(sys.argv) > 2:
        target_user = sys.argv[2].lower()

    bot_username = get_bot_info()
    print("=" * 70)
    print(f"📲 Telegram Chat / Group ID Auto-Detector")
    print(f"   Bot: @{bot_username}")
    print("=" * 70)
    print("Option 1 (Shared Group - Recommended):")
    print(f"  • Create a Telegram Group with your friends.")
    print(f"  • Add @{bot_username} to the group.")
    print(f"  • Send a message like 'hi' in that group.")
    print("\nOption 2 (Direct message):")
    print(f"  • Open @{bot_username} and send 'hi' or '/start'.\n")

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
                print(f"\n🎉 Message received from: {m['name']}")
                print(f"   Chat ID : {cid}")
                print(f"   Message : '{m['text']}'")
        if found_messages:
            break
        time.sleep(2)

    if not found_messages:
        print("\n⏳ No new messages received yet. Please send a message in your group or to the bot and run again.")
        return

    latest = found_messages[-1]
    cid = latest["chat_id"]
    sender = latest["name"]
    is_group = latest["is_group"]

    if target_all or is_group:
        print(f"\nGroup detected or requested: Routing ALL 3 bots to {sender} (Chat ID: {cid})")
        for u in ("bhanu", "kiran", "rahul"):
            update_env_file(u, cid)
        print("🚀 Syncing to Cloud Run for all bots...")
        subprocess.run(["./sync_env.sh", "all"], check=True)
        print(f"\n🎉 SUCCESS! All 3 bots will now broadcast their job application updates to {sender}!")
        return

    if target_user in ("bhanu", "kiran", "rahul"):
        chosen = target_user
    else:
        print("\nWhere do you want to route messages for this Chat ID?")
        print(f"  [1] ALL BOTS (Shared Group / Common feed for everyone)")
        print(f"  [2] Kiran Kumar only (.env.kiran)")
        print(f"  [3] Rahul RP only    (.env.rahul)")
        print(f"  [4] Bhanu Teja only  (.env.bhanu)")
        choice = input("\nEnter choice (1/2/3/4): ").strip()
        mapping = {"1": "all", "2": "kiran", "3": "rahul", "4": "bhanu"}
        chosen = mapping.get(choice)

    if chosen == "all":
        for u in ("bhanu", "kiran", "rahul"):
            update_env_file(u, cid)
        print("🚀 Syncing to Cloud Run for all bots...")
        subprocess.run(["./sync_env.sh", "all"], check=True)
        print(f"\n🎉 SUCCESS! All 3 bots will now broadcast their job application updates to {sender} (Chat ID: {cid})!")
    elif chosen in ("bhanu", "kiran", "rahul"):
        update_env_file(chosen, cid)
        subprocess.run(["./sync_env.sh", chosen], check=True)
        print(f"\n🎉 SUCCESS! {sender} (Chat ID: {cid}) will now receive alerts for {chosen.capitalize()}!")
    else:
        print("Invalid selection. Exiting without modifying files.")

if __name__ == "__main__":
    main()
