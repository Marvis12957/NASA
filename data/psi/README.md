# PSI downloads

Web PSI gộp mọi file vào một chỗ và mất cây thư mục. Tên file vẫn giữ loại tài liệu, ví dụ:

`PSI-102_Reports_SAME_Final Science Report.pdf`

## Cách dùng

1. Tải từ trang investigation, bỏ **Raw Data**.
2. Thả toàn bộ file vừa tải vào `_inbox`.
3. Chạy:

```bash
python3 scripts/sort_psi_downloads.py
```

Script sẽ chuyển file vào đúng investigation và đúng mục.

## Tick trên web

- Reports
- Engineering Documents
- Science Documents
- Presentations (PDF/PPT đầy đủ; MP4 chỉ clip ngắn)
- Investigation Metadata Files
- Experimental table / Experiment Table
- Analyzed Data (số liệu đã xử lý; nếu thư mục này cũng hàng chục GB thì chỉ lấy csv/xlsx)
- Reference Data (tải; cũng bỏ nếu toàn file raw/ảnh khổng lồ)
- Calibration: bỏ nếu nặng (thường giống Raw Data)

Không tick Raw Data. PSI-181 (mối hàn) không có thư mục vì không dùng cho challenge.
