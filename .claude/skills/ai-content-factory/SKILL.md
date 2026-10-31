---
name: ai-content-factory
description: Xây và vận hành AI Content Factory — hệ thống biến một chủ đề thành video dọc ngắn (TikTok / YouTube Shorts / Facebook Reels) tiếng Việt, có bước người duyệt bắt buộc trước khi xuất. Dùng skill này mỗi khi làm việc trong repo AI-content-factory: triển khai Week 1–4 theo BACKLOG.md, viết stage/agent/provider mới, sửa pipeline render FFmpeg hay phụ đề tiếng Việt, chọn model cho từng stage, giao việc cho subagent, hay khi hỏi "làm bước tiếp theo", "làm item 1.x", "chạy Week 2" — kể cả khi không nhắc tên tài liệu nào.
---

# AI Content Factory

Pipeline biến một chủ đề hoặc mô tả sản phẩm thành video dọc ngắn, thị trường Việt Nam,
ngách AI & Công nghệ. Mô hình kinh doanh: kênh riêng, dịch vụ sản xuất nội dung cho doanh
nghiệp, và SaaS về sau.

## Đọc trước khi làm bất cứ việc gì

Bốn tài liệu ở gốc repo là nguồn sự thật. **Đừng suy diễn thiết kế từ code** — code chưa tồn tại.

| File | Nội dung |
|---|---|
| `PRD.md` | Phạm vi, 7 tiêu chí thành công, 6 quyết định sản phẩm, vị thế chi phí (§8) |
| `ARCHITECTURE.md` | Tầng kiến trúc, hợp đồng stage thống nhất, provider abstraction, đặc thù Windows |
| `DATABASE.md` | 15 bảng, trạng thái job, bố cục file trên đĩa |
| `BACKLOG.md` | Quy trình tối thiểu (Part A), kế hoạch 4 tuần + 4 cổng kiểm (Part B), 12 rủi ro (Part C) |

Đọc theo thứ tự: `BACKLOG.md` Part A → `PRD.md` §6 và §8 → `BACKLOG.md` Part C.

## Hiện trạng

- Kế hoạch **đã được founder duyệt**. Bắt đầu từ Week 1 item 1.1 trong `BACKLOG.md` Part B.
- **Không làm vượt kế hoạch.** Mỗi tuần có một cổng kiểm; Gate 1 hoặc Gate 2 có thể đổi hướng
  cả tuần làm việc, nên đừng xây trước những thứ của tuần sau.
- Đã xong: Python 3.12 và FFmpeg (item 1.1) trên máy laptop. **Máy khác phải cài lại** —
  xem `README.md`.
- Kế hoạch 30 ngày (`AI_Viral_Media_Network_30_Day_Plan.md`) thay thế thứ tự tuần ở BACKLOG Part B; ánh xạ ở BACKLOG Part F. Ba format `comedy` / `facts` / `gadget` thay cho ngách AI; thời lượng mặc định theo format, gate chấm theo target ±20%.
- Sprint 1 (channels, Trend Radar, Content Planner, TrendScore, Alembic) đã merge vào `main`.
- Sprint 2 (cổng duyệt kịch bản `SCRIPT_REVIEW`, sửa tay, tạo lại kèm ghi chú, prompt theo format có `prompt_version`, tạo job hàng loạt) xong trên nhánh `sprint-2-script-agent`. Prompt theo format nằm ở `app/prompts/format_*.md`.
- Week 1 walking skeleton đã chạy được (README.md mục "What is built"): Script → Voice → Visuals → Subtitles → Render → Quality → duyệt → export. Research, Strategy, SEO riêng, stock footage và Alembic chưa làm.

## Năm luật không được phá

Đây là những bất biến bảo vệ thương hiệu và doanh thu dịch vụ. Vi phạm là lỗi nghiêm trọng,
không phải lựa chọn thiết kế.

1. **Người duyệt là bắt buộc.** Job chỉ tới `EXPORTED` qua `AWAITING_APPROVAL → APPROVED`.
   Chặn ở ba chỗ độc lập: tầng service, ràng buộc `CHECK` trên `content_job`, và việc
   **không có code publish nào tồn tại** (`app/platforms/*` chỉ raise `NotImplementedError`).
   Không bao giờ viết đường publish tự động.
