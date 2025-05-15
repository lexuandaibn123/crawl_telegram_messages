from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from telethon import TelegramClient, events
from datetime import datetime, timezone, timedelta
import logging
import asyncio
import os
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import json

logging.basicConfig(level=logging.DEBUG) # DEBUG để xem log chi tiết từ Telethon và Uvicorn
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
    allow_origins=["*"], # Nên giới hạn trong production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_subscriptions = {} # Format: {websocket: {"subscribed_channels": set(), "subscribed_to_all": False}}
ALL_CHANNELS_WILDCARD = "*"

@app.on_event("startup")
async def startup_event():
    # ... (logic startup_event không đổi nhiều, đảm bảo client.connect() và client.start()) ...
    try:
        logger.info("Bắt đầu kết nối TelegramClient...")
        if not client.is_connected():
            await client.connect()
        # Không cần client.start() ở đây nếu session đã tồn tại và hợp lệ.
        # client.start() sẽ cố gắng đăng nhập, chỉ cần khi chưa có session hoặc session hết hạn.
        # Chúng ta sẽ dựa vào việc client.is_connected() và is_user_authorized()
        if not await client.is_user_authorized():
            logger.info("Client chưa được ủy quyền. Bắt đầu quá trình đăng nhập...")
            async def input_code():
                logger.info("Vui lòng nhập mã OTP được gửi đến số điện thoại %s:", phone)
                return input("Mã OTP: ")
            async def input_password():
                logger.info("Vui lòng nhập mật khẩu xác minh hai bước:")
                return input("Mật khẩu: ")
            await client.start(phone=phone, code_callback=input_code, password=input_password)
        logger.info("Đăng nhập Telegram thành công và client đã sẵn sàng!")
    except Exception as e:
        logger.error(f"Lỗi nghiêm trọng khi khởi động hoặc đăng nhập TelegramClient: {str(e)}")
        # Cân nhắc việc thoát ứng dụng nếu không thể kết nối Telegram
        raise # Ném lại lỗi để FastAPI biết startup thất bại

# API HTTP GET không thay đổi nhiều, chỉ đảm bảo client kết nối
@app.get("/api/get-messages")
async def get_message_http(channel: str = Query(...), time_interval_minutes: int = Query(10)):
    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_minutes)
    old_messages = []
    if not client.is_connected() or not await client.is_user_authorized():
        logger.error("HTTP GET: Telegram client không kết nối hoặc chưa được ủy quyền.")
        raise HTTPException(status_code=503, detail="Telegram service not available or not authorized.")
    
    try:
        async for message in client.iter_messages(channel, limit=200): # Giới hạn số lượng tin nhắn lấy về
            if message.date < time_threshold:
                break 
            if not message.media and message.text:
                sender_id_val = message.sender_id if message.sender_id else None
                old_messages.append({
                    'text': message.text,
                    'date': message.date.isoformat(),
                    'from_id': sender_id_val,
                })
    except ValueError as e:
        logger.error(f"HTTP GET: Lỗi khi lấy tin nhắn cho channel '{channel}': {e}")
        raise HTTPException(status_code=400, detail=f"Kênh không hợp lệ hoặc lỗi khi lấy tin nhắn: {channel}. Chi tiết: {str(e)}")
    except Exception as e:
        logger.error(f"HTTP GET: Lỗi không mong muốn cho channel '{channel}': {e}")
        raise HTTPException(status_code=500, detail="Lỗi máy chủ nội bộ khi lấy tin nhắn.")
    return JSONResponse(content=old_messages)


