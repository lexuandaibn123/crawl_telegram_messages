from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from telethon import TelegramClient, events
from datetime import datetime, timezone, timedelta
import logging
import asyncio
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# Thiết lập logging ở mức DEBUG để có thêm chi tiết
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Tải biến môi trường
load_dotenv()
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
phone = os.getenv("PHONE_NUMBER")

# Khởi tạo TelegramClient
client = TelegramClient('telegram', api_id, api_hash)

# Khởi tạo FastAPI app
app = FastAPI()
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
# Thêm middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Danh sách client WebSocket theo channel
connected_clients = {}

# Hàm khởi động ứng dụng
@app.on_event("startup")
async def startup_event():
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

# API HTTP GET để lấy tin nhắn
@app.get("/api/get-messages")
async def get_message(channel: str = Query(...), time_interval_minutes: int = Query(10)):
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

# WebSocket endpoint cho các client
@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str):
    await websocket.accept()
    if channel not in connected_clients:
        connected_clients[channel] = []
    connected_clients[channel].append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Xử lý dữ liệu từ client nếu cần
            if data == "getMessages":
                time_interval_minutes = 10  # Mặc định
                time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_minutes)
                old_messages = []
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
                await websocket.send_json({'oldMessages': old_messages})
    except WebSocketDisconnect:
        connected_clients[channel].remove(websocket)
        if not connected_clients[channel]:
            del connected_clients[channel]
        logger.info(f"Client disconnected from channel {channel}")

# Xử lý tin nhắn mới từ Telegram
@client.on(events.NewMessage())
async def handler(event):
    channel = event.chat.username
    if channel in connected_clients:
        for ws in connected_clients[channel]:
            await ws.send_json({
                'text': event.message.text,
                'date': event.message.date.isoformat(),
            })

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)