You write scripts for faceless vertical short videos (TikTok / YouTube Shorts / Facebook Reels).

{format_guide}

Topic: {topic}
{product_block}
Output language: {language_name}. Every field (title, caption, hashtags, narration, on_screen_text) must be in {language_name}, with correct diacritics.
Target spoken length: {duration} seconds, which is about {syllables} spoken syllables in total across all scenes ({rate} syllables per second). Stay within 10% of that.

Structure:
- Scene 1 is the hook: the first sentence must make a scrolling viewer stop within 2 seconds. A bold claim, a
  question that splits opinions, or a specific "wait, what?" detail. No greeting, no introduction, no "hôm nay".
- The middle scenes are the body: one beat per scene, concrete and specific, each beat raising the stakes or the
  surprise over the last one.
- The last scene carries the payoff or punchline and a call to action that invites a reply with an opinion
  (pick a side, name yours, tag the person it describes), not a generic "follow for more".
- Use {min_scenes} to {max_scenes} scenes in total.

Rules:
- No invented statistics, prices, dates, quotes or company claims. If a claim is not common knowledge, phrase it as an observation or a prediction instead of a fact.
- narration is what the voice says; write it the way a person talks to a friend, no emojis, no hashtags, no markdown.
- Every sentence of the narration is shown full-screen as it is spoken, so each sentence is at most 12 words and
  carries one idea. A new sentence is a new card: use short sentences to control the pace.
- on_screen_text is the scene's key phrase, at most 6 words, made of words that also appear in that scene's narration: those words are coloured on screen.
- visual_query is 3 to 6 English keywords for a stock-photo search that matches the scene, e.g. "person typing laptop office night". Always in English, even when the narration is in {language_name}. Describe the scene's subject and setting, not the on-screen text.
- title at most 90 characters, written as a hook itself. caption at most 300 characters, ending with the question you want answered in the comments.
- 3 to 8 hashtags, each starting with #, lowercase, without diacritics or spaces (e.g. #haihuoc #giadinh): a mix of one broad tag, two or three topic tags, and one tag people in {language_name} actually search for.

Return ONLY one JSON object, no prose and no code fences, with exactly this shape:
{{"title": "...", "caption": "...", "hashtags": ["#..."], "scenes": [{{"narration": "...", "on_screen_text": "...", "visual_query": "..."}}]}}
