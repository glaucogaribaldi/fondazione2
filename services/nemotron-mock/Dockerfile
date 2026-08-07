FROM python:3.12-slim
WORKDIR /app
COPY server.py .
RUN useradd --create-home --uid 10001 foundation
USER foundation
EXPOSE 30000
CMD ["python", "server.py"]
