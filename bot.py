import os
import gc
import time
import asyncio
import logging
import psutil
import pytz
from datetime import datetime, timedelta

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

IST = pytz.timezone('Asia/Kolkata')

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
PENDING_SCHEDULES = {}
USER_ENGINES = {}  # User-specific Engine Preferences

def get_user_engine(user_id):
    return USER_ENGINES.get(user_id, "FFmpeg")

def get_system_stats():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   📊 **SERVER RESOURCE STATS**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"  🖥️ **CPU Usage:** `{cpu}%`\n"
        f"  💾 **RAM Usage:** `{mem.percent}%`\n"
        f"     • Used: `{mem.used // 1024**2} MB`\n"
        f"     • Total: `{mem.total // 1024**2} MB`\n"
        f"  💽 **Disk Usage:** `{disk.percent}%`\n"
        f"     • Used: `{disk.used // 1024**3} GB`\n"
        f"     • Total: `{disk.total // 1024**3} GB`\n"
        "──────────────────────"
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
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:
        pass

def get_settings_markup(user_id):
    current = get_user_engine(user_id)
    btn_ffmpeg = "✅ FFmpeg (Recommended)" if current == "FFmpeg" else "FFmpeg"
    btn_streamlink = "✅ Streamlink" if current == "Streamlink" else "Streamlink"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_ffmpeg, callback_data="set_engine|FFmpeg")],
        [InlineKeyboardButton(btn_streamlink, callback_data="set_engine|Streamlink")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_start")]
    ])