@app.websocket("/ws") # Sử dụng endpoint /ws chung
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = f"{websocket.client.host}:{websocket.client.port}"
    active_subscriptions[websocket] = {"subscribed_channels": set(), "subscribed_to_all": False}
    logger.info(f"Client {client_id} đã kết nối. Trạng thái đăng ký ban đầu được khởi tạo.")

    async def send_status_to_client(ws: WebSocket):
        if ws in active_subscriptions:
            status_payload = {
                "type": "subscription_status",
                "subscribed_to_all": active_subscriptions[ws]["subscribed_to_all"],
                "subscribed_channels": sorted(list(active_subscriptions[ws]["subscribed_channels"])) # Sắp xếp để dễ đọc
            }
            await ws.send_text(json.dumps(status_payload))
            logger.debug(f"Đã gửi trạng thái đăng ký cho client {client_id}: {status_payload}")

    await send_status_to_client(websocket) # Gửi trạng thái ban đầu

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                message_json = json.loads(raw_data)
                action = message_json.get("action")
                logger.info(f"Client {client_id} gửi: action='{action}', payload={message_json}")

                subscription_changed = False

                if action == "subscribe":
                    channels_payload = message_json.get("channels", [])
                    if not isinstance(channels_payload, list):
                        await websocket.send_text(json.dumps({"type": "error", "message": "Trường 'channels' phải là một danh sách."}))
                        continue

                    if ALL_CHANNELS_WILDCARD in channels_payload:
                        active_subscriptions[websocket]["subscribed_to_all"] = True
                        active_subscriptions[websocket]["subscribed_channels"].clear() # Khi đăng ký tất cả, xóa các kênh cụ thể
                        logger.info(f"Client {client_id} đã đăng ký TẤT CẢ các kênh.")
                    else:
                        # Nếu client đang đăng ký tất cả và giờ muốn đăng ký kênh cụ thể,
                        # thì tắt cờ đăng ký tất cả.
                        if active_subscriptions[websocket]["subscribed_to_all"]:
                            active_subscriptions[websocket]["subscribed_to_all"] = False
                            active_subscriptions[websocket]["subscribed_channels"].clear() # Bắt đầu lại danh sách kênh cụ thể
                        
                        valid_channels_to_add = {ch for ch in channels_payload if isinstance(ch, str) and ch}
                        active_subscriptions[websocket]["subscribed_channels"].update(valid_channels_to_add)
                        logger.info(f"Client {client_id} đã thêm đăng ký cho các kênh: {valid_channels_to_add}.")
                    subscription_changed = True

                elif action == "unsubscribe":
                    channels_payload = message_json.get("channels", [])
                    if not isinstance(channels_payload, list):
                        await websocket.send_text(json.dumps({"type": "error", "message": "Trường 'channels' phải là một danh sách."}))
                        continue

                    if ALL_CHANNELS_WILDCARD in channels_payload:
                        active_subscriptions[websocket]["subscribed_to_all"] = False
                        # Không xóa subscribed_channels ở đây, client có thể muốn giữ lại các kênh cụ thể
                        # nếu họ đã từng đăng ký. Họ sẽ không nhận gì nếu subscribed_channels cũng trống.
                        logger.info(f"Client {client_id} đã hủy đăng ký TẤT CẢ các kênh.")
                    else:
                        channels_to_remove = {ch for ch in channels_payload if isinstance(ch, str) and ch}
                        for ch_to_remove in channels_to_remove:
                            active_subscriptions[websocket]["subscribed_channels"].discard(ch_to_remove)
                        logger.info(f"Client {client_id} đã hủy đăng ký các kênh: {channels_to_remove}.")
                        
                        if active_subscriptions[websocket]["subscribed_to_all"] and channels_to_remove:
                             await websocket.send_text(json.dumps({
                                "type": "info", 
                                "message": f"Bạn vẫn đang đăng ký TẤT CẢ các kênh. Hủy đăng ký kênh cụ thể ({', '.join(channels_to_remove)}) sẽ không ngăn bạn nhận tin nhắn từ chúng cho đến khi bạn hủy đăng ký TẤT CẢ ('{ALL_CHANNELS_WILDCARD}')."
                            }))
                    subscription_changed = True
                
                elif action == "get_old_messages":
                    target_channel = message_json.get("channel")
                    if not target_channel or not isinstance(target_channel, str) or target_channel == ALL_CHANNELS_WILDCARD:
                        await websocket.send_text(json.dumps({"type": "error", "message": "Phải chỉ định kênh cụ thể (không phải wildcard) cho 'get_old_messages'."}))
                        continue
                    
                    # Kiểm tra quyền truy cập
                    client_subs_info = active_subscriptions.get(websocket)
                    is_authorized_for_channel = False
                    if client_subs_info:
                        if client_subs_info["subscribed_to_all"] or target_channel in client_subs_info["subscribed_channels"]:
                            is_authorized_for_channel = True
                    
                    if not is_authorized_for_channel:
                        await websocket.send_text(json.dumps({"type": "error", "message": f"Bạn chưa đăng ký kênh '{target_channel}'. Không thể lấy tin nhắn cũ."}))
                        continue

                    time_interval_minutes = message_json.get("time_interval_minutes", 10) # Mặc định 10 phút
                    # ... (logic lấy old_messages như trước, gửi với type: "old_message_batch") ...
                    # (Đã được viết ở phiên bản trước, có thể copy-paste và điều chỉnh lại)
                    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=time_interval_minutes)
                    old_messages_data = []
                    try:
                        async for message_obj in client.iter_messages(target_channel, limit=200):
                            if message_obj.date < time_threshold: break
                            if not message_obj.media and message_obj.text:
                                sender_id_val = message_obj.sender_id if message_obj.sender_id else None
                                old_messages_data.append({
                                    'text': message_obj.text,
                                    'date': message_obj.date.isoformat(),
                                    'from_id': sender_id_val,
                                })
                        await websocket.send_text(json.dumps({
                            "type": "old_message_batch",
                            "channel": target_channel, # Gửi kèm tên channel
                            "data": old_messages_data
                        }))
                        logger.info(f"Đã gửi tin nhắn cũ của kênh '{target_channel}' cho client {client_id}")
                    except ValueError as e: # Kênh không tồn tại hoặc không truy cập được
                        logger.error(f"Lỗi khi lấy tin nhắn cũ cho kênh '{target_channel}' (client {client_id}): {e}")
                        await websocket.send_text(json.dumps({"type": "error", "message": f"Không thể lấy tin nhắn cho kênh '{target_channel}': {str(e)}"}))
                    except Exception as e:
                        logger.error(f"Lỗi không mong muốn khi lấy tin nhắn cũ cho '{target_channel}': {e}")
                        await websocket.send_text(json.dumps({"type": "error", "message": "Lỗi máy chủ nội bộ khi lấy tin nhắn cũ."}))


                else:
                    logger.warning(f"Hành động không xác định từ client {client_id}: {action}")
                    await websocket.send_text(json.dumps({"type": "error", "message": f"Hành động không xác định: {action}"}))

                if subscription_changed:
                    await send_status_to_client(websocket) # Gửi lại trạng thái đăng ký nếu có thay đổi

            except json.JSONDecodeError:
                logger.error(f"JSON không hợp lệ từ client {client_id}: {raw_data}")
                await websocket.send_text(json.dumps({"type": "error", "message": "Định dạng JSON không hợp lệ."}))
            except Exception as e:
                logger.error(f"Lỗi khi xử lý tin nhắn từ client {client_id}: {e}", exc_info=True)
                await websocket.send_text(json.dumps({"type": "error", "message": "Lỗi khi xử lý yêu cầu của bạn."}))

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} đã ngắt kết nối.")
    except Exception as e:
        logger.error(f"Lỗi không mong muốn trong kết nối WebSocket của client {client_id}: {e}", exc_info=True)
    finally:
        if websocket in active_subscriptions:
            del active_subscriptions[websocket]
            logger.info(f"Đã xóa đăng ký cho client {client_id} vừa ngắt kết nối.")


