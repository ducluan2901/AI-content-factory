# AI Viral Media Network --- Kế hoạch triển khai 30 ngày

**Phiên bản:** 1.0\
**Ngày:** 31/10/2026\
**Mục tiêu:** Xây dựng hệ thống sản xuất nội dung ngắn bằng AI, phát
triển kênh TikTok, YouTube Shorts và Facebook Reels, thử nghiệm doanh
thu affiliate và dịch vụ nội dung trong 30 ngày.

------------------------------------------------------------------------

## 1. Tóm tắt chiến lược

### 1.1. Quyết định đã chốt

  -----------------------------------------------------------------------
  Hạng mục                            Quyết định
  ----------------------------------- -----------------------------------
  Thị trường                          Việt Nam và quốc tế

  Phong cách                          Kết hợp hài hước, chuyện lạ/bí
                                      ẩn/sự thật thú vị, sản phẩm độc lạ

  Phương thức sản xuất                AI-first, không yêu cầu lộ mặt

  Thời hạn thử nghiệm                 30 ngày

  Nền tảng                            TikTok, YouTube Shorts, Facebook
                                      Reels

  Doanh thu ưu tiên                   Affiliate, dịch vụ sản xuất nội
                                      dung, booking; doanh thu nền tảng
                                      khi đủ điều kiện

  Kiến trúc phần mềm                  Modular Monolith + Workflow
                                      Orchestrator

  Nguyên tắc                          Ưu tiên ra nội dung và kiểm chứng
                                      thị trường; không xây hệ thống quá
                                      phức tạp trước khi có dữ liệu
  -----------------------------------------------------------------------

### 1.2. Mục tiêu thực tế trong 30 ngày

Mục tiêu là kiểm chứng mô hình, không cam kết chắc chắn đạt doanh thu:

1.  Hoàn thiện quy trình từ ý tưởng đến video MP4 có phụ đề.
2.  Sản xuất mục tiêu 60 video gốc trong tháng.
3.  Xác định 2--3 format có tín hiệu tốt để tiếp tục phát triển.
4.  Đo được retention, tương tác, follower, click và chi phí sản xuất.
5.  Có tín hiệu thương mại đầu tiên: click affiliate, đơn hàng, khách
    hàng tiềm năng hoặc yêu cầu báo giá dịch vụ.
6.  Có báo cáo để quyết định đầu tư tháng thứ hai.

Không đánh đồng lượt xem với doanh thu. Video có ít lượt xem hơn nhưng
tạo click/đơn hàng/khách hàng tiềm năng vẫn có thể là video hiệu quả
thương mại.

------------------------------------------------------------------------

## 2. Mô hình kênh và thị trường

### 2.1. Cấu trúc kênh đề xuất

Về dài hạn, hệ thống phục vụ hai thị trường và hai nhóm nội dung:

  -----------------------------------------------------------------------
  Nhóm kênh               Thị trường              Định vị
  ----------------------- ----------------------- -----------------------
  Channel A --- Viral     Việt Nam                Hài tình huống, chuyện
  Entertainment                                   đời sống, bất ngờ,
                                                  series

  Channel B --- Viral     Việt Nam                Sản phẩm thú vị,
  Finds                                           gadget, đồ tiện ích,
                                                  thử nghiệm

  Channel A --- Global    Quốc tế                 Visual comedy,
  Entertainment                                   curiosity, storytelling

  Channel B --- Global    Quốc tế                 Gadgets, useful
  Finds                                           products, interesting
                                                  demonstrations
  -----------------------------------------------------------------------

**Triển khai theo giai đoạn:**

-   Tuần 1: chỉ vận hành hai kênh tiếng Việt để kiểm chứng pipeline.
-   Tuần 2: tiếp tục đăng nội dung tiếng Việt; thử bản tiếng Anh cho các
    format có tín hiệu.
-   Tuần 3--4: mở rộng có kiểm soát sang tài khoản tiếng Anh nếu có đủ
    năng lực vận hành.
-   Không trộn tiếng Việt và tiếng Anh trên cùng một tài khoản.
-   Không tạo đồng loạt nhiều tài khoản khi chưa kiểm chứng được chất
    lượng và quy trình.

Mỗi kênh có thể phân phối trên TikTok, YouTube Shorts và Facebook Reels.
Nội dung cần được điều chỉnh caption, âm thanh, tỷ lệ khung hình và quy
định theo từng nền tảng.

