from telethon import TelegramClient, events
from datetime import datetime, timezone, timedelta
import socketio
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import uvicorn
import os
from dotenv import load_dotenv
import logging
import asyncio

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
phone = os.getenv("PHONE_NUMBER")

client = TelegramClient('telegram', api_id, api_hash)

sio = socketio.AsyncServer(async_mode='asgi')

async def get_message(request):
    channel = request.query_params.get('channel')
    if not channel:
        return JSONResponse({"error": "Channel is required"}, status_code=400)
    time_interval_minutes = int(request.query_params.get('time_interval_minutes', 10))
    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_minutes)

    old_messages = []
    if not client.is_connected():
        await client.connect()
    async for message in client.iter_messages(channel):
        if message.date >= time_threshold:
            if not message.media:
                old_messages.append({
                    'text': message.text,
                    'date': message.date.isoformat(),
                })
        else:
            break
    return JSONResponse(old_messages)

async def startup():
    try:
        logger.info("Bắt đầu kết nối TelegramClient...")
        if not client.is_connected():
            await client.connect()

        # Hàm callback để nhập OTP hoặc mật khẩu
        async def input_code():
            logger.info("Vui lòng nhập mã OTP được gửi đến số điện thoại %s:", phone)
            return input("Mã OTP: ")

        async def input_password():
            logger.info("Vui lòng nhập mật khẩu xác minh hai bước:")
            return input("Mật khẩu: ")

        # Đăng nhập với callback cho OTP và mật khẩu
        await asyncio.wait_for(
            client.start(
                phone=phone,
                code_callback=input_code,
                password=input_password
            ),
            timeout=60
        )
        logger.info("Đăng nhập Telegram thành công!")
    except asyncio.TimeoutError:
        logger.error("Timeout khi đăng nhập Telegram! Vui lòng kiểm tra lại.")
        raise
    except Exception as e:
        logger.error(f"Lỗi khi khởi động TelegramClient: {str(e)}")
        raise

routes = [
    Route("/api/get-messages", get_message, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup])
app.mount("/", socketio.ASGIApp(sio))

connected_clients = {}

@client.on(events.NewMessage())
async def handler(event):
    channel = event.chat.username
    if channel in connected_clients:
        for sid in connected_clients[channel]:
            await sio.emit('newMessage', {
                'text': event.message.text,
                'date': event.message.date.isoformat(),
            }, room=sid)

@sio.event
async def connect(sid, environ):
    logger.info(f"Client {sid} connected")

@sio.event
async def getMessage(sid, data):
    channel = data['channel']
    time_interval_minutes = data['time_interval_minutes']
    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_minutes)

    old_messages = []
    if not client.is_connected():
        await client.connect()
    async for message in client.iter_messages(channel):
        if message.date >= time_threshold:
            if not message.media:
                old_messages.append({
                    'text': message.text,
                    'date': message.date.isoformat(),
                    'from': message.from_id,
                })
        else:
            break

    await sio.emit('oldMessages', old_messages, room=sid)

    if channel not in connected_clients:
        connected_clients[channel] = []
    connected_clients[channel].append(sid)

@sio.event
async def disconnect(sid):
    for channel in connected_clients:
        if sid in connected_clients[channel]:
            connected_clients[channel].remove(sid)
    logger.info(f"Client {sid} disconnected")

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)