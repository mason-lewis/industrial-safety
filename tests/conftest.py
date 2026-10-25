"""Tests use local modules and never opt into hosted model requests."""
import os
import sys
from pathlib import Path
import pytest

os.environ["AI_ENABLED"] = "false"
os.environ["AI_ALWAYS_ON"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


@pytest.fixture(autouse=True)
def disable_real_model_requests(monkeypatch):
    from pydantic_ai import models
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def isolated_legacy_cost_guard(tmp_path,monkeypatch,request):
    """Serialization fixtures use explicit fictional prices and a local ledger.

    Their older, larger packets test wire formatting. Production defaults are
    tested separately under backend/tests; no real provider is enabled here.
    """
    import json,shutil
    from safety_app.infrastructure.runtime import Runtime
    root=tmp_path/'guard-runtime'
    shutil.copytree(Path(__file__).resolve().parents[1]/'configs',root/'configs')
    rt=Runtime(root)
    if 'serialization' in request.node.name or 'http_payload' in request.node.name:
        rt.config['pricing']['gpt-6-luna']={'version':'fictional-test-price','input_per_million':.01,'output_per_million':.01,'cached_input_per_million':.001,'image_usd':.00001}
        rt.config['limits'].update(frames=96,input_tokens=120000,output_tokens=9000,output_per_request=3000)
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
