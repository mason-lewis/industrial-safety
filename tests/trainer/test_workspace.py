import asyncio
import json
from pathlib import Path
import shutil
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app.trainer.workspace import LearningWorkspace
from app.trainer import api as learning_api
from app.pipeline import Pipeline


def video(path,color=40):
    path.parent.mkdir(parents=True,exist_ok=True)
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),5,(64,48))
    for i in range(10):writer.write(np.full((48,64,3),color+i,np.uint8))
    writer.release();return path

def ready(ws,tmp_path):
    p=ws.create('Independent synthetic test',['person','forklift'])
    for n,split in enumerate(['train','valid','test']):
        path=video(tmp_path/f'{split}.mp4',40+n*60)
        p=ws.import_clip(p['id'],path,path.name,f'independent-{n}',split);clip=p['clips'][-1]
        p=ws.sample(p['id'],clip['id'],1,1);f=p['frames'][-1]
        p=ws.label(p['id'],f['id'],[{'id':'person','label':'person','bbox':[.1,.1,.2,.3]},{'id':'forklift','label':'forklift','bbox':[.5,.3,.3,.3]}],True,False)
    return p

def test_rejects_analysis_video_and_renamed_duplicate(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('new',['person','forklift'])
    original=video(tmp_path/'data/input/analysis.mp4');copy=tmp_path/'renamed.mp4';shutil.copy2(original,copy)
    with pytest.raises(ValueError,match='analysis library'):ws.import_clip(p['id'],copy,'renamed.mp4','group','train')
    assert ws.read(p['id'])['clips']==[] and copy.exists()


def test_group_split_leakage_and_duplicate_sources_are_rejected(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('new',['person','forklift']);a=video(tmp_path/'one.mp4');b=tmp_path/'duplicate.mp4';shutil.copy2(a,b)
    p=ws.import_clip(p['id'],a,'one.mp4','Session A','train')
    with pytest.raises(ValueError,match='already'):ws.import_clip(p['id'],b,'duplicate.mp4','different','valid')
    c=video(tmp_path/'other.mp4',120)
    with pytest.raises(ValueError,match='same split'):ws.import_clip(p['id'],c,'other.mp4','session a','test')


def test_unlabeled_images_are_not_negative_examples(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('new',['person','forklift']);p=ws.import_clip(p['id'],video(tmp_path/'clip.mp4'),'clip.mp4','group','train');p=ws.sample(p['id'],p['clips'][0]['id'],1,2);f=p['frames'][0]
    assert not f['reviewed'] and not ws.check(p)['ready']
    with pytest.raises(ValueError,match='Explicitly confirm'):ws.label(p['id'],f['id'],[],True,False)
    p=ws.label(p['id'],f['id'],[],True,True);assert p['frames'][0]['verified_empty']
    with pytest.raises(ValueError,match='positive size'):ws.label(p['id'],f['id'],[{'id':'a','label':'forklift','bbox':[.9,.1,.3,.2]}],True,False)
    with pytest.raises(ValueError,match='classes'):ws.label(p['id'],f['id'],[{'id':'a','label':'pizza','bbox':[.1,.1,.3,.2]}],True,False)


def test_freeze_keeps_whole_sources_separate_and_refuses_mutation(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ready(ws,tmp_path);assert ws.check(p)['ready'];p=ws.freeze(p['id'])
    seen=set()
    for split in ['train','valid','test']:
        data=json.loads((ws.folder(p['id'])/'snapshot'/split/'_annotations.coco.json').read_text());names={i['file_name'] for i in data['images']};assert not names&seen;seen|=names
        assert {a['category_id'] for a in data['annotations']}=={1,2}
    with pytest.raises(ValueError,match='frozen'):ws.assign(p['id'],p['clips'][0]['id'],'changed','test')
    with pytest.raises(ValueError,match='frozen'):ws.label(p['id'],p['frames'][0]['id'],[],True,True)
    path=ws.folder(p['id'])/'snapshot/train/_annotations.coco.json';path.write_text('{}')
    with pytest.raises(ValueError,match='snapshot has changed'):ws.training_ready(p['id'])


def test_analysis_overlap_added_later_blocks_freeze_and_training(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ready(ws,tmp_path);p=ws.freeze(p['id']);clip=p['clips'][0]
    target=tmp_path/'data/input/new-analysis.mp4';target.parent.mkdir(parents=True);shutil.copy2(ws.folder(p['id'])/'sources'/clip['file'],target)
    with pytest.raises(ValueError,match='overlaps'):ws.training_ready(p['id'])


def test_api_upload_labels_and_freeze_readiness(tmp_path,monkeypatch):
    from app import main
    lab=Pipeline(tmp_path);monkeypatch.setattr(main,'pipeline',lab)
    raw=video(tmp_path/'external.mp4').read_bytes()
    with TestClient(main.app) as client:
        p=client.post('/api/learning/projects',json={'name':'External source project','classes':['person','forklift']}).json();url=f"/api/learning/projects/{p['id']}"
        upload=client.post(url+'/clips?name=outside.mp4&group=session-1&split=train',content=raw);assert upload.status_code==200
        cid=upload.json()['clips'][0]['id'];sampled=client.post(url+f'/clips/{cid}/sample',json={'interval_s':1,'max_frames':2});assert sampled.status_code==200
        f=sampled.json()['frames'][0];assert client.get(url+f"/frames/{f['id']}/image").status_code==200
        assert client.put(url+f"/frames/{f['id']}",json={'boxes':[],'reviewed':True}).status_code==400
        assert client.post(url+'/freeze',json={}).status_code==400
        assert client.get('/api/sources').json()==[] # External footage is not discoverable as analysis input.

@pytest.mark.asyncio
async def test_training_input_omits_test_and_evaluation_seals_training(tmp_path,monkeypatch):
    from app import main
    lab=Pipeline(tmp_path);monkeypatch.setattr(main,'pipeline',lab);ws=learning_api.workspace();p=ready(ws,tmp_path);p=ws.freeze(p['id']);monkeypatch.setattr(ws,'dependencies',lambda:{'ready':True})
    commands=[]
    async def fake_monitor(ws,id,job,command,cwd):
        commands.append(command)
        with ws.lock:
            state=ws.read(id);next(j for j in state['jobs'] if j['id']==job['id'])['status']='completed';ws.write(state)
        learning_api.tasks.pop(job['id'],None)
    monkeypatch.setattr(learning_api,'monitor',fake_monitor)
    job=await learning_api.job_start(p['id'],learning_api.JobInput(kind='train',device='cpu'))
    await asyncio.sleep(.01)
    data=ws.folder(p['id'])/'jobs'/job['id']/'train-data';assert (data/'train').exists() and (data/'valid').exists() and not (data/'test').exists()
    assert not json.loads((data/'split-provenance.json').read_text())['test_present']
    output=ws.folder(p['id'])/'jobs'/job['id']/'output';output.mkdir();weights=output/'best.pth';weights.write_bytes(b'only-checkpoint-provenance-fixture')
    relative=str(weights.relative_to(ws.folder(p['id'])))
    await learning_api.job_start(p['id'],learning_api.JobInput(kind='evaluate',checkpoint=relative,device='cpu'));await asyncio.sleep(.01)
    assert ws.read(p['id'])['test_revealed']
    with pytest.raises(Exception,match='Test results have been opened'):await learning_api.job_start(p['id'],learning_api.JobInput(kind='train'))
    other=output/'other.pth';other.write_bytes(b'different')
    with pytest.raises(Exception,match='locked to the first'):await learning_api.job_start(p['id'],learning_api.JobInput(kind='evaluate',checkpoint=str(other.relative_to(ws.folder(p['id'])))))


def test_archiving_preserves_labels_and_excludes_frames_from_dataset(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ready(ws,tmp_path);c=p['clips'][0];p=ws.archive_clip(p['id'],c['id'])
    assert not any(f['clip_id']==c['id'] for f in p['frames'])
    archived=json.loads((ws.folder(p['id'])/'archived'/f"{c['id']}.json").read_text());assert archived['frames'][0]['boxes']
    assert (ws.folder(p['id'])/'sources'/c['file']).is_file()
    assert not ws.check(p)['ready']

@pytest.mark.asyncio
async def test_local_job_monitor_reports_exit_and_removes_provider_key(tmp_path,monkeypatch):
    import sys
    ws=LearningWorkspace(tmp_path);p=ws.create('process fixture',['person','forklift']);jid='a'*32
    job={'id':jid,'kind':'train','status':'queued'};p['jobs'].append(job);ws.write(p);folder=ws.folder(p['id'])/'jobs'/jid;folder.mkdir(parents=True)
    monkeypatch.setenv('OPENAI_API_KEY','nonsecret-fixture-key')
    command=[sys.executable,'-c',"import os; print('provider_key_removed=' + str(os.getenv('OPENAI_API_KEY') is None)); print('fixture only; no training')"]
    await learning_api.monitor(ws,p['id'],job,command,folder)
    state=ws.read(p['id']);assert state['jobs'][0]['status']=='completed'
    assert 'provider_key_removed=True' in (folder/'process.log').read_text()
    assert jid not in learning_api.processes


def test_full_clip_sampling_and_dense_append_preserve_reviewed_labels(tmp_path):
    ws=LearningWorkspace(tmp_path);p=ws.create('coverage',['person'])
    p=ws.import_clip(p['id'],video(tmp_path/'bias-forklift.mp4'),'bias-forklift.mp4','one','train');cid=p['clips'][0]['id']
    p=ws.sample(p['id'],cid,.5,2,'coverage')
    assert [f['timestamp_ms'] for f in p['frames']]==[0,1800]
    f=p['frames'][0];p=ws.label(p['id'],f['id'],[],True,True)
    before=p['frames'][0].copy();image=(ws.folder(p['id'])/'images'/f['file_name']).read_bytes()
    p=ws.sample(p['id'],cid,.5,3,'every_frame')
    assert p['frames'][0]==before and (ws.folder(p['id'])/'images'/f['file_name']).read_bytes()==image
    assert [f['timestamp_ms'] for f in p['frames'][2:]]==[200,400,600]
    p=ws.sample(p['id'],cid,.5,200,'every_frame')
    assert len(p['frames'])==10 and len({f['timestamp_ms'] for f in p['frames']})==10
    assert p['clips'][0]['sampling_history'][-1]['remaining']==0
    again=ws.sample(p['id'],cid,.5,200,'every_frame')
    assert again['frames']==p['frames'] and again['clips'][0]['sampling_history'][-1]['added']==0
    again['frozen']=True;ws.write(again)
    with pytest.raises(ValueError,match='frozen'):ws.sample(p['id'],cid,.5,10,'coverage')


def test_sampling_limits_real_added_count_and_audits_api(tmp_path,monkeypatch):
    from app import main
    monkeypatch.setattr(main,'pipeline',Pipeline(tmp_path));ws=learning_api.workspace();p=ws.create('capacity',['person'])
    p=ws.import_clip(p['id'],video(tmp_path/'source.mp4'),'source.mp4','one','train');cid=p['clips'][0]['id']
    p['frames']=[dict(id=str(i),clip_id='other',timestamp_ms=i) for i in range(999)];ws.write(p)
    with TestClient(main.app) as client:
        url=f"/api/learning/projects/{p['id']}/clips/{cid}/sample"
        response=client.post(url,json={'mode':'coverage','interval_s':.5,'max_frames':200})
        assert response.status_code==200 and len(response.json()['frames'])==1000
        assert client.post(url,json={'mode':'every_frame','interval_s':.5,'max_frames':200}).status_code==400
        assert client.post(url,json={'mode':'invented'}).status_code==422
    events=learning_api.learning_audit(ws).rows(100)
    assert {'learning_sampling_started','learning_sampling_finished','learning_sampling_failed'}<={r['event_type'] for r in events}
