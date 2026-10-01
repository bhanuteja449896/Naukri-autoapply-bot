"""
sheets_auth.py — One-time Google OAuth2 Setup
==============================================
Run this ONCE on your local machine to generate token.json.
After that, naukri_bot.py will use the token silently.

Steps:
    1. Go to https://console.cloud.google.com/
    2. Create a project → Enable "Google Sheets API"
    3. APIs & Services > Credentials > Create OAuth 2.0 Client ID
       (Application type: Desktop app)
    4. Download the JSON → save as credentials.json in this folder
    5. Run:  python sheets_auth.py
    6. Log in with your Google account in the browser that opens
    7. token.json is created — keep it safe, never commit it to git
"""

from sheets_client import get_sheets_client

if __name__ == "__main__":
    print("Opening browser for Google OAuth2 sign-in...")
    service = get_sheets_client()
    print("✅ Authentication successful! token.json created.")
    print("You can now run naukri_bot.py")
