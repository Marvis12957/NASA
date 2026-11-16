# Flame in Freefall

Flame in Freefall là dự án về nghiên cứu cháy trong môi trường vi trọng lực của NASA. Backend FastAPI xếp hạng trên đồ thị và tìm chunk bằng MiniLM trong Neo4j. Frontend là ứng dụng Vite và React.

## Bố cục repo

- `backend/` — API FastAPI (`backend/app/main.py`) và `backend/requirements.txt`
- `frontend/` — giao diện Vite/React
- `data/` — tài liệu và chunk trên máy local
- `docs/` — ghi chú trong repo (auth, luồng agent, setup)
- `tools/` — CLI `agentctl`

## Chạy local

Ở gốc repo, copy file mẫu rồi điền giá trị trên máy của bạn:

```bash
cp .env.example .env
```

Cài dependency của backend. `backend/requirements.txt` có `sentence-transformers`; bản chạy local cần gói này.

```bash
python -m pip install -r backend/requirements.txt
```

Chạy API từ thư mục `backend`. Cổng 8000 trùng với proxy của Vite.

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Trong thư mục `frontend/`:

```bash
npm install
npm run dev
```

Script `dev` chạy `vite`. `frontend/vite.config.js` chuyển `/api` và `/health` tới `http://127.0.0.1:8000` cho cả `npm run dev` và `npm run preview`. Lệnh `preview` mở cổng 5173.

## Biến môi trường

Chỉ ghi tên ở đây. Giá trị nằm trong `.env`. Không commit `.env` (file này đã có trong `.gitignore`).

- Neo4j: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- Anthropic: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`
- Cloudinary: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`
- Clerk: `CLERK_ISSUER`, `CLERK_JWKS_URL`, `CLERK_SECRET_KEY`. Frontend đọc `VITE_CLERK_PUBLISHABLE_KEY` từ `frontend/.env`. Auth tắt khi issuer và JWKS đều trống.
- Database: `DIRECT_URL`, `DATABASE_URL`

## Dữ liệu

Phần lớn `data/` bị gitignore vì dung lượng lớn. File chunk local cho BM25 nằm ở `data/chunks/children.jsonl`.

## Deploy

API là process FastAPI chạy lâu bằng uvicorn. Vercel Python không cài được `backend/requirements.txt` vì `sentence-transformers` kéo `torch` (khoảng 5.6 GB, vượt hạn function 500 MB).
