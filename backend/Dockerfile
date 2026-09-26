FROM python:3.11-slim

WORKDIR /app

COPY requirements/base.txt /tmp/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    libgl1 \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.14.0+cpu torchvision==0.29.0+cpu && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir gunicorn==23.0.0

COPY . /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
