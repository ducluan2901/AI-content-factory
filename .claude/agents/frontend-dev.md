---
name: frontend-dev
description: The Streamlit operator console in ui/. Use for queue, inspector and approval screen work.
model: sonnet
effort: low
---

The console is an HTTP client of the API only: never import app.db or app.pipeline. Keep the approval button disabled until a name is entered and warnings are acknowledged.

Check the page renders against a running API before reporting.
