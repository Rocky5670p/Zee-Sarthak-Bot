import os
import gc
import time
import asyncio
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

class StopTransmission(Exception):
    pass

# Environment Variables (Set these on Render Dashboard)
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
    done = int(percent / 10)
    return "█" * done + "▒" * (10 - done)

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
            print(f"🧹 Cleaned from disk: {file_path}")
        except Exception as e:
            print(f"⚠️ Delete error: {e}")

async def upload_progress(current, total, message, start_time, task_id):
    if task_id in ACTIVE_TASKS and ACTIVE_TASKS[task_id].get("cancelled"):
        raise StopTransmission()

    now = time.time()
    last_t = LAST_UPLOAD_UPDATE.get(task_id, 0)
    
    if now - last_t < 4 and current != total:
        return

    LAST_UPLOAD_UPDATE[task_id] = now
    diff = max(1, now - start_time)

    pct = (current / total) * 100
    speed = current / diff / (1024 * 1024)
    bar = make_bar(pct)

    text = (
        f"📤 **UPLOADING TO TELEGRAM**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"[{bar}] `{pct:.1f}%`\n"
        f"⚡ **Speed:** `{speed:.2f} MB/s`\n"
        f"📦 **Size:** `{current / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB`"
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Upload", callback_data=f"cancel|{task_id}")]])
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:
        pass

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    text = (
        "🎬 **ZEE SARTHAK 24/7 CLOUD RECORDER** 🎬\n\n"
        "**Usage:**\n"
        "• `/rec 00:01:00` (Direct Zee Sarthak)\n"
        "• `/rec <URL> 00:30:00` (Custom Streamlink URL)"
    )
    await message.reply_text(text)

@app.on_message(filters.command("rec"))
async def record_handler(client, message):
    args = message.command[1:]
    if not args:
        await message.reply_text("❌ **Format:** `/rec HH:MM:SS` ya `/rec <URL> HH:MM:SS`")
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
        await message.reply_text("❌ **Invalid Duration!** Format: `HH:MM:SS` (e.g. `00:02:30`)")
        return

    duration_str = format_seconds(total_sec)
    task_id = str(int(time.time()))
    output_file = f"ZeeSarthak_{task_id}.mp4"

    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": output_file}

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Recording", callback_data=f"cancel|{task_id}")]])
    status_msg = await message.reply_text(
        f"🔴 **RECORDING INITIALIZED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **Target Duration:** `{duration_str}`\n"
        f"⚙️ **Engine:** `Streamlink AIO`\n"
        f"⏳ Connecting cloud stream...",
        reply_markup=markup
    )

    cmd = [
        "streamlink",
        "--http-header", f"User-Agent={USER_AGENT}",
        "--http-header", f"Referer={REFERER}",
        "--hls-duration", duration_str,
        "--default-stream", "best",
        stream_url, "best",
        "-o", output_file
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
                await status_msg.edit_text("🛑 **Recording Cancelled by User.**")
                return

            elapsed = int(time.time() - start_t)
            if elapsed > total_sec:
                elapsed = total_sec

            pct = min(100.0, (elapsed / total_sec) * 100)
            bar = make_bar(pct)

            text = (
                f"🔴 **RECORDING LIVE STREAM**\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"[{bar}] `{pct:.1f}%`\n"
                f"⏱️ **Recorded:** `{format_seconds(elapsed)}` / `{duration_str}`\n"
                f"⚡ **Engine:** `Streamlink`"
            )
            try:
                await status_msg.edit_text(text, reply_markup=markup)
            except Exception:
                pass

            await asyncio.sleep(3)

        await proc.wait()

        if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
            safe_file_cleanup(output_file)
            return

        if not os.path.exists(output_file) or os.path.getsize(output_file) < 5000:
            await status_msg.edit_text("❌ **Recording Failed!** Stream offline hai ya URL invalid hai.")
            safe_file_cleanup(output_file)
            return

        await status_msg.edit_text("📤 **Preparing to upload to Telegram...**")
        start_up = time.time()

        await client.send_video(
            chat_id=message.chat.id,
            video=output_file,
            caption=f"📺 **Zee Sarthak Recording**\n⏱️ **Duration:** `{duration_str}`",
            supports_streaming=True,
            progress=upload_progress,
            progress_args=(status_msg, start_up, task_id)
        )
        await status_msg.delete()

    except StopTransmission:
        await status_msg.edit_text("❌ **Upload Cancelled by User.**")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ **Error:** `{str(e)}`")
    finally:
        # Guarantee instant disk cleanup to prevent Render disk full crash
        safe_file_cleanup(output_file)
        if task_id in ACTIVE_TASKS:
            del ACTIVE_TASKS[task_id]
        if task_id in LAST_UPLOAD_UPDATE:
            del LAST_UPLOAD_UPDATE[task_id]
        gc.collect()

@app.on_callback_query(filters.regex(r"^cancel\|"))
async def cancel_callback(client, callback_query: CallbackQuery):
    task_id = callback_query.data.split("|")[1]
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
        await callback_query.answer("🛑 Task Cancelled & Cleaned!")
    else:
        await callback_query.answer("Task not found.")

# Dummy HTTP Server to satisfy Render Port Binding & Uptime Pings
async def web_root(request):
    return web.Response(text="Zee Sarthak Recorder Bot is Live and Healthy 🚀")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", web_root)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🌐 Dummy Health Server running on port {PORT}")

async def main():
    await start_web_server()
    await app.start()
    me = await app.get_me()
    print("====================================")
    print(f"✅ BOT DEPLOYED: @{me.username}")
    print("====================================")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
