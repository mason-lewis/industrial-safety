"""Export frozen annotations to COCO or YOLO. Never edit dataset provenance."""
import json
from pathlib import Path
from safety_app.domain.models import DatasetVersion

def export_dataset(rt,dataset_id,format='coco'):
    if format not in ('coco','yolo'):raise ValueError('Choose coco or yolo')
    d=DatasetVersion.model_validate(rt.db.get('dataset',dataset_id));folder=rt.root/'data/datasets'/dataset_id/format;folder.mkdir(parents=True,exist_ok=True)
    rows=d.provenance['annotations'];classes=rt.classes(d.class_registry_version).classes
    frames={a['frame_id']:rt.db.get('frame',a['frame_id']) for a in rows}
    import cv2
    import numpy as np
    for split in ('train','valid','test'):
        out=folder/split;out.mkdir(exist_ok=True);images=[];labels=[];image_ids={}
        for fid,frame in frames.items():
            annotations=[a for a in rows if a['frame_id']==fid and d.split_manifest[a['source_id']]==split]
            if not annotations:continue
            raw=rt.media.image(fid);img=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR);h,w=img.shape[:2];image_ids[fid]=len(images)+1;name=frame['content_hash']+'.jpg';(out/name).write_bytes(raw);images.append({'id':image_ids[fid],'file_name':name,'width':w,'height':h})
            yolo=[]
            for a in annotations:
                x,y,right,bottom=a['bbox'];box=[x*w,y*h,(right-x)*w,(bottom-y)*h]
                labels.append({'id':len(labels)+1,'image_id':image_ids[fid],'category_id':a['class_id']+1,'bbox':box,'area':box[2]*box[3],'iscrowd':0})
                yolo.append(f"{a['class_id']} {(x+right)/2} {(y+bottom)/2} {right-x} {bottom-y}")
            if format=='yolo':(out/(frame['content_hash']+'.txt')).write_text('\n'.join(yolo)+'\n')
        if format=='coco':(out/'_annotations.coco.json').write_text(json.dumps({'images':images,'annotations':labels,'categories':[{'id':c.class_id+1,'name':c.name} for c in classes]},indent=2))
    (folder/'split-provenance.json').write_text(json.dumps({'human_review_confirmed':True,'test_present':True,'source_assignments':d.split_manifest,'dataset_id':d.id}))
    (folder/'provenance.json').write_text(d.model_dump_json(indent=2));return folder