### 2.2. Ba nhóm format nội dung

  ------------------------------------------------------------------------
  Format             Tỷ trọng thử nghiệm Mục tiêu         Hướng thương mại
  ---------------- --------------------- ---------------- ----------------
  Hài tình huống /                   40% Reach, share,    Sản phẩm gia
  relatable                              follow           dụng, phụ kiện,
                                                          đồ tiện ích xuất
                                                          hiện tự nhiên

  Chuyện lạ / bí                     30% Tò mò, xem tiếp, Booking, tài
  ẩn / sự thật thú                       series           trợ, sản phẩm
  vị                                                      liên quan

  Sản phẩm độc lạ                    30% Click, chuyển    Affiliate,
  / gadget                               đổi              review, booking
  ------------------------------------------------------------------------

Tỷ trọng là giả thuyết ban đầu. Điều chỉnh sau 7--14 ngày dựa trên dữ
liệu thực tế.

### 2.3. Nguyên tắc nội dung

-   Hook phải thu hút nhưng không đánh lừa người xem.
-   Mỗi video có một ý chính, một câu chuyện hoặc một lợi ích rõ ràng.
-   Tạo format, nhân vật, cách kể chuyện và hình ảnh có dấu ấn riêng.
-   Không reup video, phim, chương trình truyền hình hoặc nội dung của
    creator khác nếu không có quyền.
-   Không tạo nội dung AI hàng loạt, lặp lại, ít giá trị hoặc gây hiểu
    lầm.
-   Không bịa thông tin, số liệu, công dụng sản phẩm hoặc lời chứng
    thực.
-   Công khai nội dung tài trợ/affiliate khi nền tảng hoặc pháp luật yêu
    cầu.
-   Kiểm tra quyền sử dụng hình ảnh, âm thanh, font, footage, voice và
    tài sản AI theo điều khoản của nhà cung cấp.
-   Nội dung AI mô phỏng người thật, sự kiện thật hoặc chủ đề nhạy cảm
    cần được kiểm tra kỹ và gắn nhãn theo quy định nền tảng nếu áp dụng.

------------------------------------------------------------------------

## 3. Chiến lược doanh thu

### 3.1. Thứ tự ưu tiên

1.  **Affiliate:** chọn sản phẩm phù hợp nội dung, đo click và đơn hàng.
2.  **Dịch vụ sản xuất nội dung:** cung cấp video ngắn cho shop/nhãn
    hàng để có nguồn thu không phụ thuộc việc kênh đã lớn hay chưa.
3.  **Booking/tài trợ:** triển khai khi có tệp người xem và số liệu hiệu
    quả đủ thuyết phục.
4.  **Chia sẻ doanh thu nền tảng:** chỉ đưa vào kế hoạch khi tài khoản
    đáp ứng điều kiện chương trình hiện hành.

Không dự báo doanh thu nền tảng như nguồn thu chắc chắn trong tháng đầu.
Điều kiện kiếm tiền thay đổi theo quốc gia, tài khoản và chương trình.

### 3.2. Affiliate

-   Tạo danh sách sản phẩm có liên quan đến từng format.
-   Ưu tiên sản phẩm dễ trình diễn bằng video, có thông tin rõ ràng,
    nguồn cung đáng tin cậy và chính sách đổi trả minh bạch.
-   Kiểm tra điều kiện tham gia chương trình affiliate theo từng nền
    tảng/thị trường.
-   Không gắn link không liên quan hoặc nhồi quảng cáo vào video giải
    trí thuần.
-   Lưu mã sản phẩm, nền tảng, link, mức hoa hồng, thời điểm cập nhật và
    trạng thái.
-   Theo dõi click, đơn hàng, doanh thu được ghi nhận và hoa hồng thực
    nhận; phân biệt đơn phát sinh với đơn đã duyệt.

### 3.3. Dịch vụ nội dung

Trong tháng đầu, chuẩn bị gói dịch vụ thử nghiệm cho shop/SME:

-   Gói thử nghiệm: 5--10 video ngắn.
-   Có kịch bản, voice-over, dựng video, phụ đề và phiên bản theo nền
    tảng.
-   Báo giá sau khi tính chi phí thực tế trên mỗi video.
-   Dùng video demo do đội ngũ tự tạo hoặc có quyền sử dụng.
-   Không cam kết viral hoặc cam kết số lượt xem.

