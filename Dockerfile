# GeorgeChin Personal Trade — one image: Vue build + FastAPI
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=0 \
    PYTHONPATH=/app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY --from=frontend /fe/dist /app/frontend/dist
COPY RULES.md PROFILE.md AGENTS.md README.md /app/
COPY journal/TEMPLATE.md /app/journal/TEMPLATE.md

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