2. **Mọi artifact truy vết được qua `content_job.id`.** Mỗi lần chạy stage ghi một dòng
   `stage_run` bất biến kèm input, output, provider, model, cost, duration. Chạy lại thì
   thêm dòng mới và set `superseded_by`, **không ghi đè**.
3. **Asset không có giấy phép thì không xuất.** Mỗi `media_asset` lưu nguồn, licence,
   yêu cầu ghi công. Quality gate chặn job có licence `unknown`.
4. **Tiếng Việt là ràng buộc kỹ thuật, không phải chuỗi locale.** Ước lượng thời lượng dùng
   **số âm tiết**, không dùng số từ (tiếng Việt đơn âm, đếm từ sai 30–40%). Thời gian thật
   **luôn lấy từ segment timings của TTS**, không bao giờ từ ước lượng — một nguồn thời gian
   duy nhất cho cả audio và phụ đề, để chúng không lệch nhau.
5. **Mọi claim trong script phải dẫn về một `research_item` có source URL**, hoặc bị đánh dấu
   `unverified` và quality gate cảnh báo. Trong ngách AI, một câu sai tự tin là cách nhanh
   nhất mất uy tín kênh.

## Phân model cho stage lúc chạy (runtime)

Đây là lựa chọn của founder, tối ưu chi phí. Giá USD / 1 triệu token.

| Stage | Model | Effort | Vì sao |
|---|---|---|---|
| Research | `claude-haiku-5-5` | `low` | bóc tách và cấu trúc dữ liệu, không cần suy luận sâu |
| Strategy | `claude-haiku-5-5` | `low` | đủ tốt với brief theo khuôn |
| **Script** | `claude-sonnet-5-5` | `medium` | khâu quyết định chất lượng video — đừng hạ model ở đây |
| SEO | `claude-haiku-5-5` | `low` | title/caption/hashtag theo giới hạn ký tự |

Token thinking tính như token output, nên effort là đòn bẩy chi phí thứ hai sau model. Script để `medium`; ở Gate 1 so một video `medium` với một video `high` rồi mới quyết. Đổi bằng `SCRIPT_EFFORT` trong `.env`.

Giá: Haiku 5.5 $0,10 / $0,50 · Sonnet 5.5 $2,00 / $10,00 · Opus 5.5 $4,00 / $20,00.

Chi phí thực đo được: **~24.000 token/video** (~18k input, ~6k output, đã tính retry).
Với bảng trên: **~$0,025/video** (~650đ). 3 video/ngày ≈ **$2,25/tháng**.

**Dùng `anthropic_api`, không dùng `claude_cli`, cho mọi việc generate** — kể cả kênh riêng.
Hai lý do: `claude_cli` nằm ngoài mục đích của gói Claude Code Max (`PRD.md` §8), và nó ăn
vào đúng hạn mức mà founder cần cho công việc công ty. `claude_cli` chỉ để thử prompt khi
phát triển, và `ClaudeCliProvider` phải được đánh dấu `dev_only` — khởi động với
`APP_ENV=prod` thì từ chối boot.

Đặt **spend cap và cảnh báo ngân sách** trên API key. Bảng `cost_entry` ghi chi phí từng lần
gọi để kiểm chứng con số thật (item 4.6).

## Giao việc cho subagent lúc phát triển (dev-time)

Khác hẳn bảng trên: đây là model cho các agent viết code, tiêu hạn mức Max.

| Việc | Subagent | Model | Effort | Ghi chú |
|---|---|---|---|---|
| Phân tích yêu cầu, lập kế hoạch từng bước | `architect` | `opus` | `high` | chỉ đọc; gọi trước khi code việc lớn |
| Stage, service, provider, orchestrator, API | `backend-dev` | `sonnet` | `medium` | phần lớn khối lượng Week 1 |
| Model SQLAlchemy, Alembic, ràng buộc, index | `db-engineer` | `sonnet` | `medium` | item 1.3 và mọi thay đổi schema |
| Streamlit console | `frontend-dev` | `sonnet` | `low` | item 1.12, 2.8, 3.6 |
| Test từng stage, test ràng buộc DB, e2e offline | `tester` | `sonnet` | `medium` | chỉ sửa file test |
| Soát diff tìm bug và vi phạm quy ước | `reviewer` | `opus` | `high` | giao diff, **không** giao kết luận sẵn |
| Soát lộ secret, xử lý input ngoài, quyền | `security` | `opus` | `high` | bắt buộc trước khi thêm provider gọi mạng |
| README, RUNBOOK, cập nhật tài liệu | `docs-writer` | `haiku` | `low` | đọc code lấy sự thật, không bịa |