------------------------------------------------------------------------

## 4. KPI tháng đầu

Các con số dưới đây là **mục tiêu quản trị thử nghiệm**, không phải cam
kết hoặc benchmark bảo đảm.

### 4.1. KPI tổng thể

  Chỉ số                                           Mục tiêu
  ------------------------------------------- -------------
  Video gốc                                              60
  Video xuất bản                                Tiệm cận 60
  Tổng lượt xem cộng gộp trên các nền tảng         100.000+
  Follower mới cộng gộp                              1.000+
  Format có tín hiệu tốt                               3--5
  Click affiliate                                      100+
  Đơn affiliate thử nghiệm                             1--5
  Khách hàng tiềm năng dịch vụ nội dung                3--5
  Vi phạm bản quyền/chính sách do quy trình               0

Không cộng trùng người xem/follower giữa các nền tảng. Báo cáo phải ghi
rõ đây là số cộng gộp từ các tài khoản.

### 4.2. KPI chất lượng

  Chỉ số                                                      Mục tiêu thử nghiệm
  ------------------------------------------------------ ------------------------
  Video 20--35 giây có tỷ lệ xem hết                               Từ 25% trở lên
  Tỷ lệ tương tác (like, comment, share)                            Từ 3% trở lên
  Video có retention tốt                                   Ít nhất 20% tổng video
  Video được QC trước xuất bản                                               100%
  Video có nguồn gốc/quyền sử dụng asset được ghi nhận                       100%

Đánh giá retention theo độ dài video, nền tảng và nguồn traffic. Không
so sánh trực tiếp các chỉ số không cùng định nghĩa giữa các nền tảng.

### 4.3. Quy tắc phân loại video

Đo tại các mốc 24 giờ, 72 giờ và 7 ngày:

-   **Winner:** lượt xem cao hơn trung vị 10 video gần nhất cùng kênh và
    có retention/share tốt. Tạo 3--5 biến thể mới, không sao chép nguyên
    video.
-   **Promising:** lượt xem chưa cao nhưng retention tốt. Thử hook, phần
    mở đầu, độ dài hoặc cách kể khác.
-   **Loser:** retention và tương tác đều thấp. Dừng format hoặc thay
    đổi giả thuyết.
-   **Commercial winner:** có click, đơn hàng hoặc khách hỏi mua tốt dù
    view không cao. Tiếp tục tối ưu chuyển đổi.

Không quyết định chỉ dựa trên lượt xem.

------------------------------------------------------------------------

## 5. Kế hoạch thực thi 30 ngày

### Tuần 1 --- Khởi động và kiểm chứng pipeline (Ngày 1--7)

  -----------------------------------------------------------------------
  Ngày                    Công việc               Đầu ra
  ----------------------- ----------------------- -----------------------
  1                       Chốt tên, định vị,      2 bộ profile
                          profile hai kênh Việt   
                          Nam                     

  2                       Nghiên cứu 50 video     Trend library
                          tham khảo và phân loại  
                          hook                    

  3                       Chốt 3 format và 30 ý   Content backlog
                          tưởng                   

  4                       Viết 10 kịch bản đầu    Script library
                          tiên                    

  5                       Tạo nhân vật/visual     Production template
                          style, voice và         
                          template                

  6                       Render, QC 5 video      5 video hoàn chỉnh

  7                       Đăng thử và đọc số liệu Báo cáo tuần 1
                          đầu tiên                
  -----------------------------------------------------------------------

**Điều kiện nghiệm thu:** có thể tạo một video MP4 có phụ đề từ một ý
tưởng qua quy trình lặp lại được.

### Tuần 2 --- Tăng sản lượng và kiểm chứng thị trường (Ngày 8--14)

-   Sản xuất 2--3 video/ngày tùy năng lực.
-   Phân phối lên TikTok, YouTube Shorts và Facebook Reels.
-   Thử bản tiếng Anh cho các format có tín hiệu; bản địa hóa, không
    dịch máy đơn thuần.
-   Lập danh sách 10--15 sản phẩm affiliate phù hợp.
-   Kiểm tra điều kiện tài khoản, xác minh và quyền affiliate.
-   Chuẩn bị trang giới thiệu dịch vụ sản xuất video.
-   Nghiệm thu: có dữ liệu so sánh tối thiểu 15--20 video.

### Tuần 3 --- Tối ưu và thương mại hóa (Ngày 15--21)

