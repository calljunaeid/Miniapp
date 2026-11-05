GRAM EARN — 7 DAY CHECK-IN UPDATE

PROJECT FILES
- app.py
- static/index.html
- static/icon.png          (the user's original GRAM logo, cropped square)
- static/checkin-banner.png
- static/spin-banner.png

DEPLOYMENT
1. Replace the repository app.py with this app.py.
2. Replace static/index.html.
3. Upload all three image files into the existing static/ folder.
4. Keep requirements.txt and other existing Railway settings unchanged.
5. Keep Start Command:
   uvicorn app:app --host 0.0.0.0 --port $PORT
6. Ensure only ONE Railway replica / polling instance runs this Telegram bot token.

CHECK-IN RULES
- Day 1 through Day 6: 1 free spin per successful daily claim.
- Day 7: 3 free spins.
- A missed Bangladesh calendar day resets the next claim to Day 1.
- A user can claim only once per Bangladesh calendar day (UTC+6).
- After claiming Day 7, the next day's cycle starts at Day 1.

BALANCE
- Available Balance is the spendable balance.
- Total Balance is lifetime GRAM earnings recorded by the app.

IMPORTANT
- Configure CHANNEL_1 and CHANNEL_2 in Railway Variables with the real official channel usernames.
- Existing database is preserved; new check-in and lifetime-earnings columns/tables are migrated automatically at startup.
- This update keeps existing task, referral, spin, wallet, withdrawal, admin campaign, and TON Connect API flows.
