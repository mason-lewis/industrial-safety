from fastapi.testclient import TestClient
from app.pipeline import Pipeline
from app.models.contracts import RuleCandidate


def test_startup_health_source_refresh_and_manual_actions_are_cv_only(tmp_path,monkeypatch):
    from app import main
    monkeypatch.setenv("AI_ENABLED","false")
    monkeypatch.setenv("OPENAI_API_KEY","nonsecret-test-key")
    monkeypatch.setenv("RTSP_URL","rtsp://user:credential@camera.invalid/stream")
    lab=Pipeline(tmp_path)
    monkeypatch.setattr(main,"pipeline",lab)
    ctx=dict(source_id="synthetic",camera_id="cam",session_id="s",run_generation=0,schema_version="1.0")
    lab.session=ctx | {"mode":"offline","timestamp_ms":100}
    candidate=RuleCandidate(**ctx,id="e",kind="zone_dwell",timestamp_ms=100,start_ms=0,evidence_cutoff_ms=100)
    lab.repository.save("candidate",candidate)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["ai"]["enabled"] is False
        state=client.get("/api/state").json()
        assert state["metrics"]["model_requests"]==0
        assert state["ai"]["model_requests"]==0
        assert "credential" not in client.post("/api/sources/refresh").text
        assert "rtsp://" not in client.get("/api/sources").text
        review=client.post("/api/events/e/review")
        assert review.status_code==400
        assert "Enable AI" in review.json()["detail"]
        incident=client.post("/api/incidents",json={"timestamp_ms":100,"pre_ms":100,"post_ms":100})
        assert incident.status_code==200
        response=client.post("/api/incidents/"+incident.json()["id"]+"/review")
        assert response.status_code==400
        assert "AI disabled" in response.json()["detail"]
        assert lab.review.total_requests==0
        assert client.post("/api/sessions",json={"source_id":"/etc/passwd"}).status_code==400
        assert client.get("/api/evidence/unknown/image").status_code==404
        assert client.put("/api/config",json={"secret":"invalid"}).status_code==400


def test_source_discovery_rejects_symlink_escape_and_has_stable_ids(tmp_path):
    lab=Pipeline(tmp_path)
    folder=tmp_path/"data/input"
    folder.mkdir(parents=True)
    (folder/"clip.mp4").write_bytes(b"not-a-video-discovery-only")
    outside=tmp_path/"outside.mp4"
    outside.write_bytes(b"outside")
    (folder/"escape.mp4").symlink_to(outside)
    first=lab.discover()
    second=lab.discover()
    assert first==second
    assert [s["name"] for s in first if s["kind"]=="file"]==["clip.mp4"]


def test_local_dataset_catalog_and_download(tmp_path,monkeypatch):
    import json
    from app import main
    lab=Pipeline(tmp_path);monkeypatch.setattr(main,'pipeline',lab)
    folder=tmp_path/'data/training/industrial-v1';folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'frames':[{'image_id':1}],'sources':[{'sha256':'a'}],'classes':['person','forklift'],'status':'unlabeled'}))
    (folder/'labeling-bundle.zip').write_bytes(b'PKfixture')
    with TestClient(main.app) as client:
        catalog=client.get('/api/cv/datasets');assert catalog.status_code==200 and catalog.json()[0]['images']==1
        download=client.get('/api/cv/datasets/industrial-v1/download');assert download.status_code==200 and download.content==b'PKfixture'
        assert client.get('/api/cv/datasets/nonexistent/download').status_code==404
        assert client.get('/api/cv/models').json()==[]
