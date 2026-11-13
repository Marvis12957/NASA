# Flame in Freefall — NASA Space Apps 2026

Workspace của dự án **Flame in Freefall**: phân tích dữ liệu đốt cháy vi trọng lực
từ kho PSI của NASA (BASS/BASS-II, Saffire, FLEX, SAME, ACME...) phục vụ an toàn
cháy cho ISS, căn cứ Mặt Trăng/Sao Hỏa và rover có điều áp.

## Cấu trúc

- `flame_dashboard_*.webp` — 6 ảnh concept giao diện dashboard (Evidence Atlas,
  Scenario Center, Flame Lab, Investigation Finder, Dossier & Compare,
  Next-Gen Boundaries).
- `data/profiles.json` — metadata các investigation PSI.
- `data/psi/README.md` — mô tả bộ dữ liệu PSI.
- `tailscale/linux/` — cấu hình container Linux + Tailscale/SSH để truy cập máy này
  (file `.env` thật đã được loại khỏi git bằng `.gitignore`).

## Dữ liệu

Thư mục `data/psi/` (~2,6 GB tài liệu PSI gốc tải từ NASA) **không** nằm trong git
— xem quy tắc trong `.gitignore`. Khi cần, tải lại từ PSI (psi.nasa.gov) vào đúng
cấu trúc thư mục cũ.
