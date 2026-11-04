import os, json, sqlite3, asyncio, hashlib, hmac, time, urllib.parse
from contextlib import closing
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "").rstrip("/")
APP_NAME = os.getenv("APP_NAME", "GRAM EARN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "gram_earn.db")
CHANNEL_1 = os.getenv("CHANNEL_1", "@YourOfficialChannel1")
CHANNEL_2 = os.getenv("CHANNEL_2", "@YourOfficialChannel2")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

app = FastAPI(title=APP_NAME)
bot_app = None

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(db()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
          balance REAL DEFAULT 0, spins INTEGER DEFAULT 0, wallet TEXT DEFAULT '',
          referred_by INTEGER DEFAULT 0, created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS spin_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, reward REAL,
          label TEXT, created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS task_claims(
          user_id INTEGER, task_id TEXT, created_at INTEGER,
          PRIMARY KEY(user_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS daily_spin_claims(
          user_id INTEGER PRIMARY KEY, claimed_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS tasks(
          task_id TEXT PRIMARY KEY, title TEXT, channel TEXT, reward REAL,
          kind TEXT DEFAULT 'channel', approved INTEGER DEFAULT 1, owner_id INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS submissions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel TEXT,
          reward REAL, status TEXT DEFAULT 'pending', created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
          wallet TEXT, status TEXT DEFAULT 'pending', created_at INTEGER
        );
        """)
        c.execute("INSERT OR IGNORE INTO tasks(task_id,title,channel,reward,kind,approved) VALUES(?,?,?,?,?,1)",
                  ("official_1","Official Channel 1",CHANNEL_1,0.02,"official"))
        c.execute("INSERT OR IGNORE INTO tasks(task_id,title,channel,reward,kind,approved) VALUES(?,?,?,?,?,1)",
                  ("official_2","Official Channel 2",CHANNEL_2,0.02,"official"))
        c.commit()

def verify_init_data(init_data: str):
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN is not configured")
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash")
        auth_date = int(parsed.get("auth_date", "0"))
        if not auth_date or time.time() - auth_date > 86400:
            raise ValueError("Expired Telegram session")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("Invalid Telegram signature")
        user = json.loads(parsed["user"])
        return user, parsed.get("start_param", "")
    except Exception as e:
        raise HTTPException(401, f"Telegram verification failed: {str(e)}")

async def current_user(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise HTTPException(401, "Open this Mini App from your Telegram bot.")
    user, start_param = verify_init_data(init_data)
    uid = int(user["id"])
    with closing(db()) as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            referrer = 0
            if start_param.startswith("ref_"):
                try: referrer = int(start_param[4:])
                except: referrer = 0
            if referrer == uid: referrer = 0
            c.execute("""INSERT INTO users(user_id,username,first_name,referred_by,created_at,spins)
                         VALUES(?,?,?,?,?,?)""",
                      (uid, user.get("username",""), user.get("first_name",""), referrer, int(time.time()), 0))
            if referrer:
                c.execute("UPDATE users SET spins=spins+1 WHERE user_id=?", (referrer,))
            c.commit()
        else:
            c.execute("UPDATE users SET username=?,first_name=? WHERE user_id=?",
                      (user.get("username",""), user.get("first_name",""), uid))
            c.commit()
    return uid

@app.on_event("startup")
async def startup():
    global bot_app
    init_db()
    if BOT_TOKEN:
        bot_app = Application.builder().token(BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start_command))
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
        await update.message.reply_text("Mini App is not configured yet. Set APP_URL in Railway.")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Open Mini App", web_app=WebAppInfo(url=APP_URL))
    ]])
    await update.message.reply_text(
        f"👋 Welcome to {APP_NAME}!\n\nTap below to open your earning dashboard.",
        reply_markup=keyboard
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

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
    return {"app_name": APP_NAME, "bot_username": BOT_USERNAME,
            "channels": [CHANNEL_1, CHANNEL_2], "tonconnect_manifest": APP_URL + "/tonconnect-manifest.json"}

@app.get("/api/me")
async def me(request: Request):
    uid = await current_user(request)
    with closing(db()) as c:
        u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        referrals = c.execute("SELECT COUNT(*) n FROM users WHERE referred_by=?", (uid,)).fetchone()["n"]
        spins_today = c.execute("SELECT COUNT(*) n FROM spin_log WHERE user_id=? AND created_at>=?",
                                (uid, int(time.time())-86400)).fetchone()["n"]
        return dict(user_id=uid, username=u["username"], first_name=u["first_name"],
                    balance=round(u["balance"], 6), spins=u["spins"], wallet=u["wallet"],
                    referrals=referrals, spins_today=spins_today)

@app.get("/api/tasks")
async def tasks(request: Request):
    uid = await current_user(request)
    with closing(db()) as c:
        rows = c.execute("SELECT * FROM tasks WHERE approved=1 ORDER BY CASE kind WHEN 'official' THEN 0 ELSE 1 END, task_id").fetchall()
        claimed = {r["task_id"] for r in c.execute("SELECT task_id FROM task_claims WHERE user_id=?", (uid,))}
        return {"tasks":[{"id":r["task_id"],"title":r["title"],"channel":r["channel"],
                          "reward":r["reward"],"kind":r["kind"],"claimed":r["task_id"] in claimed}
                         for r in rows]}

@app.post("/api/task/{task_id}/claim")
async def claim_task(task_id: str, request: Request):
    uid = await current_user(request)
    with closing(db()) as c:
        task = c.execute("SELECT * FROM tasks WHERE task_id=? AND approved=1", (task_id,)).fetchone()
        if not task: raise HTTPException(404, "Task not found")
        if c.execute("SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?", (uid,task_id)).fetchone():
            raise HTTPException(400, "Already claimed")
        if not bot_app: raise HTTPException(503, "Bot verification is not configured")
        try:
            member = await bot_app.bot.get_chat_member(task["channel"], uid)
            if member.status not in ("member","administrator","creator"):
                raise HTTPException(400, "Join the channel first, then try again.")
        except HTTPException: raise
        except Exception:
            raise HTTPException(400, "Could not verify membership. Check channel username and bot admin permissions.")
        c.execute("INSERT INTO task_claims(user_id,task_id,created_at) VALUES(?,?,?)",(uid,task_id,int(time.time())))
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?",(task["reward"],uid))
        c.commit()
        add_commission(c, uid, float(task["reward"]))
        return {"ok":True,"reward":task["reward"]}

def add_commission(c, earner_id, amount):
    row = c.execute("SELECT referred_by FROM users WHERE user_id=?", (earner_id,)).fetchone()
    if row and row["referred_by"]:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount*0.10,row["referred_by"]))

@app.post("/api/spin")
async def spin(request: Request):
    uid = await current_user(request)
    import random
    with closing(db()) as c:
        u = c.execute("SELECT spins FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u or u["spins"] < 1: raise HTTPException(400, "No spins available")
        # Percentages: Nothing .99%, 0.01=80%, 0.1=19%, 1=.008%, 10=.001%, 100=.001%
        reward = random.choices([0,0.01,0.1,1,10,100], weights=[0.99,80,19,0.008,0.001,0.001], k=1)[0]
        label = "Nothing" if reward == 0 else f"{reward:g} GRAM"
        c.execute("UPDATE users SET spins=spins-1,balance=balance+? WHERE user_id=?", (reward,uid))
        c.execute("INSERT INTO spin_log(user_id,reward,label,created_at) VALUES(?,?,?,?)",
                  (uid,reward,label,int(time.time())))
        if reward: add_commission(c,uid,reward)
        c.commit()
        return {"ok":True,"reward":reward,"label":label}

@app.post("/api/daily-spin")
async def daily_spin(request: Request):
    uid = await current_user(request)
    with closing(db()) as c:
        now = int(time.time())
        row = c.execute("SELECT claimed_at FROM daily_spin_claims WHERE user_id=?",(uid,)).fetchone()
        if row and now - row["claimed_at"] < 86400:
            raise HTTPException(400,"Your daily spin has already been claimed. Try again after 24 hours.")
        c.execute("INSERT INTO daily_spin_claims(user_id,claimed_at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET claimed_at=excluded.claimed_at",(uid,now))
        c.execute("UPDATE users SET spins=spins+1 WHERE user_id=?",(uid,))
        c.commit()
        return {"ok":True,"message":"Daily spin added. You can spin now."}

@app.post("/api/wallet")
async def save_wallet(request: Request):
    uid = await current_user(request)
    body = await request.json()
    address = str(body.get("address","")).strip()
    if len(address) < 20 or len(address) > 150:
        raise HTTPException(400,"Invalid TON wallet address")
    with closing(db()) as c:
        c.execute("UPDATE users SET wallet=? WHERE user_id=?",(address,uid))
        c.commit()
    return {"ok":True}

@app.post("/api/submit-task")
async def submit_task(request: Request):
    uid = await current_user(request)
    body = await request.json()
    channel = str(body.get("channel","")).strip()
    try: reward = float(body.get("reward",0.02))
    except: reward = 0
    if not channel.startswith("@") or len(channel) < 5 or reward <= 0 or reward > 1:
        raise HTTPException(400,"Enter a public channel username and a reward up to 1 GRAM.")
    with closing(db()) as c:
        c.execute("INSERT INTO submissions(user_id,channel,reward,created_at) VALUES(?,?,?,?)",
                  (uid,channel,reward,int(time.time())))
        c.commit()
    return {"ok":True,"message":"Submitted for admin review."}

@app.post("/api/withdraw")
async def withdraw(request: Request):
    uid = await current_user(request)
    body = await request.json()
    try: amount = float(body.get("amount",0))
    except: amount = 0
    with closing(db()) as c:
        u = c.execute("SELECT balance,wallet FROM users WHERE user_id=?",(uid,)).fetchone()
        if not u["wallet"]: raise HTTPException(400,"Connect your TON wallet first.")
        if amount < 1: raise HTTPException(400,"Minimum withdrawal is 1 GRAM.")
        if amount > u["balance"]: raise HTTPException(400,"Insufficient balance.")
        # Reserve balance immediately; admin must process actual token transfer manually.
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?",(amount,uid))
        c.execute("INSERT INTO withdrawals(user_id,amount,wallet,created_at) VALUES(?,?,?,?)",
                  (uid,amount,u["wallet"],int(time.time())))
        c.commit()
    return {"ok":True,"message":"Withdrawal request submitted for manual admin review."}

@app.get("/api/history")
async def history(request: Request):
    uid = await current_user(request)
    with closing(db()) as c:
        rows = c.execute("SELECT label,reward,created_at FROM spin_log WHERE user_id=? ORDER BY id DESC LIMIT 15",(uid,)).fetchall()
        return {"spins":[dict(r) for r in rows]}

@app.get("/health")
async def health():
    return {"ok":True}
