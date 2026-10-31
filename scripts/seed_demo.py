"""Seed SAMPLE channels, trends and ideas so the dashboards have something to show.

Every row is flagged is_sample=1 and source='sample'. The scores are illustrative guesses, not measured
data; nothing here is a real trend observation (plan §15: seed data must never pass as real).
Run from the repo root:  .\\.venv\\Scripts\\python.exe scripts\\seed_demo.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Channel, ContentIdea, Trend, make_sessionmaker  # noqa: E402
from app.planner import default_duration, rescore  # noqa: E402

CHANNELS = [
    dict(name="Kênh A (mẫu) — Viral Entertainment VN", market="vn", language="vi", positioning="Hài tình huống, chuyện đời sống, bất ngờ, series"),
    dict(name="Kênh B (mẫu) — Viral Finds VN", market="vn", language="vi", positioning="Sản phẩm thú vị, gadget, đồ tiện ích, thử nghiệm"),
]

# (title, format, summary, components) — components are illustrative
TRENDS = [
    ("Sếp nhắn tin lúc 11 giờ đêm", "comedy", "Tình huống công sở ai cũng từng gặp; nhiều biến thể đóng vai hai phía.", dict(velocity=70, retention_potential=65, channel_fit=85, monetization_potential=30, production_feasibility=90)),
    ("Mẹ Việt và cái điều hoà 26 độ", "comedy", "Xung đột thế hệ quanh chuyện tiết kiệm điện.", dict(velocity=60, retention_potential=70, channel_fit=90, monetization_potential=45, production_feasibility=95)),
    ("Đi ăn cưới mùa cao điểm", "comedy", "Phong bì, bàn tiệc, câu hỏi 'bao giờ đến lượt'.", dict(velocity=55, retention_potential=60, channel_fit=80, monetization_potential=20, production_feasibility=90)),
    ("Vì sao mèo sợ dưa chuột", "facts", "Câu hỏi quen thuộc, có giải thích hành vi học được nhiều nguồn nhắc đến.", dict(velocity=50, retention_potential=75, channel_fit=70, monetization_potential=25, production_feasibility=95)),
    ("Lá thư trong chai trôi 30 năm", "facts", "Dạng chuyện lạ có mở đầu, hành trình và kết thúc rõ.", dict(velocity=40, retention_potential=85, channel_fit=65, monetization_potential=15, production_feasibility=85)),
    ("Thị trấn cấm chết", "facts", "Chuyện lạ về quy định địa phương; cần kiểm chứng nguồn trước khi viết.", dict(velocity=45, retention_potential=80, channel_fit=60, monetization_potential=10, production_feasibility=70)),
    ("Đèn ngủ cảm biến tự tắt", "gadget", "Đồ tiện ích giá rẻ, dễ quay thử, có sẵn trên sàn.", dict(velocity=65, retention_potential=55, channel_fit=85, monetization_potential=85, production_feasibility=80)),
    ("Máy hút bụi mini cho bàn phím", "gadget", "Sản phẩm trình diễn trước-sau rõ ràng.", dict(velocity=60, retention_potential=50, channel_fit=80, monetization_potential=80, production_feasibility=85)),
    ("Bình giữ nhiệt hiện nhiệt độ", "gadget", "Gadget có khoảnh khắc 'ồ' khi nắp sáng số.", dict(velocity=50, retention_potential=55, channel_fit=75, monetization_potential=75, production_feasibility=90)),
    ("Ghế gấp bỏ túi", "gadget", "Chưa có dữ liệu về độ phổ biến; điểm velocity bỏ trống để thể hiện ước tính.", dict(retention_potential=50, channel_fit=70, monetization_potential=70, production_feasibility=75)),
]

# (channel index, trend title, idea title, hook, premise, product_desc)
IDEAS = [
    (0, "Sếp nhắn tin lúc 11 giờ đêm", "3 kiểu trả lời sếp lúc nửa đêm", "Tin nhắn thứ ba mới là cú chốt", "Ba cách phản ứng từ ngoan ngoãn đến bất cần, kết bằng cách thứ tư không ai ngờ.", None),
    (0, "Mẹ Việt và cái điều hoà 26 độ", "Cuộc chiến nút điều hoà", "Nhà có hai phe, một cái remote", "Con chỉnh 20, mẹ chỉnh 26, bố giấu remote.", None),
    (0, "Vì sao mèo sợ dưa chuột", "Mèo và quả dưa chuột: sự thật", "Không phải vì dưa chuột đâu", "Giải thích phản xạ giật mình và vì sao không nên thử với mèo nhà.", None),
    (0, "Lá thư trong chai trôi 30 năm", "Lá thư trôi 30 năm", "Người gửi đã quên, người nhận thì không", "Kể lại hành trình chiếc chai theo các mốc có nguồn.", None),
    (1, "Đèn ngủ cảm biến tự tắt", "Chiếc đèn ngủ nhỏ cho phòng ngủ", "Đèn ngủ này có gì khác?", "Kế hoạch quay (chưa quay): giới thiệu đèn và ba mức sáng. Chỉ dùng thông tin sản phẩm đã cho.", "Đèn ngủ cảm biến chuyển động, pin sạc, 3 mức sáng. Không nêu thông số ngoài mô tả này."),
    (1, "Máy hút bụi mini cho bàn phím", "Máy hút bụi mini cho bàn phím", "Bàn phím của bạn có sạch không?", "Kế hoạch quay (chưa quay): giới thiệu máy hút bụi mini và cách cắm USB. Chỉ dùng thông tin sản phẩm đã cho.", "Máy hút bụi mini USB cho bàn phím và bàn làm việc."),
    (1, "Bình giữ nhiệt hiện nhiệt độ", "Bình giữ nhiệt có màn hình nhiệt độ", "Bình này có màn hình trên nắp", "Kế hoạch quay (chưa quay): giới thiệu bình và màn hình trên nắp. Chỉ dùng thông tin sản phẩm đã cho.", "Bình giữ nhiệt có màn hình nhiệt độ trên nắp."),
    (1, "Ghế gấp bỏ túi", "Ghế gấp nhỏ gọn", "Một chiếc ghế gấp nhỏ gọn", "Kế hoạch quay (chưa quay): giới thiệu ghế gấp. Chỉ dùng thông tin sản phẩm đã cho.", "Ghế gấp nhỏ gọn, chịu tải theo mô tả của người bán; không tự bịa con số."),
]


def main() -> None:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    sm = make_sessionmaker(s.database_url)
    with sm() as db:
        if db.scalars(select(Channel.id).where(Channel.is_sample.is_(True))).first():
            print("sample data already present; nothing to do")
            return
        chans = [Channel(**c, is_sample=True, handles_json=json.dumps({"tiktok": "@sample"})) for c in CHANNELS]
        db.add_all(chans)
        trends = {}
        for title, fmt, summary, comps in TRENDS:
            t = Trend(title=title, format=fmt, summary=summary, source="sample", market="vn", language="vi", is_sample=True,
                      notes="Dữ liệu mẫu: điểm số là minh hoạ, không phải đo đạc.", **comps)
            rescore(t)
            trends[title] = t
        db.add_all(trends.values())
        db.flush()
        for ci, trend_title, title, hook, premise, product in IDEAS:
            t = trends[trend_title]
            db.add(ContentIdea(channel_id=chans[ci].id, trend_id=t.id, title=title, format=t.format, hook=hook, premise=premise, product_desc=product,
                               target_duration_s=default_duration(t.format), viral_score=t.retention_potential, revenue_score=t.monetization_potential, is_sample=True))
        db.commit()
        print(f"seeded {len(chans)} channels, {len(trends)} trends, {len(IDEAS)} ideas (all marked is_sample)")


if __name__ == "__main__":
    main()
