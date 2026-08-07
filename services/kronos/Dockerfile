FROM python:3.12-slim

ARG KRONOS_REF=67b630e67f6a18c9e9be918d9b4337c960db1e9a
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git gosu \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/kronos \
    && git -C /opt/kronos remote add origin https://github.com/shiyu-coder/Kronos.git \
    && git -C /opt/kronos fetch --depth 1 origin "${KRONOS_REF}" \
    && git -C /opt/kronos checkout --detach FETCH_HEAD
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY entrypoint.sh /usr/local/bin/kronos-entrypoint

ENV PYTHONPATH=/opt/kronos
RUN useradd --create-home --uid 10001 foundation \
    && mkdir -p /models/kronos-huggingface \
    && chown -R foundation:foundation /app /models/kronos-huggingface \
    && chmod 0755 /usr/local/bin/kronos-entrypoint

EXPOSE 8081
ENTRYPOINT ["kronos-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
