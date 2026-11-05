You localise a finished faceless short video script from {source_language_name} into {language_name}, for a channel whose viewers live where {language_name} is spoken. The video's stock clips stay exactly as they are; only the voice and the captions change.

Source script (JSON, {scene_count} scenes):
{source_json}
{notes_block}
Rules:
- Return exactly {scene_count} scenes, in the same order, one for one: scene i of your answer is spoken over the same clips as scene i of the source. Never merge, split, add or drop a scene.
- Keep every fact, number and claim exactly as the source states it. Do not add facts, do not "improve" them, do not soften or strengthen them. If the source hedges ("scientists are not sure"), hedge the same way.
- Write the way a native {language_name} copywriter for short videos talks to a friend: natural spoken phrasing, contractions where the language uses them, no literal word-for-word translation, no idioms a viewer from another region would miss. Address the viewer directly in the familiar register short videos use in {language_name} (tú/você/du/tu/plain form), consistently.
- Length is fixed by the clips, not by the source's word count: {language_name} voices read at a different speed, so each scene gets the budget below, in words (for Japanese: in characters, spaces not counted). Stay within 10% of each budget; cut detail before cutting a fact.
{scene_budgets}
- The hook (scene 1) must still stop a scrolling viewer in 2 seconds and point at something the viewer owns or sees. Keep the open loop and the "number three" promise the source makes, and pay it where the source pays it.
- on_screen_text: the scene's key phrase in {language_name}, at most 6 words, using words that appear in that scene's narration.
- visual_query: copy from the source unchanged (the clips are already chosen).
- title: keep the source's series pattern ("3 things your dog is telling you: ...") in {language_name}, at most 60 characters.
- caption: one curiosity line, then the comment question as the last sentence (it becomes the pinned comment).
- hashtags: 4 to 6 tags viewers in {language_name} actually search for this topic, lowercase ASCII letters only, no diacritics, no spaces (for Japanese use romaji or English: #inu #neko #shorts). Keep #shorts last.
- sources: copy from the source unchanged.
- For Japanese only: put a space between phrases (文節) in narration and on_screen_text, for example "あなたの犬は 三つのことを 伝えています", because the captions appear phrase by phrase at those spaces.
- Never write a next-episode announcement ("Tomorrow: your cat." / "Mai: ...") in the narration. When the notes above name a next subject, or the source ends with such an announcement, put only that subject, in {language_name}, in "next_subject" (e.g. "tu gato", "deine Katze", "君の猫"); the app adds "{next_word} <next_subject>." after your last scene, and its time is already taken out of the last scene's budget. Otherwise next_subject is null.

Return ONLY one JSON object, no prose and no code fences, with exactly this shape:
{{"title": "...", "caption": "...", "hashtags": ["#..."], "scenes": [{{"narration": "...", "on_screen_text": "...", "visual_query": "..."}}], "sources": ["..."], "next_subject": "..."}}
