import os
import json
import sqlite3
import hashlib
import hmac
import time
import urllib.parse
import random
from contextlib import closing

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "").rstrip("/")
APP_NAME = os.getenv("APP_NAME", "GRAM EARN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "gram_earn.db")

CHANNEL_1 = os.getenv("CHANNEL_1", "@YourOfficialChannel1")
CHANNEL_2 = os.getenv("CHANNEL_2", "@YourOfficialChannel2")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

TASK_REWARD = 0.01
CAMPAIGN_PRICE = 0.012
CAMPAIGN_MINIMUM = 50
CAMPAIGN_COST = 0.600
REFERRAL_COMMISSION = 0.10

app = FastAPI(title=APP_NAME)
bot_app = None


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    with closing(db()) as c:

        c.execute("PRAGMA journal_mode=WAL")

        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            balance REAL DEFAULT 0,
            spins INTEGER DEFAULT 0,
            wallet TEXT DEFAULT '',
            referred_by INTEGER DEFAULT 0,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS spin_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reward REAL,
            label TEXT,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS task_claims(
            user_id INTEGER,
            task_id TEXT,
            created_at INTEGER,
            PRIMARY KEY(user_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS daily_spin_claims(
            user_id INTEGER PRIMARY KEY,
            claimed_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS tasks(
            task_id TEXT PRIMARY KEY,
            title TEXT,
            channel TEXT,
            reward REAL,
            kind TEXT DEFAULT 'channel',
            approved INTEGER DEFAULT 1,
            owner_id INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel TEXT,
            reward REAL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS campaigns(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            channel TEXT,
            title TEXT,
            status TEXT DEFAULT 'pending',
            target INTEGER DEFAULT 50,
            completed INTEGER DEFAULT 0,
            reserved REAL DEFAULT 0.600,
            created_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS withdrawals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            wallet TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        );
        """)

        columns = [
            row["name"]
            for row in c.execute("PRAGMA table_info(users)").fetchall()
        ]

        if "referral_activated" not in columns:
            c.execute(
                "ALTER TABLE users ADD COLUMN referral_activated INTEGER DEFAULT 0"
            )z
        
        
        # Keep existing official tasks, if already present.
        c.execute("""
            INSERT OR IGNORE INTO tasks
            (task_id,title,channel,reward,kind,approved)
            VALUES(?,?,?,?,?,1)
        """, (
            "official_1",
            "Official Channel 1",
            CHANNEL_1,
            TASK_REWARD,
            "official"
        ))

        c.execute("""
            INSERT OR IGNORE INTO tasks
            (task_id,title,channel,reward,kind,approved)
            VALUES(?,?,?,?,?,1)
        """, (
            "official_2",
            "Official Channel 2",
            CHANNEL_2,
            TASK_REWARD,
            "official"
        ))

        # All task rewards are now fixed at 0.01 GRAM.
        c.execute("""
            UPDATE tasks SET reward=?
        """, (TASK_REWARD,))

        c.commit()


# =========================================================
# TELEGRAM MINI APP AUTHENTICATION
# =========================================================

def verify_init_data(init_data: str):

    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN is not configured.")

    try:
        parsed = dict(
            urllib.parse.parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop("hash")

        auth_date = int(parsed.get("auth_date", "0"))

        if not auth_date or time.time() - auth_date > 86400:
            raise ValueError("Expired Telegram session.")

        data_check = "\n".join(
            f"{k}={v}"
            for k, v in sorted(parsed.items())
        )

        secret = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated = hmac.new(
            secret,
            data_check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("Invalid Telegram signature.")

        user = json.loads(parsed["user"])

        return user, parsed.get("start_param", "")

    except Exception as e:
        raise HTTPException(
            401,
            "Telegram verification failed: " + str(e)
        )


async def current_user(request: Request):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    if not init_data:
        raise HTTPException(
            401,
            "Open this Mini App from your Telegram bot."
        )

    user, start_param = verify_init_data(init_data)

    uid = int(user["id"])

    with closing(db()) as c:

        row = c.execute(
            "SELECT * FROM users WHERE user_id=?",
            (uid,)
        ).fetchone()

        if not row:

            referrer = 0

            if start_param.startswith("ref_"):
                try:
                    referrer = int(start_param[4:])
                except Exception:
                    referrer = 0

            if referrer == uid:
                referrer = 0

            c.execute("""
                INSERT INTO users(
                    user_id,username,first_name,
                    referred_by,created_at,spins
                )
                VALUES(?,?,?,?,?,?)
            """, (
                uid,
                user.get("username", ""),
                user.get("first_name", ""),
                referrer,
                int(time.time()),
                0
            ))

            c.commit()

        else:

            c.execute("""
                UPDATE users
                SET username=?,first_name=?
                WHERE user_id=?
            """, (
                user.get("username", ""),
                user.get("first_name", ""),
                uid
            ))

            c.commit()

    return uid


# =========================================================
# REFERRAL COMMISSION
# =========================================================

def add_commission(c, earner_id, amount):

    row = c.execute("""
        SELECT referred_by
        FROM users
        WHERE user_id=?
    """, (earner_id,)).fetchone()

    if row and row["referred_by"]:

        commission = float(amount) * REFERRAL_COMMISSION

        c.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            commission,
            row["referred_by"]
        ))


# =========================================================
# TELEGRAM BOT STARTUP
# =========================================================

@app.on_event("startup")
async def startup():

    global bot_app

    init_db()

    if BOT_TOKEN:

        bot_app = Application.builder().token(BOT_TOKEN).build()

        bot_app.add_handler(
            CommandHandler("start", start_command)
        )

        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()


@app.on_event("shutdown")
async def shutdown():

    global bot_app

    if bot_app:

        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


async def start_command(update, context):

    if not APP_URL.startswith("https://"):

        await update.message.reply_text(
            "Mini App is not configured yet. Set APP_URL in Railway."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🚀 Open Mini App",
            web_app=WebAppInfo(url=APP_URL)
        )
    ]])

    await update.message.reply_text(
        f"👋 Welcome to {APP_NAME}!\n\n"
        "Tap below to open your earning dashboard.",
        reply_markup=keyboard
    )


# =========================================================
# WEB APP
# =========================================================

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)


@app.get("/")
async def home():
    return FileResponse("static/index.html")


@app.get("/tonconnect-manifest.json")
async def ton_manifest():

    return JSONResponse({
        "url": APP_URL,
        "name": APP_NAME,
        "iconUrl": APP_URL + "/static/icon.png"
    })


@app.get("/api/config")
async def config():

    return {
        "app_name": APP_NAME,
        "bot_username": BOT_USERNAME,
        "channels": [CHANNEL_1, CHANNEL_2],
        "tonconnect_manifest": APP_URL + "/tonconnect-manifest.json"
    }


# =========================================================
# USER PROFILE
# =========================================================

@app.get("/api/me")
async def me(request: Request):

    uid = await current_user(request)

    with closing(db()) as c:

        u = c.execute("""
            SELECT * FROM users
            WHERE user_id=?
        """, (uid,)).fetchone()

        referrals = c.execute(
            "SELECT COUNT(*) n FROM users WHERE referred_by=? AND referral_activated=1",
            (uid,)
        ).fetchone()["n"]

        spins_today = c.execute("""
            SELECT COUNT(*) n
            FROM spin_log
            WHERE user_id=? AND created_at>=?
        """, (
            uid,
            int(time.time()) - 86400
        )).fetchone()["n"]

        return {
            "user_id": uid,
            "username": u["username"],
            "first_name": u["first_name"],
            "balance": round(u["balance"], 6),
            "spins": u["spins"],
            "wallet": u["wallet"],
            "referrals": referrals,
            "spins_today": spins_today,
            "is_admin": bool(ADMIN_ID and uid == ADMIN_ID)
        }


# =========================================================
# TASK LIST
# =========================================================

@app.get("/api/tasks")
async def tasks(request: Request):

    uid = await current_user(request)

    with closing(db()) as c:

        rows = c.execute("""
            SELECT * FROM tasks
            WHERE approved=1
            ORDER BY
                CASE kind WHEN 'official' THEN 0 ELSE 1 END,
                task_id
        """).fetchall()

        claimed = {
            r["task_id"]
            for r in c.execute("""
                SELECT task_id
                FROM task_claims
                WHERE user_id=?
            """, (uid,))
        }

        result = []

        for r in rows:

            result.append({
                "id": r["task_id"],
                "title": r["title"],
                "channel": r["channel"],
                "reward": TASK_REWARD,
                "kind": r["kind"],
                "claimed": r["task_id"] in claimed
            })

        campaigns = c.execute("""
            SELECT *
            FROM campaigns
            WHERE status='active'
            AND completed < target
            ORDER BY id DESC
        """).fetchall()

        for camp in campaigns:

            task_id = "camp_" + str(camp["id"])

            result.append({
                "id": task_id,
                "title": camp["title"] or camp["channel"],
                "channel": camp["channel"],
                "reward": TASK_REWARD,
                "kind": "community",
                "claimed": task_id in claimed
            })

        return {"tasks": result}


# =========================================================
# TASK CLAIM — OFFICIAL + COMMUNITY CAMPAIGNS
# =========================================================

def member_is_valid(member):

    if member.status in (
        "member",
        "administrator",
        "creator"
    ):
        return True

    if (
        member.status == "restricted"
        and getattr(member, "is_member", False)
    ):
        return True

    return False


@app.post("/api/task/{task_id}/claim")
async def claim_task(task_id: str, request: Request):

    uid = await current_user(request)

    if not bot_app:
        raise HTTPException(
            503,
            "Bot verification is not configured."
        )

    # Find the channel to verify.
    with closing(db()) as c:

        if task_id.startswith("camp_"):

            try:
                campaign_id = int(
                    task_id.replace("camp_", "")
                )
            except Exception:
                raise HTTPException(400, "Invalid campaign.")

            campaign = c.execute("""
                SELECT *
                FROM campaigns
                WHERE id=? AND status='active'
            """, (campaign_id,)).fetchone()

            if not campaign:
                raise HTTPException(
                    404,
                    "Campaign not found or not active."
                )

            channel = campaign["channel"]

        else:

            task = c.execute("""
                SELECT *
                FROM tasks
                WHERE task_id=? AND approved=1
            """, (task_id,)).fetchone()

            if not task:
                raise HTTPException(
                    404,
                    "Task not found."
                )

            channel = task["channel"]

    # Verify Telegram membership.
    try:

        member = await bot_app.bot.get_chat_member(
            channel,
            uid
        )

        if not member_is_valid(member):
            raise HTTPException(
                400,
                "Join the channel first, then verify again."
            )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            400,
            "Could not verify membership. "
            "Check channel username and bot admin permissions."
        )

    # Lock database while checking duplicate claims and campaign limit.
    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        already = c.execute("""
            SELECT 1
            FROM task_claims
            WHERE user_id=? AND task_id=?
        """, (
            uid,
            task_id
        )).fetchone()

        if already:
            raise HTTPException(
                400,
                "You already claimed this task."
            )

        if task_id.startswith("camp_"):

            campaign = c.execute("""
                SELECT *
                FROM campaigns
                WHERE id=? AND status='active'
            """, (campaign_id,)).fetchone()

            if not campaign:
                raise HTTPException(
                    400,
                    "Campaign is no longer active."
                )

            if campaign["completed"] >= campaign["target"]:
                raise HTTPException(
                    400,
                    "This campaign has already reached its subscriber limit."
                )

            c.execute("""
                INSERT INTO task_claims(
                    user_id,task_id,created_at
                )
                VALUES(?,?,?)
            """, (
                uid,
                task_id,
                int(time.time())
            ))

            c.execute("""
                UPDATE users
                SET balance=balance+?
                WHERE user_id=?
            """, (
                TASK_REWARD,
                uid
            ))

            c.execute("""
                UPDATE campaigns
                SET completed=completed+1
                WHERE id=?
                AND status='active'
                AND completed < target
            """, (campaign_id,))

            c.execute("""
                UPDATE campaigns
                SET status='completed'
                WHERE id=?
                AND completed >= target
            """, (campaign_id,))

            add_commission(
                c,
                uid,
                TASK_REWARD
            )

            c.commit()

            return {
                "ok": True,
                "reward": TASK_REWARD,
                "message": "Campaign task completed."
            }

        # Normal official task claim.
        task = c.execute("""
            SELECT *
            FROM tasks
            WHERE task_id=? AND approved=1
        """, (task_id,)).fetchone()

        if not task:
            raise HTTPException(
                404,
                "Task not found."
            )

        c.execute("""
            INSERT INTO task_claims(
                user_id,task_id,created_at
            )
            VALUES(?,?,?)
        """, (
            uid,
            task_id,
            int(time.time())
        ))

        c.execute("""
            UPDATE users
            SET balance=balance+?
            WHERE user_id=?
        """, (
            TASK_REWARD,
            uid
        ))

        add_commission(
            c,
            uid,
            TASK_REWARD
        )

        c.commit()

        return {
            "ok": True,
            "reward": TASK_REWARD,
            "message": "Task completed."
        }


# =========================================================
# SUBMIT CHANNEL CAMPAIGN
# =========================================================

@app.post("/api/submit-task")
async def submit_task(request: Request):

    uid = await current_user(request)

    body = await request.json()

    channel = str(
        body.get("channel", "")
    ).strip()

    if (
        not channel.startswith("@")
        or len(channel) < 5
        or " " in channel
    ):
        raise HTTPException(
            400,
            "Enter a valid public channel username, e.g. @MyChannel."
        )

    if not bot_app:
        raise HTTPException(
            503,
            "Bot is not available."
        )

    # Check channel ownership and bot admin rights.
    try:

        chat = await bot_app.bot.get_chat(channel)

        if not chat.username:
            raise HTTPException(
                400,
                "The channel must be public."
            )

        owner_member = await bot_app.bot.get_chat_member(
            channel,
            uid
        )

        if owner_member.status not in (
            "administrator",
            "creator"
        ):
            raise HTTPException(
                403,
                "You must be an administrator of this channel."
            )

        bot_info = await bot_app.bot.get_me()

        bot_member = await bot_app.bot.get_chat_member(
            channel,
            bot_info.id
        )

        if bot_member.status not in (
            "administrator",
            "creator"
        ):
            raise HTTPException(
                400,
                "Add this bot as an administrator to your channel first."
            )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            400,
            "Channel verification failed. "
            "Check the public username and bot admin permissions."
        )

    # Reserve the fixed campaign cost.
    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        user = c.execute("""
            SELECT balance
            FROM users
            WHERE user_id=?
        """, (uid,)).fetchone()

        if not user or user["balance"] < CAMPAIGN_COST:
            raise HTTPException(
                400,
                "You need at least 0.600 GRAM to create a 50-subscriber campaign."
            )

        result = c.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=? AND balance>=?
        """, (
            CAMPAIGN_COST,
            uid,
            CAMPAIGN_COST
        ))

        if result.rowcount != 1:
            raise HTTPException(
                400,
                "Insufficient balance."
            )

        c.execute("""
            INSERT INTO campaigns(
                owner_id,
                channel,
                title,
                status,
                target,
                completed,
                reserved,
                created_at
            )
            VALUES(?,?,?,'pending',50,0,?,?)
        """, (
            uid,
            channel,
            chat.title or channel,
            CAMPAIGN_COST,
            int(time.time())
        ))

        c.commit()

    return {
        "ok": True,
        "message": (
            "Campaign submitted successfully. "
            "0.600 GRAM reserved. Waiting for admin approval."
        )
    }


# =========================================================
# ADMIN — PENDING CAMPAIGNS
# =========================================================

@app.get("/api/admin/campaigns")
async def admin_campaigns(request: Request):

    uid = await current_user(request)

    if not ADMIN_ID or uid != ADMIN_ID:
        raise HTTPException(
            403,
            "Admin only."
        )

    with closing(db()) as c:

        rows = c.execute("""
            SELECT *
            FROM campaigns
            WHERE status='pending'
            ORDER BY id ASC
        """).fetchall()

        return {
            "campaigns": [
                dict(r) for r in rows
            ]
        }


# =========================================================
# ADMIN — APPROVE / REJECT CAMPAIGN
# =========================================================

@app.post("/api/admin/campaign/{campaign_id}/{action}")
async def admin_campaign_action(
    campaign_id: int,
    action: str,
    request: Request
):

    uid = await current_user(request)

    if not ADMIN_ID or uid != ADMIN_ID:
        raise HTTPException(
            403,
            "Admin only."
        )

    if action not in (
        "approve",
        "reject"
    ):
        raise HTTPException(
            400,
            "Invalid action."
        )

    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        campaign = c.execute("""
            SELECT *
            FROM campaigns
            WHERE id=? AND status='pending'
        """, (campaign_id,)).fetchone()

        if not campaign:
            raise HTTPException(
                404,
                "Pending campaign not found."
            )

        if action == "approve":

            c.execute("""
                UPDATE campaigns
                SET status='active'
                WHERE id=? AND status='pending'
            """, (campaign_id,))

            message = "Campaign approved and published."

        else:

            c.execute("""
                UPDATE users
                SET balance=balance+?
                WHERE user_id=?
            """, (
                campaign["reserved"],
                campaign["owner_id"]
            ))

            c.execute("""
                UPDATE campaigns
                SET status='rejected'
                WHERE id=? AND status='pending'
            """, (campaign_id,))

            message = (
                "Campaign rejected. "
                "Reserved balance has been refunded."
            )

        c.commit()

    return {
        "ok": True,
        "message": message
    }


# =========================================================
# SPIN
# =========================================================

@app.post("/api/spin")
async def spin(request: Request):

    uid = await current_user(request)

    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        u = c.execute("""
            SELECT spins
            FROM users
            WHERE user_id=?
        """, (uid,)).fetchone()

        if not u or u["spins"] < 1:
            raise HTTPException(
                400,
                "No spins available."
            )

        # Probability:
        # Nothing = 0.99%
        # 0.01 GRAM = 80%
        # 0.1 GRAM = 19%
        # 1 GRAM = 0.008%
        # 10 GRAM = 0.001%
        # 100 GRAM = 0.001%

        reward = random.choices(
            [0, 0.01, 0.1, 1, 10, 100],
            weights=[
                0.99,
                80,
                19,
                0.008,
                0.001,
                0.001
            ],
            k=1
        )[0]

        label = (
            "Nothing"
            if reward == 0
            else f"{reward:g} GRAM"
        )

        c.execute(
            "UPDATE users SET spins=spins-1,balance=balance+? WHERE user_id=?",
            (reward, uid)
        )

        c.execute(
            "INSERT INTO spin_log(user_id,reward,label,created_at) VALUES(?,?,?,?)",
            (uid, reward, label, int(time.time()))
        )

        # FIRST SUCCESSFUL SPIN = REFERRAL ACTIVATION
        referral = c.execute(
            "SELECT referred_by, referral_activated FROM users WHERE user_id=?",
            (uid,)
        ).fetchone()

        if referral and referral["referred_by"] and not referral["referral_activated"]:

            c.execute(
                "UPDATE users SET referral_activated=1 WHERE user_id=?",
                (uid,)
            )

            c.execute(
                "UPDATE users SET spins=spins+1 WHERE user_id=?",
                (referral["referred_by"],)
            )

        if reward:
            add_commission(c, uid, reward)

        c.commit()

        return {
            "ok": True,
            "reward": reward,
            "label": label
        }


# =========================================================
# DAILY SPIN
# =========================================================

@app.post("/api/daily-spin")
async def daily_spin(request: Request):

    uid = await current_user(request)

    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        now = int(time.time())

        row = c.execute("""
            SELECT claimed_at
            FROM daily_spin_claims
            WHERE user_id=?
        """, (uid,)).fetchone()

        if row and now - row["claimed_at"] < 86400:
            raise HTTPException(
                400,
                "Your daily spin has already been claimed. Try again after 24 hours."
            )

        c.execute("""
            INSERT INTO daily_spin_claims(
                user_id,claimed_at
            )
            VALUES(?,?)
            ON CONFLICT(user_id)
            DO UPDATE SET claimed_at=excluded.claimed_at
        """, (
            uid,
            now
        ))

        c.execute("""
            UPDATE users
            SET spins=spins+1
            WHERE user_id=?
        """, (uid,))

        c.commit()

        return {
            "ok": True,
            "message": "Daily spin added. You can spin now."
        }


# =========================================================
# TON WALLET
# =========================================================

@app.post("/api/wallet")
async def save_wallet(request: Request):

    uid = await current_user(request)

    body = await request.json()

    address = str(
        body.get("address", "")
    ).strip()

    if len(address) < 20 or len(address) > 150:
        raise HTTPException(
            400,
            "Invalid TON wallet address."
        )

    with closing(db()) as c:

        c.execute("""
            UPDATE users
            SET wallet=?
            WHERE user_id=?
        """, (
            address,
            uid
        ))

        c.commit()

    return {"ok": True}


# =========================================================
# WITHDRAW
# =========================================================

@app.post("/api/withdraw")
async def withdraw(request: Request):

    uid = await current_user(request)

    body = await request.json()

    try:
        amount = float(body.get("amount", 0))
    except Exception:
        amount = 0

    if amount <= 0:
        raise HTTPException(
            400,
            "Enter a valid withdrawal amount."
        )

    with closing(db()) as c:

        c.execute("BEGIN IMMEDIATE")

        u = c.execute("""
            SELECT balance,wallet
            FROM users
            WHERE user_id=?
        """, (uid,)).fetchone()

        if not u or not u["wallet"]:
            raise HTTPException(
                400,
                "Connect your TON wallet first."
            )

        if amount < 1:
            raise HTTPException(
                400,
                "Minimum withdrawal is 1 GRAM."
            )

        if amount > u["balance"]:
            raise HTTPException(
                400,
                "Insufficient balance."
            )

        c.execute("""
            UPDATE users
            SET balance=balance-?
            WHERE user_id=?
        """, (
            amount,
            uid
        ))

        c.execute("""
            INSERT INTO withdrawals(
                user_id,amount,wallet,created_at
            )
            VALUES(?,?,?,?)
        """, (
            uid,
            amount,
            u["wallet"],
            int(time.time())
        ))

        c.commit()

    return {
        "ok": True,
        "message": (
            "Withdrawal request submitted for manual admin review."
        )
    }


# =========================================================
# USER HISTORY
# =========================================================

@app.get("/api/history")
async def history(request: Request):

    uid = await current_user(request)

    with closing(db()) as c:

        rows = c.execute("""
            SELECT label,reward,created_at
            FROM spin_log
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 15
        """, (uid,)).fetchall()

        return {
            "spins": [
                dict(r) for r in rows
            ]
        }


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
async def health():
    return {"ok": True}
