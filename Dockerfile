FROM python:3.12-slim

# System dependencies:
# - ffmpeg: required by the `av` package / video conversion pipeline
# - libgl1, libglib2.0-0: required by opencv-python (cv2) at import time
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data: converted videos and in-progress job state.
# Mount a volume here in Coolify so videos survive container restarts/updates.
VOLUME ["/app/videos", "/app/jobs"]

EXPOSE 4334

CMD ["python3", "server.py"]