@client.on(events.NewMessage())
async def handler(event):
    # Xác định username của channel (nếu có)
    event_channel_username = None
    if hasattr(event.chat, 'username') and event.chat.username:
        event_channel_username = event.chat.username
    
    # Nguồn tin nhắn: sử dụng username nếu có, nếu không thì dùng chat_id dạng string
    message_source_identifier = event_channel_username if event_channel_username else str(event.chat_id)

    if not event.text: # Chỉ xử lý tin nhắn có text
        logger.debug(f"[HANDLER] Bỏ qua tin nhắn không có nội dung text từ nguồn: {message_source_identifier}")
        return

    logger.info(f"[HANDLER] Tin nhắn mới từ nguồn '{message_source_identifier}': \"{event.message.text[:50]}...\"")

    sender_id_val = event.message.sender_id if event.message.sender_id else None
    
    payload_to_send = {
        "type": "new_message",
        "channel": message_source_identifier, # Luôn gửi kèm nguồn tin nhắn
        "data": {
            'text': event.message.text,
            'date': event.message.date.isoformat(),
            'from_id': sender_id_val,
        }
    }

    # Tạo bản sao của dict items để tránh lỗi runtime nếu dict bị thay đổi trong lúc lặp
    for ws, subs in list(active_subscriptions.items()):
        client_should_receive = False
        if subs["subscribed_to_all"]:
            client_should_receive = True
        elif event_channel_username and event_channel_username in subs["subscribed_channels"]:
            # Chỉ gửi nếu tin nhắn có username và username đó nằm trong danh sách đăng ký cụ thể
            client_should_receive = True

        if client_should_receive:
            try:
                await ws.send_text(json.dumps(payload_to_send))
                logger.info(f"[HANDLER] Đã gửi tin nhắn từ '{message_source_identifier}' đến client {ws.client.host}:{ws.client.port}")
            except Exception as e:
                logger.error(f"[HANDLER] Lỗi khi gửi tin nhắn đến client {ws.client.host}:{ws.client.port}: {e}. Xóa client này.")
                # Nếu gửi lỗi, có thể client đã ngắt kết nối không đúng cách, xóa khỏi active_subscriptions
                if ws in active_subscriptions:
                    del active_subscriptions[ws]
    logger.info("-----------------------------------------------------")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000, log_level="info") # Có thể đặt log_level cho uvicorn