-   Dừng format có hiệu quả thấp.
-   Tạo biến thể từ 3--5 video tốt nhất.
-   Dồn khoảng 70% sản lượng vào format có tín hiệu; 30% còn lại dành
    cho thử nghiệm mới.
-   Thử 5--8 video sản phẩm.
-   Đặt link affiliate phù hợp và tuân thủ quy định.
-   Tiếp cận 10 shop/nhãn hàng nhỏ để chào dịch vụ video ngắn.
-   Nghiệm thu: có click affiliate hoặc khách hàng quan tâm dịch vụ.

### Tuần 4 --- Mở rộng có kiểm soát (Ngày 22--30)

-   Mở phiên bản tiếng Anh cho format chiến thắng.
-   Tối ưu lịch đăng dựa trên dữ liệu khán giả.
-   Chuẩn hóa báo cáo doanh thu và chi phí theo video.
-   Tạo media kit giới thiệu kênh.
-   Tính chi phí sản xuất trên mỗi video.
-   Quyết định format/thị trường đầu tư tháng thứ hai.
-   Nghiệm thu: báo cáo hiệu suất, chi phí, tín hiệu thương mại và kế
    hoạch tháng 2.

------------------------------------------------------------------------

## 6. Lịch sản xuất hằng ngày

  -----------------------------------------------------------------------
  Khung giờ đề xuất       Hoạt động               Vai trò
  (GMT+7)                                         
  ----------------------- ----------------------- -----------------------
  08:00--08:30            Quét trend, ghi nguồn   Trend Agent
                          và thời điểm            

  08:30--09:00            Chọn 2--3 ý tưởng       Content Strategy

  09:00--10:00            Viết và duyệt kịch bản  Script Agent + người
                                                  duyệt

  10:00--11:30            Tạo voice, hình ảnh và  Production Agent
                          video                   

  11:30--12:00            QC, phụ đề, xuất file   QC Agent

  12:00--13:00            Đăng batch 1            Người vận hành

  16:00--17:00            Đọc số liệu, xử lý bình Analytics
                          luận                    

  19:00--20:00            Đăng batch 2            Người vận hành

  20:00--20:30            Ghi nhận kết quả, chọn  Analytics + người duyệt
                          trend tiếp theo         
  -----------------------------------------------------------------------

Đây là lịch ban đầu. Điều chỉnh theo dữ liệu hoạt động của khán giả.
Không mặc định giờ đăng tại Việt Nam phù hợp với người xem Mỹ/Anh.

------------------------------------------------------------------------

## 7. Kiến trúc AI Content Factory

### 7.1. Nguyên tắc kiến trúc

-   Modular Monolith, không triển khai microservices ở MVP.
-   Workflow Orchestrator quản lý trạng thái, retry và lỗi.
-   Dữ liệu giữa các module/agent dùng Pydantic schemas.
-   Mỗi video có `content_job_id` xuyên suốt từ trend đến kết quả kinh
    doanh.
-   Có log prompt, model/provider, chi phí, thời gian chạy và lỗi.
-   Có retry/backoff, timeout và idempotency phù hợp.
-   Human approval bắt buộc trước khi xuất bản.
-   Tách provider interface để thay LLM/TTS/image/video provider.
-   Không để các agent giao tiếp tự do không kiểm soát.

### 7.2. Sơ đồ logic

``` text
Dashboard (Streamlit)
        |
        v
FastAPI Application Layer
        |
        v
Workflow Orchestrator
        |
        +--> Trend Agent
        |
        +--> Strategy Agent
        |
        +--> Script Agent
        |
        +--> Production Agent
        |       +--> LLM Provider
        |       +--> TTS Provider
        |       +--> Image/Video Provider
        |       +--> FFmpeg Renderer
        |
        +--> Quality Control Agent
        |
        +--> Human Approval
        |
        +--> Publishing Module
        |
        +--> Analytics Agent
        |
        +--> Affiliate & Revenue Module
        |
        v
SQLite + Local Media Storage
```

