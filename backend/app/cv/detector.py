"""Real inference only. Model loading is lazy; missing weights never fabricate detections."""
from collections import defaultdict, deque
import hashlib
import importlib.metadata
import importlib.util
import logging
import os
from pathlib import Path
import cv2
import numpy as np

CAPABILITIES = [
 {'id':'person_zone_dwell','state':'supported','reason':'COCO person detections and source-time configured polygon rule; requires site validation'},
 {'id':'directional_crossing','state':'supported','reason':'Image-plane person track crossing; requires site validation'},
 *[{'id':x,'state':'unavailable','reason':'Standard COCO labels do not support this safety class; custom weights and site validation required'} for x in ['forklift','hard_hat','safety_vest','fire','smoke','spill','fall','injury']],
 {'id':'scene_review','state':'experimental','reason':'Optional sampled AI suggestions; no validated hazard detector'},
 {'id':'physical_distance','state':'unavailable','reason':'Camera calibration has not been supplied'}]

class RFDETRDetector:
    def __init__(self,variant='small',weights=None,class_map=None,device='auto'):
        if variant not in ('small','nano','custom'): raise ValueError('Only Small/Nano Apache weights or explicit custom adapter allowed')
        if variant == 'custom' and (not weights or not class_map): raise ValueError('Custom weights need an explicit class map')
        # The core player/reviewer does not require RF-DETR or its training stack.
        self.labels=class_map or {}
        if importlib.util.find_spec('rfdetr'):
            from rfdetr.assets.coco_classes import COCO_CLASSES
            self.labels=class_map or COCO_CLASSES
        self.requested_device=device; self.variant=variant; self.weights=weights; self.class_map=class_map; self.model=None; self.device=None; self.provenance={}
    def load(self):
        if not importlib.util.find_spec('rfdetr'):
            raise RuntimeError('Computer vision is optional. Install with ./scripts/uv sync --extra cv to enable it.')
        import torch
        import rfdetr
        from rfdetr.assets.coco_classes import COCO_CLASSES
        self.device = self.requested_device if self.requested_device!='auto' else 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
        cls = rfdetr.RFDETRNano if self.variant=='nano' else rfdetr.RFDETRSmall
        cache=Path(__file__).resolve().parents[3]/'data/artifacts/models'
        cache.mkdir(parents=True,exist_ok=True)
        kw={'device':self.device,'pretrain_weights':str(cache/f'rf-detr-{self.variant}.pth')}
        os.environ.setdefault('TORCH_HOME',str(cache/'torch'))
        os.environ.setdefault('HF_HOME',str(cache/'huggingface'))
        if self.weights: kw['pretrain_weights']=self.weights
        def build():
            if self.variant=='custom':
                # Infer trained head and architecture. Never resize the head to match a guessed map.
                model=rfdetr.RFDETR.from_checkpoint(self.weights,device=self.device,trust_checkpoint=False)
                expected={i:name for i,name in enumerate(model.class_names)}
                if self.class_map!=expected: raise ValueError('Custom class map must exactly match the checkpoint class names and zero-based IDs')
                return model
            return cls(**kw)
        try: self.model=build()
        except (RuntimeError,NotImplementedError):
            if self.device=='cpu': raise
            self.device='cpu'; kw['device']='cpu'; self.model=build()
        self.labels=self.class_map or COCO_CLASSES
        from rfdetr.assets.model_weights import ModelWeights
        asset=ModelWeights.from_filename(f'rf-detr-{self.variant}.pth') if self.variant!='custom' else None
        self.provenance={'variant':self.variant,'device':self.device,'version':importlib.metadata.version('rfdetr'),'code_license':'Apache-2.0','weights_license':'Apache-2.0' if asset else getattr(self,'custom_license','user supplied; verify license'),'weights_source':asset.url if asset else 'custom local weights','expected_md5':asset.md5_hash if asset else None,'class_map':self.labels}
        weight_path=Path(self.model.model_config.pretrain_weights)
        self.provenance['weights_sha256']=hashlib.sha256(weight_path.read_bytes()).hexdigest() if weight_path.is_file() else None
        logging.getLogger(__name__).info('Loaded RF-DETR %s on %s; weights SHA256 %s',self.variant,self.device,self.provenance['weights_sha256'])
    def predict(self,image,confidence):
        if self.model is None: self.load()
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        try: return self.model.predict(rgb,threshold=confidence)
        except (RuntimeError,NotImplementedError):
            if self.device=='cpu': raise
            self.requested_device='cpu'; self.load()
            return self.model.predict(rgb,threshold=confidence)

class ByteTrackAdapter:
    """Class-separated association prevents a person inheriting a vehicle's track ID."""
    def __init__(self,frame_rate=5,config=None):
        self.factory=None
        if importlib.util.find_spec('trackers'):
            from trackers import ByteTrackTracker
            self.factory=ByteTrackTracker
        self.options=dict(frame_rate=frame_rate,track_activation_threshold=getattr(config,'tracker_activation',.5),
            high_conf_det_threshold=getattr(config,'tracker_high_confidence',.6),
            minimum_iou_threshold=getattr(config,'tracker_match_iou',.1),
            minimum_consecutive_frames=getattr(config,'tracker_min_frames',2),
            lost_track_buffer=round(getattr(config,'tracker_lost_seconds',1)*30))
        self.trackers={};self.ids={};self.next_id=0;self.trails=defaultdict(lambda:deque(maxlen=24))
    def update(self,detections,timestamp=None):
        if self.factory is None:
            raise RuntimeError('Tracking is optional. Install with ./scripts/uv sync --extra cv to enable it.')
        import supervision as sv
        outputs=[];alive=set()
        for class_id in set(self.trackers)|set(int(i) for i in detections.class_id):
            if class_id not in self.trackers:self.trackers[class_id]=self.factory(**self.options)
            tracker=self.trackers[class_id]
            part=detections[detections.class_id==class_id]
            tracked=tracker.update(part,timestamp=timestamp)
            alive.update((class_id,t.tracker_id) for t in tracker.tracks if t.tracker_id>=0)
            tracked=tracked[tracked.tracker_id>=0]
            if len(tracked):
                mapped=[]
                for local in tracked.tracker_id:
                    if local<0:mapped.append(-1);continue
                    key=(class_id,int(local))
                    if key not in self.ids:self.ids[key]=self.next_id;self.next_id+=1
                    mapped.append(self.ids[key])
                tracked.tracker_id=np.array(mapped,dtype=int);outputs.append(tracked)
        self.ids={key:id for key,id in self.ids.items() if key in alive}
        result=sv.Detections.merge(outputs) if outputs else sv.Detections.empty()
        if result.tracker_id is None:result.tracker_id=np.array([],dtype=int)
        # Retain trail through short occlusion, but never emit predicted boxes as fresh observations.
        retained=set(self.ids.values())
        for id in list(self.trails):
            if id not in retained:del self.trails[id]
        return result