async def execute_record_stream(client, chat_id, stream_url, total_sec, engine="FFmpeg"):
    duration_str = format_seconds(total_sec)
    task_id = str(int(time.time()))
    output_file = f"ZeeSarthak_{task_id}.mp4"

    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": output_file}

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop & Cancel", callback_data=f"cancel|{task_id}")]])
    
    init_text = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   🔴 **INITIALIZING CAPTURE**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"  🎯 **Source:** `{stream_url[:35]}...`\n"
        f"  ⏱️ **Duration:** `{duration_str}`\n"
        f"  ⚙️ **Active Engine:** `{engine}`\n"
        f"  📡 **Mode:** `Auto-Reconnect (3h+ Safe)`\n\n"
        "⏳ *Connecting stream pipeline...*"
    )
    status_msg = await client.send_message(chat_id, init_text, reply_markup=markup)

    # Shell Command generation with Reconnect & Sync flags
    if engine == "Streamlink":
        shell_cmd = (
            f'streamlink --http-header "User-Agent={USER_AGENT}" '
            f'--http-header "Referer={REFERER}" '
            f'--retry-streams 10 --retry-open 10 --hls-live-restart '
            f'--hls-duration {duration_str} '
            f'--default-stream best "{stream_url}" best --stdout | '
            f'ffmpeg -fflags +genpts -i pipe:0 -c:v copy -c:a aac -avoid_negative_ts make_zero -y "{output_file}"'
        )
    else:  # FFmpeg Engine with Full Reconnection Suite
        shell_cmd = (
            f'ffmpeg -hide_banner -loglevel error '
            f'-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
            f'-headers "User-Agent: {USER_AGENT}\r\nReferer: {REFERER}\r\n" '
            f'-i "{stream_url}" -t {total_sec} '
            f'-fflags +genpts -c:v copy -c:a aac -avoid_negative_ts make_zero -y "{output_file}"'
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        ACTIVE_TASKS[task_id]["proc"] = proc
        start_t = time.time()

        while proc.returncode is None:
            if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
                try:
                    proc.kill()
                except Exception:
                    pass
                safe_file_cleanup(output_file)
                await status_msg.edit_text("🛑 **Recording Process Aborted by User.**")
                return

            elapsed = int(time.time() - start_t)
            if elapsed > total_sec:
                elapsed = total_sec

            pct = min(100.0, (elapsed / total_sec) * 100)
            bar = make_bar(pct)

            live_text = (
                "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
                "   🔴 **LIVE CAPTURING**\n"
                "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
                f"  `[{bar}]` **{pct:.1f}%**\n\n"
                f"  ⏱️ **Progress:** `{format_seconds(elapsed)}` / `{duration_str}`\n"
                f"  ⚙️ **Engine:** `{engine}`\n"
                f"  ⚡ **Audio Sync:** `Active (AAC Locked)`\n"
                "──────────────────────"
            )
            try:
                await status_msg.edit_text(live_text, reply_markup=markup)
            except Exception:
                pass

            await asyncio.sleep(4)

        await proc.wait()

        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            safe_file_cleanup(output_file)
            return

        if not os.path.exists(output_file) or os.path.getsize(output_file) < 5000:
            await status_msg.edit_text(f"❌ **Capture Failed!** Stream is offline or link expired in `{engine}` mode.")
            safe_file_cleanup(output_file)
            return

        file_size_mb = os.path.getsize(output_file) / (1024 * 1024)
        await status_msg.edit_text("⚡ **Recording Complete! Preparing upload...**")
        start_up = time.time()

        caption = (
            "📺 **ZEE SARTHAK HD RECORDING**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ **Duration:** `{duration_str}`\n"
            f"📦 **Size:** `{file_size_mb:.2f} MB`\n"
            f"⚙️ **Engine:** `{engine}`\n"
            f"⚡ **Sync:** `100% Matched`\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✨ *Recorded via Zee Sarthak Cloud Bot*"
        )

        await client.send_video(
            chat_id=chat_id,
            video=output_file,
            caption=caption,
            supports_streaming=True,
            progress=upload_progress,
            progress_args=(status_msg, start_up, task_id)
        )
        await status_msg.delete()

    except StopTransmission:
        await status_msg.edit_text("❌ **Upload Cancelled by User.**")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ **Error Occurred:** `{str(e)}`")
    finally:
        safe_file_cleanup(output_file)
        if task_id in ACTIVE_TASKS:
            del ACTIVE_TASKS[task_id]
        if task_id in LAST_UPLOAD_UPDATE:
            del LAST_UPLOAD_UPDATE[task_id]
        gc.collect()

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    user_engine = get_user_engine(message.from_user.id)
    text = (
        "✨ **ZEE SARTHAK UHD CLOUD RECORDER** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 **Bot Status:** `Online & Operational 🟢`\n"
        f"⚙️ **Active Engine:** `{user_engine}`\n"
        "⚡ **Audio Engine:** `FFmpeg PTS-Sync Mode`\n"
        "☁️ **Platform:** `Render Cloud Server`\n\n"
        "**Available Commands:**\n"
        "• `/rec 00:01:00` ➔ Instant Record (Default)\n"
        "• `/rec <URL> 00:30:00` ➔ Custom Link Record\n"
        "• `/schedule 01:30:00` ➔ Schedule Stream\n"
        "• `/settings` ➔ Change Recording Engine\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Settings", callback_data="open_settings"),
         InlineKeyboardButton("📡 Server Status", callback_data="server_status")],
        [InlineKeyboardButton("📖 Help & Guides", callback_data="help_menu")]
    ])
    await message.reply_text(text, reply_markup=markup)

@app.on_message(filters.command("settings"))
async def settings_cmd(client, message):
    user_id = message.from_user.id
    current = get_user_engine(user_id)
    text = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   ⚙️ **ENGINE SETTINGS**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🔴 **Current Selected Engine:** `{current}`\n\n"
        "• **FFmpeg:** Best for direct `.ts`, `.m3u8` links, auth tokens, long schedules.\n"
        "• **Streamlink:** Best for master HLS feeds and web streams.\n\n"
        "👇 **Select your recording engine:**"
    )
    await message.reply_text(text, reply_markup=get_settings_markup(user_id))

@app.on_message(filters.command("rec"))
async def record_cmd(client, message):
    args = message.command[1:]
    if not args:
        await message.reply_text("⚠️ **Invalid Syntax!** Use `/rec HH:MM:SS` ya `/rec <URL> HH:MM:SS`")
        return

    stream_url = DEFAULT_STREAM
    time_arg = args[0] if len(args) == 1 else args[1]
    if len(args) >= 2:
        stream_url = args[0].strip('"').strip("'")

    total_sec = parse_time_to_seconds(time_arg)
    if not total_sec or total_sec <= 0:
        await message.reply_text("❌ **Invalid Duration!** Format: `HH:MM:SS` (e.g. `00:02:30`)")
        return

    engine = get_user_engine(message.from_user.id)
    await execute_record_stream(client, message.chat.id, stream_url, total_sec, engine)

