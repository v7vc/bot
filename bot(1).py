import ast
import base64
import io
import json
import logging
import operator
import os
import random
import re
import secrets
import string
from pathlib import Path

import httpx
import qrcode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
SCORE_FILE = Path("scores.json")


# ───────────────────────── امتیازات ─────────────────────────
def load_scores() -> dict:
    if SCORE_FILE.exists():
        try:
            return json.loads(SCORE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def add_score(user, pts: int) -> None:
    data = load_scores()
    uid = str(user.id)
    entry = data.get(uid, {"name": user.first_name, "score": 0})
    entry["name"] = user.first_name
    entry["score"] += pts
    data[uid] = entry
    SCORE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ───────────────────────── منوها ─────────────────────────
def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


MAIN_MENU = kb([
    [("🎮 بازی‌ها", "menu:games"), ("🛠 ابزارها", "menu:tools")],
    [("🏆 جدول امتیازات", "menu:scores")],
])

GAMES_MENU = kb([
    [("🔢 حدس عدد", "game:guess"), ("✂️ سنگ کاغذ قیچی", "game:rps")],
    [("❓ مسابقه", "game:quiz"), ("⭕ دوز (XO)", "game:xo")],
    [("🎲 تاس", "dice:🎲"), ("🎯 دارت", "dice:🎯"), ("🎰 اسلات", "dice:🎰")],
    [("🏀 بسکتبال", "dice:🏀"), ("⚽ فوتبال", "dice:⚽"), ("🎳 بولینگ", "dice:🎳")],
    [("🔙 منوی اصلی", "menu:main")],
])

TOOLS_TEXT = (
    "🛠 <b>ابزارها</b>\n\n"
    "/pass [طول] ← ساخت رمز عبور قوی\n"
    "/calc 2*(3+4) ← ماشین حساب\n"
    "/qr متن ← ساخت QR کد\n"
    "/weather تهران ← آب‌وهوا\n"
    "/remind 10m متن ← یادآور (s, m, h)\n"
    "/b64e متن ← تبدیل به Base64\n"
    "/b64d متن ← برگرداندن از Base64\n"
    "/flip ← شیر یا خط\n"
    "/roll [حداکثر] ← عدد تصادفی\n"
    "/id ← آیدی شما و چت"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"سلام {name} 👋\nبه ربات من خوش اومدی! یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=MAIN_MENU,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TOOLS_TEXT + "\n\n/start ← منوی اصلی", parse_mode="HTML")


# ───────────────────────── ابزارها ─────────────────────────
async def pass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(context.args[0]) if context.args else 16
    except ValueError:
        n = 16
    n = max(6, min(n, 64))
    chars = string.ascii_letters + string.digits + "!@#$%^&*-_"
    pw = "".join(secrets.choice(chars) for _ in range(n))
    await update.message.reply_text(f"🔐 رمز شما:\n<code>{pw}</code>", parse_mode="HTML")


OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_eval(node):
    if isinstance(node, ast.Expression):
        return safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = safe_eval(node.left), safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("توان خیلی بزرگ")
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](safe_eval(node.operand))
    raise ValueError("عبارت نامعتبر")


async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expr = " ".join(context.args)
    if not expr:
        await update.message.reply_text("مثال: /calc 2*(3+4)")
        return
    try:
        result = safe_eval(ast.parse(expr, mode="eval"))
        await update.message.reply_text(f"🧮 {expr} = <b>{result}</b>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ عبارت نامعتبره.")


async def qr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("مثال: /qr https://example.com")
        return
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    await update.message.reply_photo(buf, caption="✅ QR کد آماده شد")


async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = " ".join(context.args)
    if not city:
        await update.message.reply_text("مثال: /weather Tehran")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://wttr.in/{city}", params={"format": "3", "lang": "fa"})
        await update.message.reply_text(r.text.strip() if r.status_code == 200 else "❌ شهر پیدا نشد.")
    except httpx.HTTPError:
        await update.message.reply_text("❌ خطا در دریافت آب‌وهوا.")


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2 or not re.fullmatch(r"\d+[smh]", context.args[0]):
        await update.message.reply_text("مثال: /remind 10m جلسه دارم")
        return
    amount, unit = int(context.args[0][:-1]), context.args[0][-1]
    seconds = amount * {"s": 1, "m": 60, "h": 3600}[unit]
    if seconds > 7 * 24 * 3600:
        await update.message.reply_text("حداکثر یک هفته.")
        return
    text = " ".join(context.args[1:])
    context.job_queue.run_once(
        remind_job, seconds, chat_id=update.effective_chat.id, data=text
    )
    await update.message.reply_text(f"⏰ باشه! {context.args[0]} دیگه یادآوری می‌کنم.")


async def remind_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(context.job.chat_id, f"⏰ یادآوری:\n{context.job.data}")


async def b64e_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("مثال: /b64e hello")
        return
    out = base64.b64encode(text.encode()).decode()
    await update.message.reply_text(f"<code>{out}</code>", parse_mode="HTML")


async def b64d_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    try:
        out = base64.b64decode(text.encode(), validate=True).decode()
        await update.message.reply_text(f"<code>{out}</code>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text("❌ Base64 نامعتبره.")


async def flip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(random.choice(["🪙 شیر", "🪙 خط"]))


async def roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        top = int(context.args[0]) if context.args else 100
    except ValueError:
        top = 100
    top = max(2, top)
    await update.message.reply_text(f"🎲 {random.randint(1, top)}  (۱ تا {top})")


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👤 آیدی شما: <code>{update.effective_user.id}</code>\n"
        f"💬 آیدی چت: <code>{update.effective_chat.id}</code>",
        parse_mode="HTML",
    )


# ───────────────────────── بازی‌ها ─────────────────────────
# --- حدس عدد ---
async def start_guess(query, context):
    context.user_data["guess"] = {"n": random.randint(1, 100), "tries": 0}
    await query.message.reply_text("🔢 یه عدد بین ۱ تا ۱۰۰ انتخاب کردم. حدس بزن! (۷ فرصت داری)")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g = context.user_data.get("guess")
    text = (update.message.text or "").strip()
    if not g or not text.isdigit():
        return
    n = int(text)
    g["tries"] += 1
    if n == g["n"]:
        pts = max(1, 8 - g["tries"]) * 3
        add_score(update.effective_user, pts)
        context.user_data.pop("guess")
        await update.message.reply_text(f"🎉 آفرین! با {g['tries']} حدس پیدا کردی. +{pts} امتیاز")
    elif g["tries"] >= 7:
        context.user_data.pop("guess")
        await update.message.reply_text(f"😅 باختی! عدد {g['n']} بود.")
    else:
        hint = "⬆️ بزرگ‌تر" if n < g["n"] else "⬇️ کوچک‌تر"
        await update.message.reply_text(f"{hint} (حدس {g['tries']}/7)")


# --- سنگ کاغذ قیچی ---
RPS = {"rock": "🪨 سنگ", "paper": "📄 کاغذ", "scissors": "✂️ قیچی"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


async def start_rps(query, context):
    await query.message.reply_text(
        "انتخاب کن:",
        reply_markup=kb([[(v, f"rps:{k}") for k, v in RPS.items()]]),
    )


async def play_rps(query, context, choice):
    bot = random.choice(list(RPS))
    if choice == bot:
        res, pts = "🤝 مساوی!", 1
    elif RPS_BEATS[choice] == bot:
        res, pts = "🎉 بردی!", 5
    else:
        res, pts = "😈 باختی!", 0
    if pts:
        add_score(query.from_user, pts)
    await query.message.edit_text(
        f"تو: {RPS[choice]}\nربات: {RPS[bot]}\n\n{res} (+{pts})",
        reply_markup=kb([[("🔁 دوباره", "game:rps"), ("🔙 بازی‌ها", "menu:games")]]),
    )


# --- مسابقه ---
QUIZ = [
    ("پایتخت ایران کدوم شهره؟", ["اصفهان", "تهران", "شیراز", "تبریز"], 1),
    ("بزرگ‌ترین سیاره منظومه شمسی؟", ["زمین", "مریخ", "مشتری", "زحل"], 2),
    ("۷ × ۸ چنده؟", ["54", "56", "58", "64"], 1),
    ("پایتخت فرانسه؟", ["رم", "مادرید", "برلین", "پاریس"], 3),
    ("کدوم زبان برنامه‌نویسی با «Py» شروع میشه؟", ["Java", "Python", "Ruby", "Go"], 1),
    ("چند ثانیه در یک ساعت هست؟", ["۳۶۰۰", "۶۰۰۰", "۱۸۰۰", "۷۲۰۰"], 0),
    ("بلندترین رودخانه ایران؟", ["کارون", "ارس", "سفیدرود", "هیرمند"], 0),
    ("عنصر شیمیایی با نماد O؟", ["طلا", "اکسیژن", "نقره", "آهن"], 1),
]


async def start_quiz(query, context):
    qi = random.randrange(len(QUIZ))
    q, opts, _ = QUIZ[qi]
    await query.message.reply_text(
        f"❓ {q}",
        reply_markup=kb([[(o, f"quiz:{qi}:{i}")] for i, o in enumerate(opts)]),
    )


async def answer_quiz(query, context, qi, ai):
    q, opts, correct = QUIZ[qi]
    if ai == correct:
        add_score(query.from_user, 5)
        txt = f"✅ درسته! +5 امتیاز\n\n❓ {q}\nپاسخ: {opts[correct]}"
    else:
        txt = f"❌ اشتباه!\n\n❓ {q}\nپاسخ درست: {opts[correct]}"
    await query.message.edit_text(
        txt, reply_markup=kb([[("➡️ سوال بعدی", "game:quiz"), ("🔙 بازی‌ها", "menu:games")]])
    )


# --- دوز ---
WINS = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
SYM = {"": "⬜", "X": "❌", "O": "⭕"}


def xo_winner(b):
    for a, c, d in WINS:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a]
    return None


def xo_markup(b, over=False):
    rows = []
    for r in range(3):
        rows.append([
            InlineKeyboardButton(SYM[b[r * 3 + c]], callback_data="xo:noop" if over else f"xo:{r * 3 + c}")
            for c in range(3)
        ])
    if over:
        rows.append([InlineKeyboardButton("🔁 دوباره", callback_data="game:xo"),
                     InlineKeyboardButton("🔙 بازی‌ها", callback_data="menu:games")])
    return InlineKeyboardMarkup(rows)


def xo_bot_move(b):
    empty = [i for i in range(9) if not b[i]]
    for mark in ("O", "X"):  # اول برنده شو، بعد جلوی حریف رو بگیر
        for i in empty:
            b[i] = mark
            win = xo_winner(b) == mark
            b[i] = ""
            if win:
                return i
    if 4 in empty:
        return 4
    corners = [i for i in (0, 2, 6, 8) if i in empty]
    return random.choice(corners or empty)


async def start_xo(query, context):
    context.user_data["xo"] = [""] * 9
    await query.message.reply_text("⭕ دوز! تو ❌ هستی.", reply_markup=xo_markup(context.user_data["xo"]))


async def play_xo(query, context, idx):
    board = context.user_data.get("xo")
    if board is None or board[idx]:
        return
    board[idx] = "X"
    w = xo_winner(board)
    if not w and any(not c for c in board):
        board[xo_bot_move(board)] = "O"
        w = xo_winner(board)
    full = all(board)
    if w == "X":
        add_score(query.from_user, 10)
        text, over = "🎉 بردی! +10 امتیاز", True
    elif w == "O":
        text, over = "😈 ربات برد!", True
    elif full:
        add_score(query.from_user, 2)
        text, over = "🤝 مساوی! +2 امتیاز", True
    else:
        text, over = "نوبت توئه ❌", False
    if over:
        context.user_data.pop("xo", None)
    await query.message.edit_text(text, reply_markup=xo_markup(board, over))


# --- تاس/دارت/اسلات و ... ---
async def send_dice(query, context, emoji):
    msg = await context.bot.send_dice(query.message.chat_id, emoji=emoji)
    if emoji == "🎰" and msg.dice.value == 64:
        add_score(query.from_user, 20)
        await query.message.reply_text("🎉 جک‌پات! +20 امتیاز")


# ───────────────────────── جدول امتیازات ─────────────────────────
def scoreboard_text():
    data = load_scores()
    if not data:
        return "هنوز کسی امتیازی نگرفته! 🎮"
    top = sorted(data.values(), key=lambda x: x["score"], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
    lines = [f"{medals[i]} {e['name']} — {e['score']}" for i, e in enumerate(top)]
    return "🏆 <b>جدول امتیازات</b>\n\n" + "\n".join(lines)


# ───────────────────────── مسیردهی دکمه‌ها ─────────────────────────
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind, _, rest = query.data.partition(":")

    if kind == "menu":
        if rest == "main":
            await query.message.edit_text("منوی اصلی:", reply_markup=MAIN_MENU)
        elif rest == "games":
            await query.message.edit_text("🎮 یه بازی انتخاب کن:", reply_markup=GAMES_MENU)
        elif rest == "tools":
            await query.message.edit_text(
                TOOLS_TEXT, parse_mode="HTML", reply_markup=kb([[("🔙 منوی اصلی", "menu:main")]])
            )
        elif rest == "scores":
            await query.message.edit_text(
                scoreboard_text(), parse_mode="HTML", reply_markup=kb([[("🔙 منوی اصلی", "menu:main")]])
            )
    elif kind == "game":
        await {"guess": start_guess, "rps": start_rps, "quiz": start_quiz, "xo": start_xo}[rest](query, context)
    elif kind == "dice":
        await send_dice(query, context, rest)
    elif kind == "rps":
        await play_rps(query, context, rest)
    elif kind == "quiz":
        qi, ai = rest.split(":")
        await answer_quiz(query, context, int(qi), int(ai))
    elif kind == "xo" and rest != "noop":
        await play_xo(query, context, int(rest))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("خطا:", exc_info=context.error)


def main():
    if not TOKEN:
        raise SystemExit("متغیر BOT_TOKEN تنظیم نشده. توکن رو از @BotFather بگیر.")
    app = Application.builder().token(TOKEN).build()

    commands = {
        "start": start, "help": help_cmd, "pass": pass_cmd, "calc": calc_cmd, "qr": qr_cmd,
        "weather": weather_cmd, "remind": remind_cmd, "b64e": b64e_cmd, "b64d": b64d_cmd,
        "flip": flip_cmd, "roll": roll_cmd, "id": id_cmd,
    }
    for name, fn in commands.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(on_error)

    log.info("ربات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
