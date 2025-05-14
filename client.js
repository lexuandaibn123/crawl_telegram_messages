const io = require("socket.io-client");
const socket = io("https://telegram.seedlabs.digital", {
  transports: ["websocket"],
  path: "/socket.io/",
  reconnection: true,
  reconnectionAttempts: 5,
  reconnectionDelay: 1000,
});
socket.on("connect", () => {
  console.log("Connected to server");
  // Gửi event getMessage khi kết nối
  socket.emit("getMessage", { channel: "hehe478", time_interval_minutes: 60 });
});

socket.on("oldMessages", (data) => {
  console.log("Old message:", data);
});

socket.on("newMessage", (data) => {
  console.log("New message:", data);
});

socket.on("disconnect", () => {
  console.log("Disconnected from server");
});
