"""Human approval, immutable source-group datasets and explicit model promotion."""
import hashlib
import json
from safety_app.domain.models import Annotation,DatasetVersion,DeploymentRecord,FrameRef,BoxProposalResult
class Labeling:
    def __init__(self,rt,detector=None,site_id='local'):self.rt=rt;self.detector=detector;self.site_id=site_id
    def frame_scope(self,frame_id):
        frame=self.rt.db.get('frame',frame_id);media=self.rt.db.get('media',frame['media_id'])
        if self.rt.camera(media['camera_id']).site_id!=self.site_id:raise ValueError('Label frame outside operator site')
        return frame,media
    def protect_label_media(self,frame_id):
        # A label added to historical evidence makes its source visible again.
        frame=self.rt.db.get('frame',frame_id);media_id=frame['media_id']
        with self.rt.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("DELETE FROM trial_hidden_records WHERE kind='media' AND id=?",(media_id,))
            db.execute("DELETE FROM trial_hidden_records WHERE kind='frame' AND id IN (SELECT id FROM objects WHERE kind='frame' AND json_extract(body,'$.media_id')=?)",(media_id,))
    def annotate(self,annotation):
        frame,media=self.frame_scope(annotation.frame_id)
        if annotation.class_registry_version!=self.rt.registry.version:raise ValueError('Draft uses a different class registry version')
        if annotation.source_id!=media['source_id']:raise ValueError('Source ID must match the frame')
        if annotation.class_id not in {c.class_id for c in self.rt.registry.classes}:raise ValueError('Class is not in this registry')
        if annotation.approval_status!='draft' or annotation.reviewer_id:raise ValueError('Use the explicit approval operation')
        self.rt.db.save('annotation',annotation);self.protect_label_media(annotation.frame_id);return annotation
    def approve_annotations(self,ids,reviewer_id):
        rows=[Annotation.model_validate(self.rt.db.get('annotation',id)) for id in ids]
        for a in rows:self.frame_scope(a.frame_id)
        for a in rows:
            a.approval_status='approved';a.reviewer_id=reviewer_id;self.rt.db.save('annotation',a);self.protect_label_media(a.frame_id)
        self.rt.audit.write('labels_approved',provenance={'annotation_ids':ids,'reviewer_id':reviewer_id});return rows
    def create_dataset_version(self,ids,groups):
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate annotations')
        rows=[Annotation.model_validate(self.rt.db.get('annotation',id)) for id in ids]
        if not rows or any(a.approval_status!='approved' for a in rows):raise ValueError('Dataset requires approved annotations')
        frames={a.frame_id:self.rt.db.get('frame',a.frame_id) for a in rows}
        sources={a.source_id for a in rows}
        for a in rows:
            self.frame_scope(a.frame_id)
            if a.class_registry_version!=self.rt.registry.version:raise ValueError('Mixed class registry versions')
        if set(groups)!=sources:raise ValueError('Assign each source video to exactly one split')
        if set(groups.values())!={'train','valid','test'}:raise ValueError('Provide independent train, valid and test source videos')
        for split in ('train','valid','test'):
            if {a.class_id for a in rows if groups[a.source_id]==split}!={c.class_id for c in self.rt.registry.classes}:raise ValueError('Each split requires approved examples of all preserved classes')
        # Hash-identical files cannot occur on different sides of a split.
        hashes={}
        for a in rows:
            media=self.rt.db.get('media',frames[a.frame_id]['media_id']);digest=media['content_hash']
            if digest in hashes and hashes[digest]!=groups[a.source_id]:raise ValueError('Duplicate video crosses dataset splits')
            hashes[digest]=groups[a.source_id]
        version=hashlib.sha256(json.dumps({'labels':[a.model_dump(mode='json') for a in rows],'splits':groups,'classes':self.rt.registry.model_dump()},sort_keys=True).encode()).hexdigest()
        dataset=DatasetVersion(id='dataset-'+version,version=version,class_registry_version=self.rt.registry.version,sample_ids=ids,split_manifest=groups,provenance={'annotations':[a.model_dump(mode='json') for a in rows],'source_hashes':hashes,'split_unit':'source_video','related_sessions':'Assign related clips to the same split before import.'})
        self.rt.db.save('dataset',dataset,immutable=True);self.rt.audit.write('dataset_created',output=dataset.model_dump(mode='json'));return dataset
    def propose_boxes(self,frame_id,class_ids):
        import cv2
        import numpy as np
        if self.detector is None:
            from safety_app.modules.monitoring.detector import RFDETRDetector
            self.detector=RFDETRDetector()
        frame=self.rt.db.get('frame',frame_id);media=self.rt.db.get('media',frame['media_id']);image=cv2.imdecode(np.frombuffer(self.rt.media.image(frame_id),np.uint8),cv2.IMREAD_COLOR);h,w=image.shape[:2]
        detections=self.detector.predict(image,.5);names={c.name:c.class_id for c in self.rt.registry.classes};rows=[];scores=[]
        for i,box in enumerate(detections.xyxy):
            name=self.detector.labels[int(detections.class_id[i])]
            if name in names and names[name] in class_ids:rows.append(Annotation(frame_id=frame_id,source_id=media['source_id'],class_id=names[name],bbox=tuple(float(v) for v in box/[w,h,w,h]),origin='detector',class_registry_version=self.rt.registry.version));scores.append(float(detections.confidence[i]))
        return BoxProposalResult(candidates=rows,scores=scores,detector_version=str(self.detector.provenance),warnings=['Draft boxes. Human approval is required.'])
    async def promote_model(self,model_id,evaluation_id,cameras,reviewer_id,pipeline=None):
        model=self.rt.db.get('model',model_id);evaluation=self.rt.db.get('model_evaluation',evaluation_id)
        if evaluation['model_id']!=model_id or evaluation['status']!='completed' or not evaluation.get('passed'):raise ValueError('This model needs a passing held-out evaluation')
        for camera in cameras:self.rt.camera(camera)
        if not cameras:raise ValueError('Select a camera scope')
        # Only registered checkpoints from the existing trainer can become active.
        from app.cv.models import resolve_model
        registration=self.rt.db.get('model_registration',model_id)
        resolve_model(self.rt.root,registration['registered_ref'])
        previous=self.rt.db.list('deployment');record=DeploymentRecord(model_id=model_id,evaluation_id=evaluation_id,camera_scope=cameras,reviewer_id=reviewer_id,previous_model_id=previous[-1]['model_id'] if previous else None)
        if pipeline:
            try:self.rt.db.get('detector_default','baseline')
            except KeyError:self.rt.db.save('detector_default',{'id':'baseline','detector':pipeline.config.detector,'custom_model_id':pipeline.config.custom_model_id},immutable=True)
            record.previous_detector_config={'detector':pipeline.config.detector,'custom_model_id':pipeline.config.custom_model_id}
            await pipeline.update_config({'detector':'custom','custom_model_id':registration['registered_ref']})
        self.rt.audit.write('model_promoted',output=record.model_dump(mode='json'));self.rt.db.save('deployment',record,immutable=True);return record
