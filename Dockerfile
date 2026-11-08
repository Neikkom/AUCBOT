FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Keep the database on a mounted volume so it survives redeploys.
VOLUME ["/app/data"]
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "python -m auction_agent serve --buybox buybox.yaml --sources sources.yaml --every ${POLL_MINUTES:-15} --port ${PORT}"]
