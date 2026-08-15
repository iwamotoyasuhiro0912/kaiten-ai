FROM python:3.11-slim

WORKDIR /app

# 依存インストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコピー
COPY main.py .
COPY static/ static/

# データ永続化ディレクトリ
RUN mkdir -p data

EXPOSE 8300

CMD ["python", "main.py"]
