# Official sources checked 2026-10-03

Installed exact versions are in uv.lock and frontend/package-lock.json. APIs are also inspected in installed packages.

- RF-DETR code/models/license: https://github.com/roboflow/rf-detr and https://rfdetr.roboflow.com/latest/faq/
- Trackers current ByteTrackTracker: https://github.com/roboflow/trackers
- Supervision tracking migration: https://supervision.roboflow.com/0.28.0/how_to/track_objects/
- PyAV PTS/time bases: https://pyav.org/docs/stable/api/time.html
- Pydantic AI OpenAIResponsesModel: https://pydantic.dev/docs/ai/models/openai/
- Image bytes: https://pydantic.dev/docs/ai/core-concepts/input/
- Shared delegate usage: https://pydantic.dev/docs/ai/guides/multi-agent-applications/
- Luna model: https://developers.openai.com/api/docs/models/gpt-6-luna

Luna availability for the user's account remains unverified. No model requests run during installation or ordinary tests. The default model is preserved, and reasoning effort/mode are omitted unless effort is explicitly configured. Hosted image review is separate from the local CV stack.