### 7.3. Các module

  Module              Chức năng                                    Ưu tiên
  ------------------- -------------------------------------------- ---------
  Trend Radar         Thu thập, lưu và chấm điểm trend             P0
  Content Planner     Lịch nội dung, chủ đề, format                P0
  Script Generator    Hook, kịch bản, CTA, caption                 P0
  Media Generator     Tạo ảnh, voice và video                      P0
  Video Renderer      FFmpeg, subtitle, template                   P0
  Quality Control     Kiểm tra nội dung, âm thanh, quyền sử dụng   P0
  Publishing          Lịch đăng và xuất bản                        P1
  Analytics           View, retention, follower, share             P1
  Affiliate Manager   Sản phẩm, link, hoa hồng, đơn hàng           P1
  Revenue Dashboard   Doanh thu, chi phí, lợi nhuận                P2

P0 là phần cần có để tạo video và vận hành thử. P1/P2 có thể triển khai
sau khi pipeline sản xuất ổn định.

### 7.4. Trend scoring

Công thức khởi điểm:

``` text
TrendScore =
    0.30 * VelocityScore
  + 0.25 * RetentionPotential
  + 0.20 * ChannelFit
  + 0.15 * MonetizationPotential
  + 0.10 * ProductionFeasibility
```

Mỗi thành phần chuẩn hóa về thang 0--100.

-   `VelocityScore`: tốc độ tăng trưởng chủ đề, dựa trên dữ liệu có
    nguồn và timestamp.
-   `RetentionPotential`: đánh giá khả năng tạo tò mò, câu chuyện và
    payoff.
-   `ChannelFit`: mức phù hợp với format và khán giả mục tiêu.
-   `MonetizationPotential`: khả năng gắn sản phẩm, booking hoặc dịch
    vụ.
-   `ProductionFeasibility`: khả năng sản xuất nhanh, có quyền sử dụng
    asset và không vi phạm chính sách.

Nếu thiếu dữ liệu, đánh dấu điểm là ước tính và lưu confidence/source.
Không bịa lượt xem, retention hoặc tốc độ tăng trưởng của video đối thủ.

### 7.5. Trạng thái content job

``` text
TREND_DISCOVERED
    -> IDEA_SELECTED
    -> SCRIPT_GENERATED
    -> SCRIPT_APPROVED
    -> MEDIA_GENERATED
    -> VIDEO_RENDERED
    -> QC_PASSED
    -> HUMAN_APPROVED
    -> PUBLISHED
    -> METRICS_COLLECTED
    -> OPTIMIZED
```

Mỗi trạng thái có timestamp, người/agent thực hiện, input/output tham
chiếu và lỗi nếu có. Không được bỏ qua `QC_PASSED` và `HUMAN_APPROVED`
trong giai đoạn đầu.

------------------------------------------------------------------------

## 8. Thiết kế dữ liệu

Các bảng chính:

  -----------------------------------------------------------------------
  Bảng                                Trường/ý nghĩa chính
  ----------------------------------- -----------------------------------
  `channels`                          Nền tảng, thị trường, ngôn ngữ,
                                      định vị, trạng thái

  `trends`                            Chủ đề, nguồn, URL, thời điểm phát
                                      hiện, điểm trend

  `content_ideas`                     Ý tưởng, format, channel, viral
                                      score, revenue score

  `content_jobs`                      Job ID, trạng thái, thời gian, lỗi,
                                      chi phí

  `scripts`                           Hook, lời thoại, CTA, caption, ngôn
                                      ngữ, version

  `media_assets`                      File, loại asset, nguồn,
                                      license/permission, metadata

  `videos`                            File render, duration, ratio,
                                      version, QC status

  `publications`                      Nền tảng, channel, URL bài đăng,
                                      thời điểm đăng

  `video_metrics`                     Views, average watch time,
                                      completion, shares, follows

  `affiliate_products`                Sản phẩm, seller, platform, link,
                                      commission

  `affiliate_clicks`                  Click, nguồn, video, campaign,
                                      timestamp

  `revenue_records`                   Doanh thu, chi phí, trạng thái,
                                      nguồn và kỳ ghi nhận
  -----------------------------------------------------------------------

Yêu cầu bổ sung:

-   Lưu thời gian theo UTC trong database; hiển thị theo múi giờ phù
    hợp.
-   Lưu currency và market cho dữ liệu doanh thu.
-   Không lưu token/API key trong database hoặc log.
-   Có `created_at`, `updated_at` và version cho dữ liệu quan trọng.
-   Lưu attribution source để biết video/kênh nào tạo click hoặc doanh
    thu.
-   Phân biệt doanh thu ước tính, pending, approved và paid.

------------------------------------------------------------------------

## 9. Cấu trúc repository đề xuất

