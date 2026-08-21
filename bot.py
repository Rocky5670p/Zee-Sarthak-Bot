import os
import gc
import time
import asyncio
import logging
import psutil

logging.basicConfig(level=logging.INFO)

from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

class StopTransmission(Exception):
    pass

API_ID = int(os.environ.get("API_ID", "29968148"))
API_HASH = os.environ.get("API_HASH", "0dc95a4aa9b3514b9db31a4331bf630a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8456919664:AAHij8u6pBZ_vtwEnVRYacz2FP8vg8b_1z0")
PORT = int(os.environ.get("PORT", 8080))

DEFAULT_STREAM = "https://shoebinfo.qzz.io/bgmi/zee5.php/0-9-sarthaktv.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
REFERER = "https://www.zee5.com/"

app = Client(
    "ZeeSarthak_Pro_Bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    ipv6=False,
    max_concurrent_transmissions=4
)

ACTIVE_TASKS = {}
LAST_UPLOAD_UPDATE = {}
# Global storage for user engine preferences
USER_PREFERENCES = {} 

def get_system_stats():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return (
        f"📊 **SERVER RESOURCE STATS**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ **CPU Usage:** `{cpu}%`\n"
        f"💾 **RAM Usage:** `{mem.percent}%`\n"
        f"💽 **Disk Usage:** `{disk.percent}%`"
    )

def make_bar(percent):
    filled = int(percent / 10)
    return "▰" * filled + "▱" * (10 - filled)

def parse_time_to_seconds(time_str):
    try:
        parts = list(map(int, time_str.split(':')))
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 1:
            return parts[0]
    except Exception:
        return None

def format_seconds(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def safe_file_cleanup(file_path):
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            print(f"🧹 Cleaned: {file_path}")
        except Exception as e:
            print(f"⚠️ Delete error: {e}")

async def upload_progress(current, total, message, start_time, task_id):
    if task_id in ACTIVE_TASKS and ACTIVE_TASKS[task_id].get("cancelled"):
        raise StopTransmission()

    now = time.time()
    last_t = LAST_UPLOAD_UPDATE.get(task_id, 0)
    if now - last_t < 3.5 and current != total:
        return

    LAST_UPLOAD_UPDATE[task_id] = now
    diff = max(1, now - start_time)
    pct = (current / total) * 100
    speed = current / diff / (1024 * 1024)
    bar = make_bar(pct)

    text = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   ☁️ **UPLOADING TO TELEGRAM**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"  `[{bar}]` **{pct:.1f}%**\n\n"
        f"  ⚡ **Speed:** `{speed:.2f} MB/s`\n"
        f"  📦 **Uploaded:** `{current / (1024*1024):.1f} MB` / `{total / (1024*1024):.1f} MB`\n"
        "──────────────────────"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel Upload", callback_data=f"cancel|{task_id}")]])
    try: await message.edit_text(text, reply_markup=markup)
    except: pass

@app.on_message(filters.command("settings"))
async def settings_handler(client, message):
    user_id = message.from_user.id
    current_engine = USER_PREFERENCES.get(user_id, "Streamlink")
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'✅ ' if current_engine == 'Streamlink' else ''}Streamlink", callback_data="set_engine|Streamlink")],
        [InlineKeyboardButton(f"{'✅ ' if current_engine == 'Ffmpeg' else ''}Ffmpeg", callback_data="set_engine|Ffmpeg")],
        [InlineKeyboardButton(f"{'✅ ' if current_engine == 'Nm3u8dlre' else ''}Nm3u8dlre", callback_data="set_engine|Nm3u8dlre")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_start")]
    ])
    await message.reply_text("🔴 **Stream Recording Engine:**", reply_markup=markup)

@app.on_message(filters.command("rec"))
async def record_handler(client, message):
    user_id = message.from_user.id
    engine = USER_PREFERENCES.get(user_id, "Streamlink")
    
    args = message.command[1:]
    if not args:
        await message.reply_text("⚠️ **Format:** `/rec HH:MM:SS`")
        return

    stream_url = DEFAULT_STREAM
    time_arg = args[0] if len(args) == 1 else args[1]
    if len(args) >= 2: stream_url = args[0].strip('"').strip("'")

    total_sec = parse_time_to_seconds(time_arg)
    duration_str = format_seconds(total_sec)
    task_id = str(int(time.time()))
    output_file = f"ZeeSarthak_{task_id}.mp4"

    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": output_file}

    # Dynamic Engine Logic
    if engine == "Streamlink":
        cmd_exec = [
            "streamlink", "--http-header", f"User-Agent={USER_AGENT}",
            "--http-header", f"Referer={REFERER}", "--hls-duration", duration_str,
            "--default-stream", "best", stream_url, "best", "--stdout"
        ]
        # Pipe to ffmpeg for sync
        shell_cmd = " ".join(cmd_exec) + f' | ffmpeg -fflags +genpts -i pipe:0 -c:v copy -c:a aac -avoid_negative_ts make_zero -y "{output_file}"'
    else:
        # Placeholder for other engines (Expand as needed)
        shell_cmd = f'ffmpeg -i "{stream_url}" -t {duration_str} -c copy "{output_file}"'

    status_msg = await message.reply_text(f"🔴 **Recording using {engine}...**")

    try:
        proc = await asyncio.create_subprocess_shell(shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ACTIVE_TASKS[task_id]["proc"] = proc
        start_t = time.time()

        while proc.returncode is None:
            if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
                proc.kill(); safe_file_cleanup(output_file); return
            await asyncio.sleep(5)
            
        await proc.wait()
        
        await client.send_video(message.chat.id, video=output_file, caption=f"📺 **Engine:** `{engine}`", progress=upload_progress, progress_args=(status_msg, time.time(), task_id))
        await status_msg.delete()
    except Exception as e:
        await message.reply_text(f"⚠️ Error: {e}")
    finally:
        safe_file_cleanup(output_file)

@app.on_callback_query()
async def callback_router(client, query: CallbackQuery):
    data = query.data
    if data.startswith("set_engine|"):
        engine = data.split("|")[1]
        USER_PREFERENCES[query.from_user.id] = engine
        await query.answer(f"✅ Set to {engine}")
        # Refresh Menu
        await settings_handler(client, query.message)
    elif data == "back_start":
        # ... logic for home menu ...
        pass

# ... rest of the main loop ...
