"""Create the five translated global channels (reports/plan_5_kenh_ngoai_2026-11-05.md) once; safe to rerun.

    python scripts/seed_channels_global.py                      # create / list
    python scripts/seed_channels_global.py es=UCxxxx ja=UCyyyy  # also record YouTube channel ids (publish.py's guard)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.db import Channel, make_sessionmaker  # noqa: E402

CHANNELS = {  # language: (name, Edge voice, positioning)
    "es": ("NDL Datos de Mascotas (ES)", "es-MX-JorgeNeural", "Pet and body facts for Spanish-speaking viewers in Latin America and the US"),
    "pt": ("NDL Fatos Pet (PT-BR)", "pt-BR-AntonioNeural", "Pet and body facts for Brazilian viewers"),
    "de": ("NDL Haustier Fakten (DE)", "de-DE-ConradNeural", "Pet and body facts for viewers in Germany, Austria and Switzerland"),
    "fr": ("NDL Faits Animaux (FR)", "fr-FR-HenriNeural", "Pet and body facts for viewers in France, Québec, Belgium and Switzerland"),
    "ja": ("NDL ペット雑学 (JA)", "ja-JP-KeitaNeural", "Pet and body facts for viewers in Japan"),
}
youtube = dict(a.split("=", 1) for a in sys.argv[1:])

with make_sessionmaker(Settings().database_url, create=False)() as db:
    for lang, (name, voice, positioning) in CHANNELS.items():
        ch = db.query(Channel).filter(Channel.name == name).first()
        if not ch:
            ch = Channel(name=name, market="global", language=lang, voice_id=voice, handles_json=json.dumps({}), positioning=positioning)
            db.add(ch)
        if lang in youtube:
            ch.handles_json = json.dumps(json.loads(ch.handles_json or "{}") | {"youtube": youtube[lang]})
        db.commit()
        print(ch.id, ch.language, ch.voice_id, ch.handles_json, ch.name)
