# Dockerfile

# 1. Chọn Python base image
FROM python:3.10-slim

# 2. Tạo thư mục làm việc trong container
WORKDIR /app

# 3. Copy file requirements và cài các dependency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy toàn bộ code (bao gồm main.py, .env.example nếu có, v.v.)
COPY . .

# 5. Expose port (nếu bạn chạy API trên 8000)
EXPOSE 8000

# 6. Khởi động Uvicorn khi container chạy
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]