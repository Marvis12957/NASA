# Clerk Auth (Google sign-in) — trạng thái & cách bật thật

Trạng thái hiện tại: **đã tích hợp sẵn, đang TẮT** (chưa có Clerk key nào).
Khi tắt, app chạy hoàn toàn bình thường và **link demo công khai vào thẳng
được, không bị khoá sau đăng nhập** — nút "Đăng nhập" trên thanh trên chỉ
hiện một thông báo gọn cho biết auth chưa cấu hình.

## Đã tích hợp những gì

- **Frontend** (`@clerk/clerk-react`): `ClerkProvider` chỉ được mount khi có
  key; nút Đăng nhập/Đăng ký mở modal Clerk, sau đăng nhập hiện `UserButton`.
  Mã: `frontend/src/lib/auth.jsx`, `frontend/src/main.jsx`.
- **Backend** (FastAPI): `backend/app/auth.py` xác thực JWT phiên Clerk qua
  JWKS (kiểm chữ ký RS256 + issuer + hạn token). Endpoint chứng minh:
  `GET /api/me` kèm header `Authorization: Bearer <session token>`.

## Các bước bật thật (khoảng 5 phút)

1. Vào https://clerk.com → **Create application** → đặt tên (vd "Flame").
2. Trong Clerk Dashboard → **User & authentication → Social connections** →
   bật **Google** (đăng nhập bằng Google sẽ là lựa chọn nổi bật trong modal).
3. Vào **API Keys** → chép **Publishable key** (`pk_...`) và **Secret key**
   (`sk_...`). Secret key chỉ để ở server, **không bao giờ commit**.
4. **Frontend:** tạo file `frontend/.env` với một dòng
   `VITE_CLERK_PUBLISHABLE_KEY=pk_...` rồi build lại frontend.
5. **Backend:** thêm vào file `.env` ở thư mục gốc repo:
   - `CLERK_ISSUER=https://<instance>.clerk.accounts.dev` (địa chỉ instance,
     xem tại API Keys → "Frontend API URL" / issuer trong JWT template)
   - `CLERK_SECRET_KEY=sk_...` (chỉ dành cho các lời gọi Clerk Backend API
     sau này; việc xác thực JWT qua JWKS không cần nó)
   rồi khởi động lại API.
6. Kiểm tra: mở app → nút Đăng nhập mở modal Clerk → đăng nhập Google →
   gọi `GET /api/me` kèm token phiên sẽ trả thông tin user. Chưa cấu hình
   thì `/api/me` trả `{"authEnabled": false, "user": null}` (HTTP 200);
   đã cấu hình mà thiếu/sai token thì 401.

## Nguyên tắc an toàn

- Không hardcode key trong code; key chỉ đi qua biến môi trường / file `.env`
  (đã gitignore). File mẫu: `.env.example` (gốc repo) và
  `frontend/.env.example`.
- Mọi endpoint hiện tại vẫn công khai; auth hiện chỉ là lớp tuỳ chọn phía
  trên. Muốn bảo vệ thêm endpoint nào trong tương lai, gắn dependency
  `require_user` của `backend/app/auth.py` vào endpoint đó.