Định nghĩa từng subagent (model, effort, công cụ được dùng) nằm ở `.claude/agents/*.md`; sửa ở đó và giữ bảng này khớp.

Nguyên tắc: **việc phán xét dùng Opus** (kiến trúc, review, bảo mật), **việc sản xuất dùng
Sonnet** (viết code theo kế hoạch có sẵn), **việc cơ học dùng Haiku** (tài liệu).

Hạn mức Max đo thật: viết xong 4 tài liệu kế hoạch (~1.200 dòng) tốn **5% hạn mức tuần**.
Week 1 (~35–40 file) ước chừng **20–35% một tuần**. Rủi ro thật không ở hạn mức tuần mà ở
**cửa sổ 5 giờ** — nên làm dự án này ngoài giờ làm để không đụng quota công việc công ty.

## Hợp đồng stage

Mọi stage — agent LLM hay module FFmpeg — implement cùng một protocol, xem
`ARCHITECTURE.md` §4. Sáu việc xuyên suốt **không viết lặp trong từng stage**, mà **khai báo**
trên stage rồi `StageRunner` thực thi một lần:

- `input_model` / `output_model` — Pydantic, runner validate cả hai, **không bao giờ tin
  response của LLM**
- `timeout_s` — `asyncio.wait_for`
- `retry` — `RetryPolicy`, backoff lũy thừa có jitter; **chỉ** retry lỗi tạm thời và lỗi
  schema, **không bao giờ** retry content refusal hay quality failure
- Lỗi — phân cấp `StageError` có kiểu; exception lạ thì bọc lại, không nuốt
- Log — structlog, mọi dòng mang `job_id`, `stage`, `attempt`, `provider`, `model`
- Truy vết — runner ghi `stage_run` trước và sau khi chạy

LLM trả JSON sai thì: parse → validate → sửa một lần bằng cách đưa lỗi validation vào lại →
lần hai thất bại thì stage fail với `SchemaError` và job về tay người. **Không bao giờ chạy
tiếp với response parse dở.**

## Quy ước code

- Dependency **chỉ đi xuống** theo tầng ở `ARCHITECTURE.md` §2. Tầng business không import
  SDK của vendor. Có contract import-linter kiểm tra tự động — nếu nó đỏ thì sửa code, đừng
  sửa contract.
- Dependency injection ở biên: service nhận provider và repository, không với tay lấy global.
  Đây là điều kiện để `FixtureProvider` chạy được.
- `FixtureProvider` phát lại response đã ghi, khoá theo hash của request. Nhờ nó mà cả
  pipeline chạy trong CI **không mạng, không tốn tiền, kết quả tất định** — đây là quyết định
  test có đòn bẩy cao nhất của dự án, đừng bỏ.
- Secret chỉ qua biến môi trường. `.env` không commit. `.env.example` commit với giá trị rỗng.
- Alembic từ commit đầu. SQLite không drop/alter được cột, nên schema tùy hứng sẽ không
  migrate nổi sau một tuần.

## Windows

- Đường dẫn FFmpeg lấy từ config, không giả định `PATH`; probe lúc khởi động và ở `/health`.
- Tên file artifact **ngắn và hash**, không lấy từ text chủ đề: `MAX_PATH` 260 ký tự, mà
  `storage/jobs/{uuid}/...` cộng tên file tiếng Việt là chạm giới hạn.
- `PYTHONUTF8=1` trong script chạy, nếu không console Windows làm hỏng dấu tiếng Việt.
- Truyền text phụ đề **qua file**, không bao giờ nhét vào chuỗi filter trên command line.
- Font có đủ dấu tiếng Việt (Noto Sans / Be Vietnam Pro) **bundle trong repo**, không giả
  định máy có.

## Khi gặp việc không nằm trong kế hoạch

Cân mọi đề xuất tính năng bằng một câu hỏi: **"việc này có giúp xuất được một video hôm nay
không?"** Rủi ro R10 trong `BACKLOG.md` là trượt phạm vi sang SaaS trước khi video đầu tiên
kiếm được đồng nào — đó là cách dự án này thất bại kinh điển. Danh sách ngoài phạm vi ở
`PRD.md` §5.2 là ranh giới, không phải gợi ý.
