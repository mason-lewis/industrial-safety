from fastapi.testclient import TestClient
from app.pipeline import Pipeline

def test_idle_websocket_disconnect_releases_subscription(tmp_path,monkeypatch):
    from app import main
    monkeypatch.setenv('AI_ENABLED','false')
    lab=Pipeline(tmp_path)
    monkeypatch.setattr(main,'pipeline',lab)
    with TestClient(main.app) as client:
        with client.websocket_connect('/api/ws') as socket:
            state=socket.receive_json()
            assert state['type']=='state'
            assert state['ai']['model_requests']==0
        assert client.get('/api/state').status_code==200
    assert not lab.subscribers
