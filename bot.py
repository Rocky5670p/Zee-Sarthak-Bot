import os
import gc
import time
import asyncio
import logging

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
        f"  🛡️ **Status:** `Encoding & Pushing...`\n"
        "──────────────────────"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Cancel Upload", callback_data=f"cancel|{task_id}")]])
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:
        pass

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    text = (
        "✨ **ZEE SARTHAK UHD CLOUD RECORDER** ✨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 **Bot Status:** `Online & Operational 🟢`\n"
        "⚡ **Engine:** `Streamlink + FFmpeg PTS-Sync`\n"
        "☁️ **Platform:** `Render Cloud Server`\n\n"
        "**Available Quick Commands:**\n"
        "• `/rec 00:01:00` ➔ Record 1 Minute\n"
        "• `/rec 00:30:00` ➔ Record 30 Minutes\n"
        "• `/rec <URL> 00:10:00` ➔ Custom Link\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help & Guides", callback_data="help_menu"),
         InlineKeyboardButton("📡 Server Status", callback_data="server_status")]
    ])
    await message.reply_text(text, reply_markup=markup)

@app.on_message(filters.command("rec"))
async def record_handler(client, message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "⚠️ **Invalid Syntax!**\n\n"
            "**Examples:**\n"
            "• `/rec 00:05:00`\n"
            "• `/rec <M3U8_URL> 00:10:00`"
        )
        return

    stream_url = DEFAULT_STREAM
    time_arg = ""

    if len(args) == 1:
        time_arg = args[0]
    elif len(args) >= 2:
        stream_url = args[0].strip('"').strip("'")
        time_arg = args[1]

    total_sec = parse_time_to_seconds(time_arg)
    if not total_sec or total_sec <= 0:
        await message.reply_text("❌ **Invalid Format!** Use `HH:MM:SS` (e.g. `00:02:30`)")
        return

    duration_str = format_seconds(total_sec)
    task_id = str(int(time.time()))
    output_file = f"ZeeSarthak_{task_id}.mp4"

    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": output_file}

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop & Cancel", callback_data=f"cancel|{task_id}")]])
    
    init_text = (
        "┏━━━━━━━━━━━━━━━━━━━━━┓\n"
        "   🔴 **INITIALIZING STREAM**\n"
        "┗━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"  🎯 **Target:** `Zee Sarthak Live`\n"
        f"  ⏱️ **Duration:** `{duration_str}`\n"
        f"  ⚙️ **Sync Mode:** `AV Frame Lock`\n\n"
        "⏳ *Handshaking with stream proxy...*"
    )
    status_msg = await message.reply_text(init_text, reply_markup=markup)

    shell_cmd = (
        f'streamlink --http-header "User-Agent={USER_AGENT}" '
        f'--http-header "Referer={REFERER}" '
        f'--hls-duration {duration_str} '
        f'--default-stream best "{stream_url}" best --stdout | '
        f'ffmpeg -fflags +genpts -i pipe:0 -c:v copy -c:a aac -avoid_negative_ts make_zero -y "{output_file}"'
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
                f"  📡 **Source:** `Zee Sarthak Live`\n"
                f"  ⚡ **Audio Sync:** `Active (AAC Locked)`\n"
                "──────────────────────"
            )
            try:
                await status_msg.edit_text(live_text, reply_markup=markup)
            except Exception:
                pass

            await asyncio.sleep(3)

        await proc.wait()

        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            safe_file_cleanup(output_file)
            return

        if not os.path.exists(output_file) or os.path.getsize(output_file) < 5000:
            await status_msg.edit_text("❌ **Capture Failed!** Stream is offline or link expired.")
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
            f"⚡ **Sync:** `100% Matched`\n"
            f"🤖 **Engine:** `Streamlink + FFmpeg`\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✨ *Recorded via Zee Sarthak Cloud Bot*"
        )

        await client.send_video(
            chat_id=message.chat.id,
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

@app.on_callback_query()
async def callback_router(client, query: CallbackQuery):
    data = query.data
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
        else:
            await query.answer("Task not active.", show_alert=False)

    elif data == "help_menu":
        help_text = (
            "📖 **HOW TO USE RECORDER BOT**\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ **Default Channel (Zee Sarthak):**\n"
            "`/rec 00:02:00` (Records 2 mins)\n\n"
            "2️⃣ **Custom Stream URL:**\n"
            "`/rec https://link.m3u8 00:05:00`\n\n"
            "3️⃣ **Instant Cancel:**\n"
            "Click **⛔ Stop & Cancel** at any time."
        )
        await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))

    elif data == "server_status":
        await query.answer("🟢 Cloud Server Status: Healthy (24/7 Active)", show_alert=True)

    elif data == "back_start":
        text = (
            "✨ **ZEE SARTHAK UHD CLOUD RECORDER** ✨\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🤖 **Bot Status:** `Online & Operational 🟢`\n"
            "⚡ **Engine:** `Streamlink + FFmpeg PTS-Sync`\n"
            "☁️ **Platform:** `Render Cloud Server`\n\n"
            "**Available Quick Commands:**\n"
            "• `/rec 00:01:00` ➔ Record 1 Minute\n"
            "• `/rec 00:30:00` ➔ Record 30 Minutes\n"
            "• `/rec <URL> 00:10:00` ➔ Custom Link\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help & Guides", callback_data="help_menu"),
             InlineKeyboardButton("📡 Server Status", callback_data="server_status")]
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
    print(f"✅ BOT LIVE & DESIGNED: @{me.username}")
    print("====================================")
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
