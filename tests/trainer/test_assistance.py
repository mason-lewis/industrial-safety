"""Local fixture checks for blind proposals, corrections, intake isolation and scoring."""
import asyncio
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
import supervision as sv
from fastapi.testclient import TestClient
from app.trainer.workspace import LearningWorkspace
from app.trainer import assistance, api
from app.pipeline import Pipeline


def video(path, color=60):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),5,(64,48))
    for i in range(10):writer.write(np.full((48,64,3),color+i,np.uint8))
    writer.release()
    return path


class Detector:
    labels={0:'person',1:'truck'}
    provenance={'weights_sha256':'fixture','device':'cpu','variant':'fixture'}
    def __init__(self):self.inputs=[]
    def predict(self,image,confidence):
        assert isinstance(image,np.ndarray) and image.ndim==3
        self.inputs.append(image.copy())
        return sv.Detections(xyxy=np.array([[6.4,4.8,19.2,19.2],[32,24,48,36]],dtype=float),
            class_id=np.array([0,1]),confidence=np.array([.9,.8]))


def sample(ws,tmp_path,classes=('person','truck'),split='train',name='unsafe_expected_behavior.mp4'):
    p=ws.create('fixture',list(classes))
    p=ws.import_clip(p['id'],video(tmp_path/name),name,'session',split)
    return ws.sample(p['id'],p['clips'][0]['id'],1,1)


def test_proposals_are_unapproved_and_require_explicit_decisions(tmp_path):
    ws=LearningWorkspace(tmp_path)
    p=sample(ws,tmp_path,('person','truck','forklift'))
    frame=p['frames'][0]
    p=assistance.assist_frame(ws,p['id'],frame['id'],Detector(),.25)
    f=p['frames'][0]
    assert not f['reviewed'] and f['boxes']==[]
    assert f['proposal_run']['unsupported_classes']==['forklift']
    boxes=[{k:b[k] for k in ('id','label','bbox')} for b in f['proposals']]
    with pytest.raises(ValueError,match='every proposal'):
        ws.label(p['id'],f['id'],boxes,True,False,{},1)
    decisions={b['id']:'accepted' for b in boxes}
    p=ws.label(p['id'],f['id'],boxes,True,False,decisions,1)
    assert p['frames'][0]['reviewed'] and p['review_history'][-1]['proposal_decisions']==decisions
    assert assistance.assist_frame(ws,p['id'],f['id'],Detector(),.5)['frames'][0]['boxes']==boxes
    with pytest.raises(ValueError,match='Frame changed'):
        ws.label(p['id'],f['id'],boxes,True,False,decisions,1)


def test_rejected_and_adjusted_proposals_are_preserved_outside_final_labels(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path)
    p=assistance.assist_frame(ws,p['id'],p['frames'][0]['id'],Detector(),.25);f=p['frames'][0]
    a,b=f['proposals'];box={k:a[k] for k in ('id','label','bbox')};box['bbox']=[.15,.1,.2,.3]
    with pytest.raises(ValueError,match='marked adjusted'):
        ws.label(p['id'],f['id'],[box],True,False,{a['id']:'accepted',b['id']:'rejected'})
    p=ws.label(p['id'],f['id'],[box],True,False,{a['id']:'adjusted',b['id']:'rejected'})
    assert len(p['frames'][0]['proposals'])==2 and len(p['frames'][0]['boxes'])==1
    assert p['review_history'][-1]['proposal_decisions'][b['id']]=='rejected'
    with pytest.raises(ValueError,match='removed'):
        ws.label(p['id'],f['id'],[{k:b[k] for k in ('id','label','bbox')}],True,False,{a['id']:'rejected',b['id']:'rejected'})


def test_reject_all_needs_explicit_empty_review(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path)
    p=assistance.assist_frame(ws,p['id'],p['frames'][0]['id'],Detector(),.25);f=p['frames'][0]
    decisions={b['id']:'rejected' for b in f['proposals']}
    with pytest.raises(ValueError,match='Explicitly confirm'):ws.label(p['id'],f['id'],[],True,False,decisions)
    assert ws.label(p['id'],f['id'],[],True,True,decisions)['frames'][0]['verified_empty']


