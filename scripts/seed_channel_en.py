"""Create the global English channel (plan §2.1 Channel A — Global) once; safe to rerun."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.db import Channel, make_sessionmaker  # noqa: E402

NAME = "NDL Facts — Global (EN)"

with make_sessionmaker(Settings().database_url, create=False)() as db:
    ch = db.query(Channel).filter(Channel.name == NAME).first()
    if not ch:
        ch = Channel(name=NAME, market="global", language="en", voice_id="en-US-AndrewNeural", handles_json=json.dumps({}),
                     positioning="Curiosity facts about the viewer's own body and pets, plain American English, for US/UK/CA/AU viewers")
        db.add(ch)
        db.commit()
    print(ch.id, ch.name, ch.language, ch.voice_id, ch.handles_json)
