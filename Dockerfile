FROM node:22-alpine AS web
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY colab/ ./colab/
COPY workflows/ ./workflows/
COPY LICENSE ./
COPY --from=web /build/dist ./web/dist
ENV DRAWING_DATA=/data PYTHONUNBUFFERED=1
EXPOSE 8790
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8790", "--proxy-headers"]
