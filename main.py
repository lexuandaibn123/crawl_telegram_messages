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

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
phone = os.getenv("PHONE_NUMBER")

client = TelegramClient('telegram', api_id, api_hash)

app = FastAPI()
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients = {}

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
    return JSONResponse(content=old_messages)

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
                            sender_id_val = None
                            if message.sender_id:
                                sender_id_val = message.sender_id

                            old_messages.append({
                                'text': message.text,
                                'date': message.date.isoformat(),
                                'from': sender_id_val,
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
    logger.info("-----------------------------------------------------")
    logger.info(f"[HANDLER] New message event received!")
    logger.info(f"[HANDLER] Chat Entity: {event.chat}")
    logger.info(f"[HANDLER] Chat ID: {event.chat_id}")

    chat_username_from_event = None
    if hasattr(event.chat, 'username') and event.chat.username:
        chat_username_from_event = event.chat.username
        logger.info(f"[HANDLER] Event's chat username: '{chat_username_from_event}' (type: {type(chat_username_from_event)})")
    else:
        logger.warning(f"[HANDLER] Event's chat does NOT have a username. Chat title: {getattr(event.chat, 'title', 'N/A')}")

    logger.info(f"[HANDLER] Current connected_clients keys: {list(connected_clients.keys())}")

    target_channel_key = chat_username_from_event 

    if target_channel_key and target_channel_key in connected_clients:
        logger.info(f"[HANDLER] Match found! Message from '{target_channel_key}' will be sent to {len(connected_clients[target_channel_key])} WebSocket client(s).")

        sender_id_for_new_message = None
        if event.message.sender_id: # Lấy sender_id cho tin nhắn mới
            sender_id_for_new_message = event.message.sender_id

        message_content = {
            'text': event.message.text,
            'date': event.message.date.isoformat(),
            'from_id': sender_id_for_new_message, # Thêm from_id vào đây
        }
        logger.debug(f"[HANDLER] Message content to send: {message_content}")

        clients_to_send = list(connected_clients[target_channel_key])
        for ws_client in clients_to_send:
            try:
                await ws_client.send_json(message_content)
                logger.info(f"[HANDLER] Successfully sent message to a WebSocket client for channel '{target_channel_key}'.")
            except Exception as e:
                logger.error(f"[HANDLER] Error sending message to a WebSocket client for channel '{target_channel_key}': {e}")
                if ws_client in connected_clients[target_channel_key]:
                    connected_clients[target_channel_key].remove(ws_client)
                    if not connected_clients[target_channel_key]:
                        del connected_clients[target_channel_key]
                        logger.info(f"[HANDLER] Removed empty client list and key for channel '{target_channel_key}'.")
    elif not target_channel_key:
        logger.warning(f"[HANDLER] No username in event. Cannot route message from chat ID {event.chat_id} based on username.")
    else:
        logger.warning(f"[HANDLER] Message from channel '{target_channel_key}', but no WebSocket clients are currently listening to this exact channel name.")
    logger.info("-----------------------------------------------------")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)