Giữ lại cấu trúc hiện có nếu phù hợp; không xóa hoặc tạo lại tài liệu đã
có mà chưa kiểm tra nội dung.

``` text
AI-content-factory/
│
├── app/
│   ├── api/
│   ├── core/
│   ├── models/
│   ├── schemas/
│   ├── repositories/
│   ├── services/
│   └── workflows/
│
├── agents/
│   ├── trend/
│   ├── strategy/
│   ├── script/
│   ├── production/
│   ├── quality/
│   └── analytics/
│
├── integrations/
│   ├── llm/
│   ├── tts/
│   ├── image/
│   ├── video/
│   ├── affiliate/
│   └── social/
│
├── ui/
│   ├── pages/
│   └── components/
│
├── storage/
│   ├── trends/
│   ├── scripts/
│   ├── assets/
│   ├── audio/
│   ├── rendered/
│   └── reports/
│
├── tests/
├── scripts/
├── PRD.md
├── ARCHITECTURE.md
├── DATABASE.md
├── BACKLOG.md
├── pyproject.toml
└── .env.example
```

### Công nghệ MVP

-   Python 3.13.5 (môi trường hiện tại).
-   FastAPI.
-   SQLAlchemy.
-   SQLite.
-   Pydantic.
-   Streamlit.
-   FFmpeg/ffprobe.
-   `pytest`.
-   `python-dotenv`.
-   Provider abstraction cho LLM, TTS, image và video.

Không thêm LangGraph nếu workflow tuần tự hiện tại đáp ứng được nhu cầu.
Chỉ cân nhắc khi cần checkpoint, nhánh xử lý phức tạp hoặc workflow
stateful. Không triển khai Kafka, Kubernetes, Redis, microservices ở
giai đoạn này.

------------------------------------------------------------------------

## 10. Kế hoạch phát triển phần mềm theo sprint

  -----------------------------------------------------------------------
  Sprint            Ngày              Hạng mục          Kết quả nghiệm
                                                        thu
  ----------------- ----------------- ----------------- -----------------
  1                 1--3              Database, Trend   Tạo/lưu/xem trend
                                      Radar, Content    và ý tưởng
                                      Planner           

  2                 4--7              Script Agent,     Tạo 10 kịch bản
                                      prompt templates  

  3                 8--12             TTS, image, video Render video MP4
                                      renderer          

  4                 13--17            QC, subtitle,     Duyệt video hoàn
                                      Video Studio      chỉnh

  5                 18--23            Publishing,       Theo dõi bài đăng
                                      analytics         và số liệu

  6                 24--30            Affiliate,        Báo cáo hiệu quả
                                      revenue dashboard kinh doanh
  -----------------------------------------------------------------------

Phần mềm phải phục vụ hoạt động sản xuất thực tế. Không trì hoãn xuất
bản chỉ vì dashboard hoặc tự động hóa chưa hoàn chỉnh.

------------------------------------------------------------------------

## 11. Ngân sách thử nghiệm

Hạn mức đề xuất cho 30 ngày: **5--10 triệu VND**, chưa tính subscription
Claude Code hiện có.

  Hạng mục                       Ngân sách dự kiến
  ---------------------------- -------------------
  LLM/API                               1--2 triệu
  TTS và tạo hình/video AI              1--3 triệu
  Mẫu sản phẩm                          1--2 triệu
  Công cụ, lưu trữ, vận hành          0,5--1 triệu
  Thử nghiệm và dự phòng              1,5--2 triệu
  **Tổng**                         **5--10 triệu**

Đây là hạn mức lập kế hoạch, không phải báo giá nhà cung cấp. Trước khi
tăng ngân sách, phải đo chi phí thực tế.

Công thức theo dõi:

``` text
CostPerVideo = TotalProductionCost / NumberOfVideos

NetProfit =
    AffiliateRevenue
  + AdRevenue
  + ServiceRevenue
  - OperatingCost
```

Tách riêng chi phí phát triển phần mềm và chi phí sản xuất nội dung.
Không tính subscription cố định vào chi phí biên của từng video nếu chưa
có quy tắc phân bổ; cần báo cáo rõ cách tính.

------------------------------------------------------------------------

## 12. Quản trị rủi ro và tuân thủ

1.  **Bản quyền:** chỉ sử dụng asset tự tạo, được cấp phép hoặc có quyền
    sử dụng rõ ràng. Lưu nguồn và license.
