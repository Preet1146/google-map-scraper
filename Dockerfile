FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium
COPY scraper.py dashboard.py ./
COPY data ./data
COPY web ./web
CMD ["python", "-u", "dashboard.py"]
