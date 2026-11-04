# GRAM EARN Telegram Mini App — starter MVP

## Included
- Telegram `/start` welcome + inline Mini App button
- Black / dark-purple mobile UI
- Home, Earn, Invite, Wallet and Spin screens
- Telegram Web App `initData` signature validation
- Daily spin (once per rolling 24 hours) and referral spin
- Server-side weighted spin reward
- 10% referral commission on eligible task/spin earnings
- Official channel join verification (bot must be admin in both channels)
- Community channel task submission (stored as pending for admin review)
- TON Connect UI integration (wallet connection only)
- Withdrawal request queue (manual admin processing; no on-chain transfer)

## Railway setup
1. Create a Railway project and deploy this folder from GitHub.
2. Add these Variables:
   - `BOT_TOKEN` = your Telegram bot token
   - `APP_URL` = your deployed public HTTPS domain, e.g. `https://your-app.up.railway.app`
   - `BOT_USERNAME` = bot username without @
   - `APP_NAME` = `GRAM EARN` (or your final name)
   - `ADMIN_ID` = your Telegram numeric admin ID
   - `CHANNEL_1` = `@YourOfficialChannel1`
   - `CHANNEL_2` = `@YourOfficialChannel2`
3. Set Railway start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Set the bot's Menu Button / Mini App URL in BotFather if you want a persistent menu shortcut. The `/start` inline button is already sent by this code.
5. Add the bot as an administrator to both official channels so it can verify membership.

## Important notes before real-money launch
- This starter uses SQLite for simplicity. Railway's normal container filesystem can be ephemeral; attach a persistent volume or migrate to PostgreSQL before production.
- Set real official channel usernames before opening the app to users.
- The referral link uses Telegram's `startapp` parameter. For a regular bot `/start` referral flow, the backend must also process `ref_...` payloads. This starter's referral spin is awarded when the Mini App first creates a user from a `start_param` value; confirm the Telegram start-link behavior for your chosen bot launch method.
- Spin probabilities: Nothing 0.99%, 0.01 GRAM 80%, 0.1 GRAM 19%, 1 GRAM 0.008%, 10 GRAM 0.001%, 100 GRAM 0.001%.
- Wallet connect is not proof of token ownership or a deposit. Deposits are disabled. Withdrawals are only queued and reserved from the in-app balance; an admin must manually review them. Do not advertise guaranteed payouts until a secure TON jetton transfer and on-chain verification flow is implemented.
- Before production, add admin tools to approve community tasks, review withdrawals, refund rejected withdrawals, rate-limit endpoints, and migrate to PostgreSQL.