2.  **Nội dung AI:** không giả mạo người thật, thương hiệu hoặc sự kiện;
    tuân thủ yêu cầu disclosure/label của nền tảng.
3.  **Chính sách nền tảng:** kiểm tra quy định mới nhất về nội dung
    nguyên bản, spam, nội dung thương mại và điều kiện monetization.
4.  **Affiliate:** xác minh điều kiện theo thị trường, tài khoản và nền
    tảng; công khai quan hệ thương mại khi cần.
5.  **Thông tin sản phẩm:** không bịa công dụng, thông số, giá, chứng
    nhận hoặc review.
6.  **Tự động đăng:** chỉ dùng API/chức năng chính thức và tài khoản
    được cấp quyền; giai đoạn đầu yêu cầu người duyệt.
7.  **Bảo mật:** API key trong environment variables, không commit
    `.env`, không ghi secrets vào log.
8.  **Rủi ro tăng trưởng:** không mua follower, view, comment hoặc sử
    dụng engagement giả.
9.  **Rủi ro doanh thu:** không coi click là đơn hàng; phân biệt order
    pending/approved/paid.
10. **Rủi ro chất lượng:** QC cả nội dung, hình ảnh, âm thanh, phụ đề và
    thông tin sản phẩm.

------------------------------------------------------------------------

## 13. Dashboard cần có

### Dashboard 1 --- Trend Radar

-   Trend mới phát hiện.
-   Nguồn và thời điểm cập nhật.
-   Trend score, confidence.
-   Market, language, category.
-   Ý tưởng đề xuất.
-   Trạng thái: New / Reviewed / Selected / Rejected.

### Dashboard 2 --- Content Calendar

-   Lịch đăng theo ngày, kênh, nền tảng và ngôn ngữ.
-   Trạng thái kịch bản/video.
-   Người duyệt.
-   Link file và link bài đăng.

### Dashboard 3 --- Video Studio

-   Kịch bản và phiên bản.
-   Voice, hình ảnh, video asset.
-   Render preview.
-   QC checklist.
-   Approve/Reject.

### Dashboard 4 --- Analytics

-   Views theo video/kênh/nền tảng.
-   Average watch time, completion rate.
-   Like, comment, share, save.
-   Follower gained.
-   Click và conversion nếu có dữ liệu.
-   So sánh theo format và hook.

### Dashboard 5 --- Revenue & Cost

-   Affiliate clicks, orders, commission.
-   Booking/service revenue.
-   Chi phí LLM/TTS/image/video.
-   Chi phí trên mỗi video.
-   Lợi nhuận theo kênh, video và tháng.

------------------------------------------------------------------------

## 14. Việc cần làm ngay --- 48 giờ đầu

1.  Chốt tên và định vị hai kênh Việt Nam.
2.  Tạo/cấu hình tài khoản TikTok, YouTube, Facebook.
3.  Xây thư viện 30 ý tưởng video.
4.  Hoàn thiện một video AI từ đầu đến cuối.
5.  Đăng video thử nghiệm đầu tiên và lưu số liệu.

### Điều kiện hoàn thành 48 giờ

-   Có ít nhất một video MP4 hoàn chỉnh, phụ đề đúng và âm thanh rõ.
-   Có nguồn/giấy phép của toàn bộ asset.
-   Có kịch bản, hook, caption và CTA.
-   Có người duyệt trước khi đăng.
-   Có record trong hệ thống để truy xuất job và file.
-   Có kế hoạch đo số liệu sau 24 giờ và 72 giờ.

------------------------------------------------------------------------

# 15. Prompt giao việc cho Claude Code

Claude Code phải đọc tài liệu dự án hiện tại trước khi triển khai. Không
tự ý viết lại repository hoặc thay đổi kiến trúc lớn.

