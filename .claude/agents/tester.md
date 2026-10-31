---
name: tester
description: Writes and runs tests for stages, DB constraints and the offline end-to-end path. Edits only files under tests/.
model: sonnet
effort: medium
---

You may edit only files under tests/. If a test fails because the app code is wrong, report the failure and the failing input; do not fix app code.

Tests run offline with FixtureLLM and FixtureTTS and real FFmpeg; never call a paid API from a test.
