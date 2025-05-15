const WebSocket = require("ws"); // Import thư viện ws
const channel = "gem_tools_calls"; // Channel cố định, thay đổi nếu cần

function connectWebSocket() {
  const ws = new WebSocket(`wss://telegram.seedlabs.digital/ws/${channel}`);
  // const ws = new WebSocket(`ws://localhost:8000/ws/${channel}`);

  ws.on("open", () => {
    console.log("Connected to server");
    ws.send("getMessages"); // Gửi yêu cầu lấy tin nhắn cũ
  });

  ws.on("message", (data) => {
    try {
      // Dữ liệu nhận được từ ws là Buffer, cần chuyển thành string và parse JSON
      const parsedData = JSON.parse(data.toString());
      if (parsedData.oldMessages) {
        console.log("Old messages:", parsedData.oldMessages);
      } else if (parsedData.text && parsedData.date) {
        console.log("New message:", parsedData.text, parsedData.date);
      } else {
        console.log("Unknown message format:", parsedData);
      }
    } catch (error) {
      console.error("Error parsing message:", error);
    }
  });

  ws.on("close", () => {
    console.log("Disconnected from server. Reconnecting in 5 seconds...");
    setTimeout(connectWebSocket, 5000); // Thử lại sau 5 giây
  });

  ws.on("error", (error) => {
    console.log("Connection error:", error.message);
    ws.close(); // Đóng kết nối để kích hoạt sự kiện close
  });

  return ws;
}

// Khởi tạo kết nối
let ws = connectWebSocket();