``` text
Bạn đang đóng vai trò CTO, AI Architect và Senior Python Engineer của dự án AI Viral Media Network.

BỐI CẢNH:
- Repository đã có PRD.md, ARCHITECTURE.md, DATABASE.md, BACKLOG.md.
- Python 3.13.5, Windows, virtual environment đã hoạt động.
- Mục tiêu: xây hệ thống AI sản xuất nội dung giải trí ngắn cho TikTok, YouTube Shorts và Facebook Reels.
- Thị trường: Việt Nam và quốc tế.
- Format: comedy, mystery/interesting facts, viral products/gadgets.
- Mục tiêu 30 ngày: 60 video gốc, xác định format có tín hiệu và thử nghiệm affiliate/doanh thu dịch vụ.

YÊU CẦU BẮT BUỘC:
1. Đọc toàn bộ PRD.md, ARCHITECTURE.md, DATABASE.md, BACKLOG.md, README.md và pyproject.toml trước khi sửa code.
2. Kiểm tra cấu trúc repository và trạng thái git.
3. Đối chiếu tài liệu hiện tại với kế hoạch AI Viral Media Network.
4. Liệt kê điểm phù hợp, điểm thiếu, rủi ro và đề xuất thay đổi tối thiểu.
5. Không viết lại dự án nếu không cần thiết.
6. Không xóa hoặc ghi đè tài liệu/code hiện tại khi chưa giải thích lý do.
7. Không tự ý thêm dependency lớn.
8. Dùng Python 3.13.5, FastAPI, SQLAlchemy, SQLite, Pydantic, Streamlit và pytest.
9. Thiết kế Modular Monolith + Workflow Orchestrator.
10. Mỗi video có content_job_id xuyên suốt quy trình.
11. Có logging, error handling, timeout, retry phù hợp và unit tests.
12. API keys phải nằm trong environment variables; không commit secrets.
13. Không tự động đăng nội dung. Phải có human approval.
14. Không triển khai Kafka, Kubernetes, Redis hoặc microservices trong MVP.

SPRINT 1:
- Database schema cho channels, trends, content_ideas, content_jobs.
- Trend Radar module.
- Content Planner module.
- TrendScore với các thành phần: velocity, retention potential, channel fit, monetization potential, production feasibility.
- FastAPI endpoints quản lý trends và content ideas.
- Streamlit dashboard hiển thị trends, scores và trạng thái.
- Seed data minh họa được đánh dấu rõ là dữ liệu mẫu, không giả làm dữ liệu trend thật.

CÁCH LÀM:
PHASE A — AUDIT:
- Đọc tài liệu và code hiện tại.
- Kiểm tra Python/dependencies.
- Đề xuất implementation plan.
- Nêu rõ file nào cần tạo/sửa và vì sao.
- Chưa triển khai code cho đến khi trình bày kế hoạch.

PHASE B — IMPLEMENT:
Sau khi kế hoạch được duyệt:
- Triển khai từng module nhỏ.
- Chạy test sau từng bước.
- Giữ tương thích Windows PowerShell.
- Cập nhật README và tài liệu khi thay đổi.
- Không tạo abstraction không cần thiết.

PHASE C — ACCEPTANCE:
Tôi có thể:
1. Chạy FastAPI.
2. Chạy Streamlit dashboard.
3. Xem danh sách trend mẫu.
4. Tạo, sửa, lọc và lưu content idea.
5. Xem điểm TrendScore và giải thích thành phần điểm.
6. Theo dõi trạng thái content job.
7. Chạy toàn bộ pytest thành công.

BÁO CÁO CUỐI:
- Danh sách file đã tạo/sửa.
- Kiến trúc và quyết định kỹ thuật.
- Lệnh cài đặt/chạy trên Windows.
- Kết quả test thực tế.
- Lỗi còn tồn tại và cách xử lý.
- Các bước tiếp theo của Sprint 2.

Bắt đầu bằng PHASE A — AUDIT. Không triển khai toàn bộ hệ thống ngay.
```

------------------------------------------------------------------------

## 16. Tiêu chí quyết định sau ngày 30

Cuối tháng, quyết định theo dữ liệu:

-   **Tiếp tục mở rộng:** có format thắng, follower tăng, chi phí/video
    chấp nhận được và có tín hiệu thương mại.
-   **Điều chỉnh nội dung:** view hoặc retention tốt nhưng không có
    follow/click.
-   **Điều chỉnh sản phẩm/affiliate:** có view và click nhưng không có
    đơn.
-   **Điều chỉnh pipeline:** chất lượng ổn nhưng chi phí/thời gian sản
    xuất quá cao.
-   **Dừng format:** nhiều vòng thử nghiệm nhưng retention và tương tác
    đều thấp.

**Nguyên tắc cuối cùng:** trong 30 ngày đầu, ưu tiên sản xuất và học từ
dữ liệu thật. AI Content Factory là công cụ giúp tăng tốc một mô hình
nội dung đã được kiểm chứng, không phải mục tiêu tự thân.