@app.on_message(filters.command("schedule"))
async def schedule_cmd(client, message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "⚠️ **Schedule Usage:**\n"
            "• `/schedule HH:MM:SS`\n"
            "• `/schedule <URL> HH:MM:SS`"
        )
        return

    stream_url = DEFAULT_STREAM
    time_arg = args[0] if len(args) == 1 else args[1]
    if len(args) >= 2:
        stream_url = args[0].strip('"').strip("'")

    total_sec = parse_time_to_seconds(time_arg)
    if not total_sec or total_sec <= 0:
        await message.reply_text("❌ **Invalid Duration!** Format: `HH:MM:SS`")
        return

    user_id = message.from_user.id
    engine = get_user_engine(user_id)
    PENDING_SCHEDULES[user_id] = {
        "stream_url": stream_url,
        "duration_sec": total_sec,
        "duration_str": format_seconds(total_sec),
        "engine": engine
    }

    now_ist = datetime.now(IST).strftime("%I:%M %p")
    schedule_prompt = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   ⏰ **SET START TIME (IST)**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🎯 **Target Duration:** `{format_seconds(total_sec)}`\n"
        f"⚙️ **Selected Engine:** `{engine}`\n"
        f"🕒 **Current IST Time:** `{now_ist}`\n\n"
        "👉 **Ab starting time reply karein:**\n"
        "Examples: `2:00pm`, `02:00 PM`, `14:30`"
    )
    await message.reply_text(schedule_prompt)

@app.on_message(filters.text & ~filters.command(["start", "rec", "schedule", "settings"]))
async def handle_time_input(client, message):
    user_id = message.from_user.id
    if user_id not in PENDING_SCHEDULES:
        return

    sched_data = PENDING_SCHEDULES.pop(user_id)
    time_input = message.text.strip()

    target_time = None
    now = datetime.now(IST)

    for fmt in ["%I:%M%p", "%I:%M %p", "%H:%M", "%I%p"]:
        try:
            parsed = datetime.strptime(time_input.replace(" ", "").upper(), fmt.replace(" ", ""))
            target_time = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            break
        except ValueError:
            pass

    if not target_time:
        await message.reply_text("❌ **Invalid Time Format!** Examples: `2:00pm`, `02:00 PM`, `14:00`")
        return

    if target_time <= now:
        target_time += timedelta(days=1)

    wait_seconds = int((target_time - now).total_seconds())
    end_time = target_time + timedelta(seconds=sched_data["duration_sec"])

    task_id = str(int(time.time()))
    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": None}

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel Schedule", callback_data=f"cancel|{task_id}")]])
    
    confirm_text = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   📅 **RECORDING SCHEDULED**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"⏱️ **Start Time:** `{target_time.strftime('%I:%M %p (%d %b)')}`\n"
        f"🏁 **End Time:** `{end_time.strftime('%I:%M %p')}`\n"
        f"⏳ **Duration:** `{sched_data['duration_str']}`\n"
        f"⚙️ **Engine:** `{sched_data['engine']}`\n"
        f"⌛ **Waiting In:** `{format_seconds(wait_seconds)}`\n"
        "──────────────────────\n"
        "🟢 *Bot will auto-start recording on exact time!*"
    )
    status_msg = await message.reply_text(confirm_text, reply_markup=markup)

    async def schedule_worker():
        await asyncio.sleep(wait_seconds)
        if not ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            try:
                await status_msg.delete()
            except:
                pass
            await execute_record_stream(
                client, message.chat.id, sched_data["stream_url"], 
                sched_data["duration_sec"], sched_data["engine"]
            )

    asyncio.create_task(schedule_worker())

