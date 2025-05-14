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
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# Thiết lập logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tải biến môi trường
load_dotenv()
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
phone = os.getenv("PHONE_NUMBER")

# Khởi tạo TelegramClient
client = TelegramClient('telegram', api_id, api_hash)

# Tạo Socket.IO ASGI server
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins="*",
    allow_credentials=False,   # Tắt yêu cầu credentials
    logger=True,  # Bật log chi tiết cho Socket.IO
    engineio_logger=True  # Bật log cho engine.io
)

# Hàm xử lý API HTTP GET để lấy tin nhắn
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
    return JSONResponse(content=old_messages, headers={"Content-Disposition": "attachment"})

# Hàm khởi động ứng dụng
async def startup():
    try:
        logger.info("Bắt đầu kết nối TelegramClient...")
        if not client.is_connected():
            await client.connect()

        async def input_code():
            logger.info("Vui lòng nhập mã OTP được gửi đến số điện thoại %s:", phone)
            return input("Mã OTP: ")

        async def input_password():
            logger.info("Vui lòng nhập mật khẩu xác minh hai bước:")
            return input("Mật khẩu: ")

        await asyncio.wait_for(
            client.start(
                phone=phone,
                code_callback=input_code,
                password=input_password
            ),
            timeout=60 * 30
        )
        logger.info("Đăng nhập Telegram thành công!")
    except asyncio.TimeoutError:
        logger.error("Timeout khi đăng nhập Telegram! Vui lòng kiểm tra lại.")
        raise
    except Exception as e:
        logger.error(f"Lỗi khi khởi động TelegramClient: {str(e)}")
        raise

# Định nghĩa routes cho ứng dụng Starlette
http_app = Starlette(routes=[Route("/api/get-messages", get_message, methods=["GET"])], on_startup=[startup])

# Tạo Socket.IO ASGI app với http_app làm ứng dụng dự phòng
sio_asgi_app = socketio.ASGIApp(sio, other_asgi_app=http_app)

# Ứng dụng chính là sio_asgi_app
app = sio_asgi_app
class LogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        logger.info(f"Request: {request.method} {request.url} Headers: {request.headers}")
        response = await call_next(request)
        logger.info(f"Response: {response.status_code}")
        return response

# Thêm middleware vào http_app
http_app.add_middleware(LogMiddleware)
# Thêm middleware CORSMiddleware vào http_app
http_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Danh sách các client đã kết nối
connected_clients = {}

# Xử lý tin nhắn mới từ Telegram
@client.on(events.NewMessage())
async def handler(event):
    channel = event.chat.username
    if channel in connected_clients:
        for sid in connected_clients[channel]:
            await sio.emit('newMessage', {
                'text': event.message.text,
                'date': event.message.date.isoformat(),
            }, room=sid)

# Sự kiện Socket.IO
@sio.event
async def connect(sid, environ):
    logger.info(f"Client {sid} connected")
    await sio.emit('connection', {'status': 'connected'}, room=sid)

@sio.event
async def getMessage(sid, data):
    channel = data.get('channel')
    time_interval_minutes = data.get('time_interval_minutes', 10)
    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_seconds)

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
    for channel in list(connected_clients.keys()):
        if sid in connected_clients[channel]:
            connected_clients[channel].remove(sid)
            if not connected_clients[channel]:
                del connected_clients[channel]
    logger.info(f"Client {sid} disconnected")

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)