FROM python:3.11-slim

# Cài tesseract OCR và các thư viện hệ thống cần thiết
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-vie \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cài Python packages trước (tận dụng Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ source code
COPY . .

# Tạo sẵn các thư mục cần thiết
RUN mkdir -p uploads/avatars database data

# Render sẽ tự set biến PORT, mặc định 10000
EXPOSE 10000

CMD sh -c "gunicorn --chdir backend app:app --bind 0.0.0.0:${PORT:-10000} --workers 2 --timeout 300 --graceful-timeout 30"