def test_manual_labels_and_frozen_projects_cannot_be_overwritten(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path)
    f=p['frames'][0];ws.label(p['id'],f['id'],[],True,True)
    with pytest.raises(ValueError,match='human labels'):assistance.assist_frame(ws,p['id'],f['id'],Detector(),.25)
    state=ws.read(p['id']);state['frozen']=True;ws.write(state)
    with pytest.raises(ValueError,match='frozen'):assistance.assist_frame(ws,p['id'],f['id'],Detector(),.25)


def test_title_folder_metadata_and_category_do_not_enter_detector_or_scoring(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path,split='valid');f=p['frames'][0]
    detector=Detector()
    first,_=assistance.predict_boxes(ws,p['id'],f,p['classes'],detector,.5)
    signature=assistance.validation_signature(p)
    with ws.lock:
        p['clips'][0].update(name='safe_expected_person_missing.mp4',group='unsafe-category',
                             intake_source={'source_category':'forklift_overload','relative_path':'safe/test/expected.mp4'})
        ws.write(p)
    assert assistance.validation_signature(p)==signature
    second,_=assistance.predict_boxes(ws,p['id'],f,p['classes'],detector,.5)
    assert np.array_equal(detector.inputs[0],detector.inputs[1])
    assert [{k:b[k] for k in ('label','bbox','score')} for b in first]==[{k:b[k] for k in ('label','bbox','score')} for b in second]
    truth=[{k:first[0][k] for k in ('id','label','bbox')},dict(id='miss',label='person',bbox=[.7,.1,.1,.2])]
    ws.label(p['id'],f['id'],truth,True,False)
    report=assistance.validate_current(ws,p['id'],detector,.5,25)
    person,truck=report['classes']
    assert (person['true_positive'],person['false_negative'],person['recall'])==(1,1,.5)
    assert truck['false_positive']==1 and truck['recall'] is None
    assert 'source_category' not in json.dumps(report) and 'safe_expected' not in json.dumps(report)
    assert ws.read(p['id'])['validation_runs'][0]['id']==report['id']
    assert report['label_snapshot_sha256']==assistance.validation_signature(ws.read(p['id']))
    assert len(report['frame_image_sha256'][f['id']])==64


def test_changed_labels_flag_saved_validation_and_failure_saves_no_success(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path,split='valid');f=p['frames'][0]
    ws.label(p['id'],f['id'],[],True,True)
    report=assistance.validate_current(ws,p['id'],Detector(),.5,25)
    ws.label(p['id'],f['id'],[dict(id='new',label='person',bbox=[.1,.1,.2,.3])],True,False)
    assert report['label_snapshot_sha256']!=assistance.validation_signature(ws.read(p['id']))
    class Failure(Detector):
        def predict(self,image,confidence):raise RuntimeError('fixture detector failed')
    with pytest.raises(RuntimeError):assistance.validate_current(ws,p['id'],Failure(),.5,25)
    assert len(ws.read(p['id'])['validation_runs'])==1


def test_validation_excludes_unreviewed_and_test_and_keeps_duplicates_false(tmp_path):
    ws=LearningWorkspace(tmp_path);p=sample(ws,tmp_path,split='test');f=p['frames'][0]
    ws.label(p['id'],f['id'],[],True,True)
    with pytest.raises(ValueError,match='Validation recording'):assistance.validate_current(ws,p['id'],Detector(),.5,25)
    ws.assign(p['id'],p['clips'][0]['id'],'session','valid')
    class Duplicate(Detector):
        def predict(self,image,confidence):
            return sv.Detections(xyxy=np.array([[6.4,4.8,19.2,19.2]]*2),class_id=np.array([0,0]),confidence=np.array([.9,.8]))
    ws.label(p['id'],f['id'],[dict(id='truth',label='person',bbox=[.1,.1,.2,.3])],True,False)
    row=assistance.validate_current(ws,p['id'],Duplicate(),.5,1)['classes'][0]
    assert (row['true_positive'],row['false_positive'],row['precision'])==(1,1,.5)


