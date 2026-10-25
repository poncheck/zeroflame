FROM python:3.12-slim

# Playwright's Firefox needs these system libs even on slim Debian.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates \
    libgtk-3-0 libdbus-glib-1-2 libx11-xcb1 libxt6 libasound2 \
    libnss3 libnspr4 libxss1 libxrandr2 fonts-liberation \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
  && playwright install --with-deps firefox

COPY common/ ./common/
COPY scripts/ ./scripts/
COPY webapp/ ./webapp/
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x ./docker/entrypoint.sh

ENV ZEROFLAME_DATA_DIR=/app/data
VOLUME ["/app/data"]

EXPOSE 8088

ENTRYPOINT ["./docker/entrypoint.sh"]
