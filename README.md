# KIO.ai — backend thật + chat widget

Bộ này thay thế trang tĩnh cũ (`index.html` chỉ có HTML/CSS) bằng một agent
thật chạy được: có backend Python (`server.py`), router chọn skill/tool
(`agent_router.py`), và một khung chat gắn thẳng vào giao diện gốc.

## Cấu trúc

```
.
├── server.py          # Backend Flask — API chat, scan workspace, tool
├── agent_router.py     # Router: chấm điểm từ khoá -> chọn skill/tool
├── index.html          # Giao diện gốc + khung chat (JS thuần, không framework)
├── requirements.txt
├── .env.example
└── README.md
```

## Chạy local

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# mở .env, điền ANTHROPIC_API_KEY (bắt buộc để có trả lời AI thật)
# điền GITHUB_TOKEN + AGENT_TOKEN nếu muốn dùng tool git/github/terminal

python server.py
```

Mở trình duyệt: **http://localhost:8000** — góc dưới bên phải có nút 💬,
bấm vào để chat với KIO.ai. Backend sẽ:

1. Chấm điểm task để chọn tối đa 4 skill (`agent_router.route`)
2. Suy ra bộ tool tương ứng
3. Quét workspace (`/api/scan`)
4. Gọi Claude (Anthropic API) với system prompt mô tả skill/tool đã chọn
5. Trả về câu trả lời + skill/tool đã dùng, hiển thị ngay dưới tin nhắn

Nếu chưa điền `ANTHROPIC_API_KEY`, agent vẫn chạy nhưng trả lời giả lập
(để bạn test router/UI trước khi gắn key thật).

## Deploy lên GitHub Pages + server riêng

GitHub Pages **chỉ phục vụ file tĩnh**, không chạy được Python. Vì vậy:

1. Deploy `server.py` lên một nơi chạy được Python (Render, Railway, Fly.io,
   VPS riêng, v.v.), lấy URL công khai, ví dụ `https://kio-api.example.com`.
2. Trong `index.html`, trước thẻ `<script>` chứa logic chat, thêm:
   ```html
   <script>window.KIO_API_BASE = "https://kio-api.example.com";</script>
   ```
3. Giữ `index.html` trên GitHub Pages như cũ (`zskbot.github.io/zskbot-kio-ai`),
   nó sẽ gọi API sang server thật ở bước 1.

## Về các tool "nhạy cảm" (git / github / http / terminal)

Đây là các tool có thể đọc/ghi hệ thống hoặc gọi ra ngoài, nên:

- Mặc định **bị chặn** nếu bạn chưa đặt `AGENT_TOKEN` trong `.env`.
- Khi đã đặt, client phải gửi header `X-Agent-Token: <giá trị AGENT_TOKEN>`
  thì mới gọi được `/api/tool/git`, `/api/tool/github`, `/api/tool/http`,
  `/api/tool/terminal`.
- Tool `terminal` chỉ chạy được các lệnh nằm trong **allowlist** cứng trong
  `server.py` (ví dụ `git status`, `ls`, `pip list`...) — không nhận lệnh
  tự do, để tránh biến trang web thành cửa thực thi lệnh (RCE) cho bất kỳ ai
  ghé trang.
- **Không** expose server này ra internet công khai mà không có xác thực
  và giới hạn CORS phù hợp cho môi trường của bạn.

## Mở rộng

- Thêm skill/tool mới: sửa `SKILLS` trong `agent_router.py`.
- Thêm tool thật: viết hàm `<ten>_tool()` trong `server.py` rồi nối vào
  route `POST /api/tool/<name>`.
- Muốn agent tự sửa file: mở rộng route `file-manager` trong `server.py`
  để hỗ trợ ghi file (hiện tại chỉ đọc, để an toàn mặc định).