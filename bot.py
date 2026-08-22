import os
import gc
import time
import asyncio
import logging
import psutil
import pytz
from datetime import datetime, timedelta

# Minimal Logging to save RAM
logging.basicConfig(level=logging.ERROR)

from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

class StopTransmission(Exception):
    pass

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", "29968148"))
API_HASH = os.environ.get("API_HASH", "0dc95a4aa9b3514b9db31a4331bf630a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8456919664:AAHij8u6pBZ_vtwEnVRYacz2FP8vg8b_1z0")
PORT = int(os.environ.get("PORT", 8080))
OWNER_ID = int(os.environ.get("OWNER_ID", "8788390728"))

DEFAULT_STREAM = "https://shoebinfo.qzz.io/bgmi/zee5.php/0-9-sarthaktv.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
REFERER = "https://www.zee5.com/"

IST = pytz.timezone('Asia/Kolkata')
app = Client("ZeeSarthak_Pro_Bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS, LAST_UPLOAD_UPDATE, PENDING_SCHEDULES, USER_ENGINES = {}, {}, {}, {}
AUTHORIZED_USERS = {OWNER_ID}

# --- UTILS ---
def is_authorized(user_id): return user_id in AUTHORIZED_USERS
def get_user_engine(user_id): return USER_ENGINES.get(user_id, "FFmpeg")
def format_seconds(seconds):
    h, m, s = int(seconds // 3600), int((seconds % 3600) // 60), int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

async def safe_file_cleanup(file_path):
    if file_path and os.path.exists(file_path):
        try: os.remove(file_path)
        except: pass

# --- CORE ENGINE ---
async def execute_record_stream(client, chat_id, stream_url, total_sec, engine="FFmpeg"):
    task_id = str(int(time.time()))
    output_file = f"Zee_{task_id}.mp4"
    ACTIVE_TASKS[task_id] = {"cancelled": False, "proc": None, "file": output_file}

    def get_cmd():
        if engine == "Streamlink":
            # Streamlink pipe mode
            return f'streamlink --hls-duration {format_seconds(total_sec)} "{stream_url}" best --stdout | ffmpeg -hide_banner -loglevel error -i pipe:0 -c copy -y "{output_file}"'
        else:
            # Direct FFmpeg (Low RAM mode)
            return f'ffmpeg -hide_banner -loglevel error -reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 5 -headers "User-Agent: {USER_AGENT}\r\n" -i "{stream_url}" -t {total_sec} -bufsize 512k -max_muxing_queue_size 1024 -c copy -y "{output_file}"'

    status_msg = await client.send_message(chat_id, f"🔴 **Recording started with {engine}...**")
    
    try:
        shell_cmd = get_cmd()
        proc = await asyncio.create_subprocess_shell(shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ACTIVE_TASKS[task_id]["proc"] = proc
        start_t, last_size, stall_count = time.time(), 0, 0

        while proc.returncode is None:
            if ACTIVE_TASKS.get(task_id, {}).get("cancelled"):
                proc.kill(); safe_file_cleanup(output_file); return

            # Watchdog: Restart if stalled
            if os.path.exists(output_file):
                curr = os.path.getsize(output_file)
                if curr > 0 and curr == last_size: stall_count += 1
                else: stall_count = 0
                last_size = curr
                if stall_count >= 12: # 48 seconds stall
                    proc.kill(); await asyncio.sleep(2)
                    proc = await asyncio.create_subprocess_shell(shell_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    ACTIVE_TASKS[task_id]["proc"] = proc; stall_count = 0

            await asyncio.sleep(4)
            gc.collect() # Force free memory every loop
        
        await proc.wait()
        if os.path.exists(output_file) and os.path.getsize(output_file) > 10000:
            await client.send_video(chat_id, video=output_file, caption=f"✅ Recording Complete")
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"⚠️ **Error:** {str(e)}")
    finally:
        safe_file_cleanup(output_file)
        if task_id in ACTIVE_TASKS: del ACTIVE_TASKS[task_id]

# --- HANDLERS (Same logic as before) ---
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    if not is_authorized(message.from_user.id):
        await message.reply_text("🚫 **Access Denied.** Contact Owner.")
        return
    await message.reply_text("✨ Bot Online! Use /rec or /schedule.")

@app.on_message(filters.command("rec"))
async def record_cmd(client, message):
    if not is_authorized(message.from_user.id): return
    # ... (Command logic) ...
    args = message.command[1:]
    stream_url = args[0] if len(args) > 1 else DEFAULT_STREAM
    time_sec = parse_time_to_seconds(args[-1])
    await execute_record_stream(client, message.chat.id, stream_url, time_sec, get_user_engine(message.from_user.id))

# --- WEB HEALTH CHECK ---
async def web_root(request): return web.Response(text="Bot Alive")
async def start_web_server():
    server = web.Application()
    server.router.add_get("/", web_root)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def main():
    await start_web_server()
    await app.start()
    print(f"✅ BOT LIVE | OWNER: {OWNER_ID}")
    await idle()
    await app.stop()

if __name__ == "__main__": app.run(main())