@app.on_callback_query()
async def callback_router(client, query: CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data.startswith("cancel|"):
        task_id = data.split("|")[1]
        if task_id in ACTIVE_TASKS:
            ACTIVE_TASKS[task_id]["cancelled"] = True
            proc = ACTIVE_TASKS[task_id].get("proc")
            f_path = ACTIVE_TASKS[task_id].get("file")
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            safe_file_cleanup(f_path)
            await query.answer("🛑 Cancelled Successfully!", show_alert=True)
            try:
                await query.message.edit_text("🛑 **Task or Schedule Cancelled by User.**")
            except:
                pass
        else:
            await query.answer("Task not active.", show_alert=False)

    elif data.startswith("set_engine|"):
        selected_engine = data.split("|")[1]
        USER_ENGINES[user_id] = selected_engine
        await query.answer(f"✅ Recording Engine set to {selected_engine}!", show_alert=True)
        
        text = (
            "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
            "   ⚙️ **ENGINE SETTINGS**\n"
            "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"🔴 **Current Selected Engine:** `{selected_engine}`\n\n"
            "• **FFmpeg:** Best for direct `.ts`, `.m3u8` links, auth tokens, and raw live streams.\n"
            "• **Streamlink:** Best for master HLS feeds and standard web streams.\n\n"
            "👇 **Select your recording engine:**"
        )
        try:
            await query.message.edit_text(text, reply_markup=get_settings_markup(user_id))
        except:
            pass

    elif data == "open_settings":
        current = get_user_engine(user_id)
        text = (
            "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
            "   ⚙️ **ENGINE SETTINGS**\n"
            "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"🔴 **Current Selected Engine:** `{current}`\n\n"
            "• **FFmpeg:** Best for direct `.ts`, `.m3u8` links, auth tokens, and raw live streams.\n"
            "• **Streamlink:** Best for master HLS feeds and standard web streams.\n\n"
            "👇 **Select your recording engine:**"
        )
        await query.message.edit_text(text, reply_markup=get_settings_markup(user_id))

    elif data == "help_menu":
        help_text = (
            "📖 **HOW TO USE RECORDER BOT**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ **Instant Record:**\n"
            "`/rec 00:02:00` (Records Zee Sarthak)\n\n"
            "2️⃣ **Custom Link Record:**\n"
            "`/rec <URL> 00:05:00`\n\n"
            "3️⃣ **Schedule Recording:**\n"
            "`/schedule 01:00:00` ➔ Reply with `2:00pm`\n\n"
            "4️⃣ **Engine Switch:**\n"
            "`/settings` ➔ Switch between FFmpeg & Streamlink."
        )
        await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))

    elif data == "server_status":
        stats = get_system_stats()
        await query.message.edit_text(stats, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))

    elif data == "back_start":
        user_engine = get_user_engine(user_id)
        text = (
            "✨ **ZEE SARTHAK UHD CLOUD RECORDER** ✨\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🤖 **Bot Status:** `Online & Operational 🟢`\n"
            f"⚙️ **Active Engine:** `{user_engine}`\n"
            "⚡ **Audio Engine:** `FFmpeg PTS-Sync Mode`\n"
            "☁️ **Platform:** `Render Cloud Server`\n\n"
            "**Available Commands:**\n"
            "• `/rec 00:01:00` ➔ Instant Record (Default)\n"
            "• `/rec <URL> 00:30:00` ➔ Custom Link Record\n"
            "• `/schedule 01:30:00` ➔ Schedule Stream\n"
            "• `/settings` ➔ Change Recording Engine\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Settings", callback_data="open_settings"),
             InlineKeyboardButton("📡 Server Status", callback_data="server_status")],
            [InlineKeyboardButton("📖 Help & Guides", callback_data="help_menu")]
        ])
        await query.message.edit_text(text, reply_markup=markup)

async def web_root(request):
    return web.Response(text="Zee Sarthak Recorder Bot is Live & Healthy 🚀")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", web_root)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Health Server running on port {PORT}")

async def main():
    asyncio.create_task(start_web_server())
    await app.start()
    me = await app.get_me()
    print("====================================")
    print(f"✅ BOT LIVE & ROBUST RECORDER: @{me.username}")
    print("====================================")
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
