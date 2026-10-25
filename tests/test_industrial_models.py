import hashlib
import json
import pytest
from app.cv.models import resolve_model,model_catalog


def registered(root):
    folder=root/'data/artifacts/models';folder.mkdir(parents=True)
    weights=folder/'industrial.pth';weights.write_bytes(b'synthetic checksum fixture, not actual model weights')
    manifest={'id':'industrial','format':'rfdetr-checkpoint','weights':weights.name,'sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'classes':['person','forklift'],'license':'fixture license'}
    (folder/'industrial.detector.json').write_text(json.dumps(manifest));return weights,manifest


def test_registered_model_identity_and_checksum_are_not_accuracy_claims(tmp_path):
    weights,_=registered(tmp_path)
    _,path,labels=resolve_model(tmp_path,'industrial')
    assert path==weights and labels=={0:'person',1:'forklift'}
    assert model_catalog(tmp_path)[0]['status']=='registered; compatibility checked on activation'
    weights.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):resolve_model(tmp_path,'industrial')


def test_registry_rejects_path_escape_and_missing_license(tmp_path):
    _,manifest=registered(tmp_path)
    with pytest.raises(ValueError):resolve_model(tmp_path,'../industrial')
    manifest['weights']='../../outside.pth'
    (tmp_path/'data/artifacts/models/industrial.detector.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):resolve_model(tmp_path,'industrial')
