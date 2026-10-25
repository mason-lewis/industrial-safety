import json
from pathlib import Path
import pytest

from app.trainer.cli import dataset

def fixture(tmp_path):
    root=tmp_path/'prepared';(root/'images').mkdir(parents=True)
    images=[];frames=[];annotations=[]
    for id,source in enumerate(['a','b','c'],1):
        name=f'{source}.jpg';(root/'images'/name).write_bytes(b'fixture')
        images.append({'id':id,'file_name':name,'width':100,'height':100})
        frames.append({'image_id':id,'file_name':name,'source_sha256':source})
        for category in [1,2]:annotations.append({'id':len(annotations)+1,'image_id':id,'category_id':category,'bbox':[1,2,10,20],'area':200,'iscrowd':0})
    manifest={'classes':['person','forklift'],'frames':frames,'sources':[{'sha256':s} for s in ['a','b','c']]}
    data={'images':images,'categories':[{'id':1,'name':'person'},{'id':2,'name':'forklift'}],'annotations':annotations}
    (root/'manifest.json').write_text(json.dumps(manifest));labels=root/'labels.json';labels.write_text(json.dumps(data));splits=root/'splits.json';splits.write_text(json.dumps({'a':'train','b':'valid','c':'test'}))
    return root,labels,splits,data,manifest

def test_requires_human_review_and_rejects_empty_annotations(tmp_path):
    root,labels,splits,data,manifest=fixture(tmp_path)
    with pytest.raises(ValueError,match='Human review'):dataset.split_dataset(root,labels,splits,tmp_path/'out',False)
    data['annotations']=[]
    with pytest.raises(ValueError,match='No human'):dataset.validate_annotations(data,manifest,root)

def test_source_clips_stay_in_one_partition(tmp_path):
    root,labels,splits,_,_=fixture(tmp_path)
    output=tmp_path/'out';assert dataset.split_dataset(root,labels,splits,output,True)=={'train':1,'valid':1,'test':1}
    for split,name in [('train','a.jpg'),('valid','b.jpg'),('test','c.jpg')]:
        data=json.loads((output/split/'_annotations.coco.json').read_text());assert [i['file_name'] for i in data['images']]==[name];assert len(data['annotations'])==2
    assert json.loads((output/'split-provenance.json').read_text())['test_present']

def test_rejects_frame_based_split_and_invalid_boxes(tmp_path):
    root,labels,splits,data,manifest=fixture(tmp_path)
    splits.write_text(json.dumps({'a.jpg':'train','b.jpg':'valid','c.jpg':'test'}))
    with pytest.raises(ValueError,match='entire source'):dataset.split_dataset(root,labels,splits,tmp_path/'out',True)
    data['annotations'][0]['bbox']=[1,1,200,10]
    with pytest.raises(ValueError,match='inside'):dataset.validate_annotations(data,manifest,root)
    data['annotations'][0]['bbox']=[float('nan'),1,2,3]
    with pytest.raises(ValueError,match='inside'):dataset.validate_annotations(data,manifest,root)

def test_refuses_missing_class_examples_in_validation(tmp_path):
    root,labels,splits,data,_=fixture(tmp_path)
    data['annotations']=[a for a in data['annotations'] if not(a['image_id']==2 and a['category_id']==2)];labels.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='valid needs'):dataset.split_dataset(root,labels,splits,tmp_path/'out',True)