def test_intake_copy_preserves_original_test_partition_and_confines_paths(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('objects',['person'])
    original=video(tmp_path/'data/cv-intake/incoming/test/unsafe/clip.mp4')
    before=original.read_bytes();row=assistance.intake_catalog(ws)[0]
    assert row['source_split']=='test' and row['source_category']=='unsafe'
    with pytest.raises(ValueError,match='stays held out'):assistance.import_intake(ws,p['id'],row['id'],'session','train')
    p=assistance.import_intake(ws,p['id'],row['id'],'session','test')
    assert original.read_bytes()==before and p['clips'][0]['intake_source']==row
    with pytest.raises(ValueError,match='stays held out'):ws.assign(p['id'],p['clips'][0]['id'],'other','valid')
    outside=video(tmp_path/'outside.mp4',100)
    (tmp_path/'data/cv-intake/incoming/escape.mp4').symlink_to(outside)
    assert len(assistance.intake_catalog(ws))==1
    with pytest.raises(ValueError,match='no longer'):assistance.import_intake(ws,p['id'],'../../outside.mp4','s','test')


def test_automatic_import_defaults_preserve_roles_without_behavior_based_groups(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('easy import',['person'])
    video(tmp_path/'data/cv-intake/incoming/collection/train/safe/a.mp4',40)
    video(tmp_path/'data/cv-intake/incoming/collection/train/unsafe/b.mp4',100)
    video(tmp_path/'data/cv-intake/incoming/collection/test/unsafe/c.mp4',150)
    rows=assistance.intake_catalog(ws)
    training=[r for r in rows if r['source_split']=='train'];testing=next(r for r in rows if r['source_split']=='test')
    assert training[0]['suggested_group']==training[1]['suggested_group']
    assert testing['collection_id']==training[0]['collection_id']
    for row in training:
        p=assistance.import_intake(ws,p['id'],row['id'])
    p=assistance.import_intake(ws,p['id'],testing['id'])
    assert [c['split'] for c in p['clips']]==['train','train','test']
    assert p['clips'][0]['group']==p['clips'][1]['group']
    assert all(c['group_basis']=='automatic_collection' for c in p['clips'])
    assert ws.check(p)['needs_group_review']
    with pytest.raises(ValueError,match='Add training, validation'):ws.confirm_recording_separation(p['id'])


def test_automatic_groups_require_review_and_assignment_changes_invalidate_it(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('three roles',['person'])
    for n,role in enumerate(('train','valid','test')):
        video(tmp_path/f'data/cv-intake/incoming/collection/{role}/clip.mp4',30+n*60)
    for row in assistance.intake_catalog(ws):
        p=assistance.import_intake(ws,p['id'],row['id'])
    assert ws.check(p)['needs_group_review']
    p=ws.confirm_recording_separation(p['id'])
    assert not ws.check(p)['needs_group_review']
    assert p['group_review_history'][-1]['assignment_sha256']==ws.grouping_signature(p)
    clip=p['clips'][0]
    p=ws.assign(p['id'],clip['id'],clip['group'],clip['split'])
    assert clip['group_basis']=='automatic_collection' and not ws.check(p)['needs_group_review']
    video(tmp_path/'data/cv-intake/incoming/collection/train/new.mp4',240)
    row=next(r for r in assistance.intake_catalog(ws) if r['name']=='new.mp4')
    p=assistance.import_intake(ws,p['id'],row['id'])
    assert ws.check(p)['needs_group_review']


def test_intake_api_accepts_recommended_defaults_without_group_or_role(tmp_path,monkeypatch):
    from app import main
    monkeypatch.setattr(main,'pipeline',Pipeline(tmp_path));ws=api.workspace();p=ws.create('defaults',['person'])
    video(tmp_path/'data/cv-intake/incoming/train/source.mp4')
    with TestClient(main.app) as client:
        row=client.get('/api/learning/intake').json()[0]
        response=client.post(f"/api/learning/projects/{p['id']}/intake",json={'intake_id':row['id']})
        assert response.status_code==200
        assert response.json()['clips'][0]['group']==row['suggested_group']
        assert response.json()['clips'][0]['split']=='train'
        rows=api.learning_audit(ws).rows(100)
        assert {'learning_intake_import_requested','learning_intake_import_completed'}<={r['event_type'] for r in rows}

def test_intake_usage_persists_imported_roles_review_counts_and_other_projects(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('first',['person'])
    original=video(tmp_path/'data/cv-intake/incoming/train/unsafe_expected_forklift/a.mp4',80)
    row=assistance.intake_catalog(ws)[0];assert row['imported_in']==[]
    p=assistance.import_intake(ws,p['id'],row['id'])
    p=ws.sample(p['id'],p['clips'][0]['id'],1,1);f=p['frames'][0]
    ws.label(p['id'],f['id'],[],True,True)
    usage=assistance.intake_catalog(LearningWorkspace(tmp_path))[0]['imported_in']
    assert usage==[dict(project_id=p['id'],clip_id=p['clips'][0]['id'],split='train',frames=1,reviewed=1)]
    ws.assign(p['id'],p['clips'][0]['id'],'independent-validation','valid')
    assert assistance.intake_catalog(ws)[0]['imported_in'][0]['split']=='valid'
    other=ws.create('other',['person'])
    assert assistance.intake_catalog(ws)[0]['imported_in'][0]['project_id']!=other['id']
    with pytest.raises(ValueError,match='already in a learning project'):assistance.import_intake(ws,other['id'],row['id'])
    assert original.is_file()


def test_general_classes_reopen_review_and_frozen_exports_use_only_human_boxes(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('single class',['person'])
    for n,split in enumerate(('train','valid','test')):
        p=ws.import_clip(p['id'],video(tmp_path/f'{split}.mp4',30+50*n),f'{split}.mp4',split,split)
        p=ws.sample(p['id'],p['clips'][-1]['id'],1,1);f=p['frames'][-1]
        p=assistance.assist_frame(ws,p['id'],f['id'],Detector(),.25);f=p['frames'][-1];proposal=f['proposals'][0]
        p=ws.label(p['id'],f['id'],[{k:proposal[k] for k in ('id','label','bbox')}],True,False,{proposal['id']:'accepted'})
    assert ws.check(p)['ready']
    p=ws.update_classes(p['id'],['person','crate'])
    assert all(not f['reviewed'] for f in p['frames'])
    for f in p['frames']:
        p=ws.label(p['id'],f['id'],f['boxes']+[dict(id='crate',label='crate',bbox=[.5,.5,.2,.2])],True,False,f['proposal_decisions'])
    p=ws.freeze(p['id'])
    data=json.loads((ws.folder(p['id'])/'snapshot/train/_annotations.coco.json').read_text())
    assert {c['name'] for c in data['categories']}=={'person','crate'}
    assert len(data['annotations'])==2 and 'unsafe' not in json.dumps(data)
    with pytest.raises(ValueError,match='frozen'):ws.update_classes(p['id'],['person'])


def test_api_proposals_and_validation_use_typed_requests(tmp_path,monkeypatch):
    from app import main
    monkeypatch.setattr(main,'pipeline',Pipeline(tmp_path));ws=api.workspace();p=sample(ws,tmp_path,split='valid')
    monkeypatch.setattr(api,'proposal_detector',lambda:Detector())
    with TestClient(main.app) as client:
        url=f"/api/learning/projects/{p['id']}";fid=p['frames'][0]['id']
        assert client.post(url+f'/frames/{fid}/assist',json={'confidence':.25,'expected_behavior':'unsafe'}).status_code==422
        f=client.post(url+f'/frames/{fid}/assist',json={}).json()['frames'][0]
        body=dict(boxes=[{k:b[k] for k in ('id','label','bbox')} for b in f['proposals']],reviewed=True,
                  expected_revision=f['revision'],proposal_decisions={b['id']:'accepted' for b in f['proposals']})
        assert client.put(url+f'/frames/{fid}',json=body).status_code==200
        assert client.put(url+f'/frames/{fid}',json=body).status_code==400
        assert client.post(url+'/validate',json={}).json()['classes'][0]['recall']==1


@pytest.mark.asyncio
async def test_cancellation_joins_local_work_before_releasing_inference_lock():
    import threading
    started=threading.Event();finish=threading.Event()
    def native():started.set();finish.wait(2);return 'done'
    task=asyncio.create_task(api.local_work(native))
    await asyncio.to_thread(started.wait,1)
    task.cancel();await asyncio.sleep(.01)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
