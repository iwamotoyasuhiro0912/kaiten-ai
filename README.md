# 🍣 Kaiten AI Gateway

**AIのAPIキーを日替わりローテーションするゲートウェイサーバー**

```
クライアント → Kaiten AI → Groq / Gemini / OpenAI / Anthropic / ...
```

## 特徴

- 🔄 **日替わりローテーション** — 同じプロバイダーのキーを毎日JST 0:00に自動切り替え
- 🌐 **マルチプロバイダー** — Groq, Gemini, OpenAI, Anthropic, Mistral, Cerebras, DeepSeek, OpenRouter 他
- 🗂️ **Web管理画面** — `/admin` でエクセル風にキーを追加・削除・有効化
- ⚡ **OpenAI互換** — LiteLLMベースで100+モデルに対応
- 🔒 **Basic認証対応** — ADMIN_TOKENで管理画面・APIを保護
- 🐳 **Docker対応** — Oracleサーバーに一発デプロイ

## クイックスタート

```bash
# 1. クローン
git clone https://github.com/iwamotoyasuhiro0912/kaiten-ai.git
cd kaiten-ai

# 2. 環境変数設定
cp .env.example .env
# .envを編集（ADMIN_TOKENは必ず設定！）

# 3. Docker起動
docker compose up -d
```

**管理画面:** `http://localhost:8300/admin`
（nginx + ROOT_PATH使用時: `http://your-host/kaiten-ai/admin`）

## セキュリティ設定

`.env` で認証を有効化してください。

```env
ADMIN_USER=admin
ADMIN_TOKEN=your_strong_password_here
```

`ADMIN_TOKEN` が設定されていると、以下のエンドポイントにBasic認証がかかります：
- `/admin`（Web管理画面）
- `/api/keys` `/api/rotation` `/api/log`（API）

> ⚠️ **外部公開する場合は必ずADMIN_TOKENを設定してください。**

## APIの使い方

### チャット送信

```bash
curl -X POST http://localhost:8300/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "groq",
    "messages": [{"role": "user", "content": "こんにちは！"}]
  }'
```

### モデルを指定する場合

```bash
# プロバイダー/モデル名 の形式
"model": "groq/llama-3.3-70b-versatile"
"model": "gemini/gemini-2.0-flash"
"model": "openai/gpt-4o-mini"
```

### 対応プロバイダー一覧

| プロバイダー | model値 | デフォルトモデル |
|------------|--------|--------------|
| Groq | `groq` | llama-3.3-70b-versatile |
| Gemini | `gemini` | gemini-2.0-flash |
| OpenAI | `openai` | gpt-4o-mini |
| Anthropic | `anthropic` | claude-3-5-haiku-20241022 |
| Mistral | `mistral` | mistral-large-latest |
| Cerebras | `cerebras` | llama3.1-8b |
| OpenRouter | `openrouter` | auto |
| DeepSeek | `deepseek` | deepseek-chat |
| Perplexity | `perplexity` | sonar-small |
| Together AI | `together` | Llama-3.3-70B |

## APIキー管理

### Web画面（推奨）
`http://localhost:8300/admin` にアクセス

### API経由

```bash
# キー一覧（要認証）
GET /api/keys

# キー追加（要認証）
POST /api/keys
{"provider": "groq", "api_key": "gsk_xxx", "label": "アカウント名"}

# キー削除（要認証）
DELETE /api/keys/{id}

# 有効/無効切り替え（要認証）
PATCH /api/keys/{id}
{"is_active": false}
```

## ローテーション仕組み

```
キーA、キーB、キーC が登録されている場合（Groq）

1日目 → キーA を使用
2日目 → キーB を使用
3日目 → キーC を使用
4日目 → キーA に戻る（繰り返し）
```

切り替えタイミング：**毎日 JST 00:00:00**

## 環境変数でキーを初期設定

```env
# 起動時に自動でDBにインポートされる
GROQ_API_KEY=gsk_xxx
GEMINI_API_KEY=AIzaSy_xxx
```

詳細は `.env.example` を参照。

## nginx プレフィックス構成

nginx でサブパス（`/kaiten-ai/`）に配置する場合は `.env` に追記：

```env
ROOT_PATH=/kaiten-ai
```

`nginx.conf` のサンプルはリポジトリの `nginx.conf` を参照。

## エンドポイント一覧

> ROOT_PATH=/kaiten-ai 設定時はパスの先頭に `/kaiten-ai` が付きます。

| メソッド | パス | 認証 | 説明 |
|--------|------|:---:|------|
| GET | `/` | ❌ | ステータス確認 |
| GET | `/health` | ❌ | プロバイダー別キー状況 |
| POST | `/v1/chat` | ❌ | チャット送信（メインAPI） |
| GET | `/api/keys` | ✅ | キー一覧 |
| POST | `/api/keys` | ✅ | キー追加 |
| PATCH | `/api/keys/{id}` | ✅ | キー更新 |
| DELETE | `/api/keys/{id}` | ✅ | キー削除 |
| GET | `/api/rotation` | ✅ | ローテーション状況 |
| GET | `/api/log` | ✅ | リクエストログ |
| GET | `/admin` | ✅ | Web管理画面 |
