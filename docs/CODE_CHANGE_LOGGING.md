# CODE_CHANGE_LOGGING — Mọi thay đổi source code đều ghi log

> Cấu hình cứng ngày 14/11/2026 theo yêu cầu của Young. Áp dụng cho repo `/workspace` trên nasa-linux.

## Log ở đâu

Một file duy nhất, chỉ ghi nối tiếp (append):

```
/var/log/agent-platform/code-changes.jsonl
```

Mỗi dòng là 1 JSON: `ts` (UTC), `event`, `host`, `os_user`, `agent_id` (env AGENT_ID nếu có),
`ssh_connection`, `repo`, `branch`, + chi tiết theo loại event.

## Ba lớp ghi log (chồng nhau, lớp này bắt được lớp kia sót)

| Lớp | Cơ chế | Bắt được gì |
|-----|--------|-------------|
| 1 | Hook `post-commit` (trong `hooks/`, ghim bằng `core.hooksPath`) | Mọi commit: hash, tác giả, tiêu đề, danh sách file + diffstat |
| 2 | Hook `pre-push` (đã có sẵn luật chặn push main/force-push, nay ghi log trước khi xét) | Mọi lần push, kể cả lần bị chặn |
| 3 | Watcher `tools/code-watch.sh` (vòng 15 giây, tự khởi động lại qua `/etc/profile.d/`) | Thay đổi **chưa commit** trong vùng source: `backend/ hooks/ tools/ scripts/ tests/ docs/ .github/` + file gốc của repo (trừ `data/` là dữ liệu thô) |

## Vì sao gọi là "cứng"

- `git config core.hooksPath /workspace/hooks` — hooks lấy từ thư mục `hooks/` **được version trong repo**, không phụ thuộc bản copy trong `.git/hooks` (vẫn giữ bản copy ở cả 2 nơi). Ai sửa hook cũng là thay đổi source → tự nó bị lớp 1 và 3 ghi lại.
- Watcher tự sống lại: `/etc/profile.d/agent-platform-codewatch.sh` kiểm tra PID mỗi lần có phiên đăng nhập SSH, chết là khởi động lại; trạng thái watcher để **ngoài** repo (`/var/log/agent-platform/.code-watch-state`) để không tự kích lẫn nhau.
- Log nằm ngoài repo, không bị `git clean`/checkout ghi đè.

## Giới hạn phải nói thẳng

- Mọi dev dùng chung user `coder` + sudo không mật khẩu, container không có systemd/auditd:
  log này **cứng với quy trình làm việc bình thường** (không commit/push/sửa file nào lọt qua được),
  nhưng **không chống được** người có sudo cố tình xóa log hoặc giết watcher rồi sửa trong khoảng trống giữa 2 vòng 15 giây trước khi watcher sống lại. Chống tới mức đó cần tài khoản Linux riêng từng dev + auditd trên máy host (việc phase 2 đã ghi trong `docs/DISCOVERY.md`).
- Sửa file ngoài vùng source (ví dụ `data/`) không ghi — đó là dữ liệu, không phải code.

## Xem log

```bash
tail -f /var/log/agent-platform/code-changes.jsonl
python3 -c "import json,sys; [print(json.loads(l)['ts'], json.loads(l)['event']) for l in open('/var/log/agent-platform/code-changes.jsonl')]"
```

## File cấu thành

- `tools/code_log.py` — bộ ghi JSON dùng chung cho hooks + watcher
- `tools/code-watch.sh` — watcher lớp 3
- `hooks/post-commit`, `hooks/pre-push` — hooks lớp 1 + 2 (bản copy đồng bộ trong `.git/hooks/`)
- `/etc/profile.d/agent-platform-codewatch.sh` — tự khởi động watcher
