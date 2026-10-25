"""Separate human-labeling projects, immutable source-group splits and local training jobs."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from app.video.sources import FileReplaySource

CLASSES=['person','forklift','reach truck','pallet jack','car','truck','bus']
SPLITS={'train','valid','test'}

class LearningWorkspace:
    def __init__(self,root):
        self.root=Path(root);self.base=self.root/'data/learning';self.lock=threading.RLock()
    def folder(self,id):
        if not re.fullmatch(r'[a-f0-9]{32}',id):raise ValueError('Unknown learning project')
        folder=(self.base/id).resolve()
        if not folder.is_relative_to(self.base.resolve()) or not (folder/'project.json').is_file():raise ValueError('Unknown learning project')
        return folder
    def read(self,id):return json.loads((self.folder(id)/'project.json').read_text())
    def write(self,p):
        folder=self.base/p['id'];folder.mkdir(parents=True,exist_ok=True)
        temporary=folder/'project.tmp';temporary.write_text(json.dumps(p,indent=2));temporary.replace(folder/'project.json')
    def create(self,name,classes):
        if not name.strip() or len(name)>100:raise ValueError('Give the project a name of up to 100 characters')
        if not 1<=len(classes)<=40 or len(set(classes))!=len(classes) or any(not x.strip() or len(x)>80 or x!=x.strip() or x.startswith('group:') for x in classes):raise ValueError('Use 1–40 unique raw object class names')
        with self.lock:
            p={'id':uuid4().hex,'name':name.strip(),'classes':classes,'clips':[],'frames':[],'frozen':False,'jobs':[],'created_at':time.time()};self.write(p);return p
    def catalog(self):
        return [json.loads(path.read_text()) for path in sorted(self.base.glob('*/project.json'))]
    def editable(self,p):
        if p['frozen']:raise ValueError('This dataset is frozen. Create a new project for changed labels or splits; the held-out test remains fixed.')
    def analysis_hashes(self):
        hashes=set()
        for path in (self.root/'data/input').glob('*'):
            if path.is_file() and path.suffix.lower() in ('.mp4','.mov','.mkv','.avi','.webm','.mpeg','.m4v'):
                with path.open('rb') as stream:hashes.add(hashlib.file_digest(stream,'sha256').hexdigest())
        return hashes
    def import_clip(self,id,path,name,group,split):
        if split not in SPLITS or not group.strip() or len(group)>120:raise ValueError('Choose a split and recording group (up to 120 characters)')
        group=group.strip().casefold()
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest in self.analysis_hashes():raise ValueError('This video matches your analysis library. Use separate training footage; current analysis clips are reserved.')
        # Verify it decodes before publishing. Sources are never placed in data/input.
        source=FileReplaySource(path)
        try:source.open();source.read()
        except Exception as exc:raise ValueError('Video could not be decoded; use a supported video file') from exc
        finally:source.close()
        with self.lock:
            p=self.read(id);self.editable(p)
            if any(c['sha256']==digest for project in self.catalog() for c in project['clips']):raise ValueError('This exact video is already in a learning project; duplicate sources are not allowed')
            if any(c['group']==group and c['split']!=split for c in p['clips']):raise ValueError('Related clips in a recording group must stay in the same split')
            cid=uuid4().hex;dest=self.folder(id)/'sources'/f'{cid}{Path(name).suffix.lower()}';dest.parent.mkdir(exist_ok=True);shutil.move(str(path),dest)
            p['clips'].append({'id':cid,'name':Path(name).name,'file':dest.name,'sha256':digest,'group':group,'split':split,'status':'uploaded','frames':0});self.write(p);return p
    def sample(self,id,cid,interval,maximum,mode='interval'):
        import cv2
        if not math.isfinite(interval) or not .5<=interval<=60 or not 1<=maximum<=200 or mode not in ('interval','coverage','every_frame'):
            raise ValueError('Choose a valid sampling mode, 0.5–60 seconds and 1–200 frames per batch')
        with self.lock:
            p=self.read(id);self.editable(p);clip=next((c for c in p['clips'] if c['id']==cid),None)
            if not clip:raise ValueError('Unknown source clip')
            if clip['status'] not in ('uploaded','failed','ready'):raise ValueError('Clip currently preparing')
            remaining=1000-len(p['frames'])
            if remaining<=0:raise ValueError('Project limit is 1000 sampled frames; create another project for further coverage')
            maximum=min(maximum,remaining);previous=clip['status']
            existing={f['timestamp_ms'] for f in p['frames'] if f['clip_id']==cid}
            clip['status']='preparing';self.write(p);path=self.folder(id)/'sources'/clip['file']
        source=FileReplaySource(path);frames=[];images=self.folder(id)/'images';images.mkdir(exist_ok=True)
        try:
            # Inspect source timestamps only. No source names or categories determine selection.
            source.open();eligible=[];seen=set();last=None
            for raw_frame in source.decoder:
                if raw_frame.pts is None or raw_frame.time_base is None:raise ValueError('Frame has no presentation timestamp')
                stamp=round(float(raw_frame.pts*raw_frame.time_base)*1000)
                if stamp in existing:continue
                if mode!='every_frame' and last is not None and stamp-last<interval*1000:continue
                if stamp not in seen:eligible.append(stamp);seen.add(stamp)
                last=stamp
            source.close()
            if mode=='coverage' and len(eligible)>maximum:
                targets={eligible[round(i*(len(eligible)-1)/(maximum-1))] for i in range(maximum)} if maximum>1 else {eligible[len(eligible)//2]}
            else:targets=set(eligible[:maximum])
            if targets:
                source=FileReplaySource(path);source.open()
                for raw_frame in source.decoder:
                    if raw_frame.pts is None or raw_frame.time_base is None:raise ValueError('Frame has no presentation timestamp')
                    stamp=round(float(raw_frame.pts*raw_frame.time_base)*1000)
                    if stamp not in targets:continue
                    targets.remove(stamp);image=raw_frame.to_ndarray(format='bgr24')
                    fid=uuid4().hex;h,w=image.shape[:2];name=f'{fid}.jpg'
                    if not cv2.imwrite(str(images/name),image):raise ValueError('Could not save frame')
                    frames.append({'id':fid,'file_name':name,'clip_id':cid,'timestamp_ms':stamp,'width':w,'height':h,'boxes':[],'reviewed':False,'verified_empty':False})
                    if not targets:break
                if targets:raise ValueError('Source timestamps changed during extraction')
            with self.lock:
                p=self.read(id);self.editable(p);clip=next(c for c in p['clips'] if c['id']==cid)
                if len(p['frames'])+len(frames)>1000:raise ValueError('Project frame capacity changed; retry a smaller batch')
                p['frames'].extend(frames)
                clip.update(status='ready',frames=sum(f['clip_id']==cid for f in p['frames']),interval_s=interval)
                clip.setdefault('sampling_history',[]).append(dict(created_at=time.time(),mode=mode,interval_s=interval,max_frames=maximum,added=len(frames),eligible=len(eligible),remaining=len(eligible)-len(frames)))
                self.write(p);return p
        except BaseException:
            with self.lock:
                p=self.read(id);next(c for c in p['clips'] if c['id']==cid)['status']='ready' if previous=='ready' else 'failed';self.write(p)
            raise
        finally:source.close()
    def archive_clip(self,id,cid):
        with self.lock:
            p=self.read(id);self.editable(p);clip=next((c for c in p['clips'] if c['id']==cid),None)
            if not clip:raise ValueError('Unknown clip')
            if clip['status']=='preparing':raise ValueError('Wait for frame preparation before archiving')
            # Preserve source files, images and labels for recovery; exclude them from active exports.
            folder=self.folder(id)/'archived';folder.mkdir(exist_ok=True)
            frames=[f for f in p['frames'] if f['clip_id']==cid]
            (folder/f'{cid}.json').write_text(json.dumps({'clip':clip,'frames':frames},indent=2))
            p['clips']=[c for c in p['clips'] if c['id']!=cid];p['frames']=[f for f in p['frames'] if f['clip_id']!=cid];self.write(p);return p
    def assign(self,id,cid,group,split):
        if split not in SPLITS or not group.strip() or len(group)>120:raise ValueError('Choose a split and recording group')
        group=group.strip().casefold()
        with self.lock:
            p=self.read(id);self.editable(p);clip=next((c for c in p['clips'] if c['id']==cid),None)
            if not clip:raise ValueError('Unknown clip')
            if clip.get('intake_source',{}).get('source_split')=='test' and split!='test':raise ValueError('Supplied test footage stays held out')
            if any(c['id']!=cid and c['group']==group and c['split']!=split for c in p['clips']):raise ValueError('Recording group already belongs to another split; move all related clips consistently')
            p.setdefault('assignment_history',[]).append({'clip_id':cid,'prior_group':clip['group'],'prior_split':clip['split'],
                'group':group,'split':split,'changed_at':time.time()})
            basis='human' if group!=clip['group'] else clip.get('group_basis','human')
            clip.update(group=group,split=split,group_basis=basis);self.write(p);return p
    @staticmethod
    def grouping_signature(p):
        rows=sorted((c['id'],c['sha256'],c['group'],c['split'],c.get('group_basis','human')) for c in p['clips'])
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    def confirm_recording_separation(self,id):
        with self.lock:
            p=self.read(id);self.editable(p)
            if {c['split'] for c in p['clips']}!=SPLITS:raise ValueError('Add training, validation and held-out test recordings before confirming separation')
            groups={}
            for c in p['clips']:
                if c['group'] in groups and groups[c['group']]!=c['split']:raise ValueError('Related recording groups cross roles; correct assignments first')
                groups[c['group']]=c['split']
            signature=self.grouping_signature(p)
            p.setdefault('group_review_history',[]).append({'confirmed_at':time.time(),'assignment_sha256':signature,
                'statement':'Operator checked independent recordings across training, validation and test; related clips remain in one role.'})
            p['group_confirmation_sha256']=signature;self.write(p);return p
    def update_classes(self,id,classes):
        if not 1<=len(classes)<=40 or len(set(classes))!=len(classes) or any(not x.strip() or len(x)>80 or x!=x.strip() or x.startswith('group:') for x in classes):raise ValueError('Use 1–40 unique raw object class names')
        with self.lock:
            p=self.read(id);self.editable(p)
            used={b['label'] for f in p['frames'] for b in [*f['boxes'],*f.get('proposals',[])]}
            if used-set(classes):raise ValueError('Cannot remove a class referenced by labels or saved proposals')
            if classes==p['classes']:return p
            p.setdefault('class_history',[]).append({'prior_classes':p['classes'],'classes':classes,'changed_at':time.time()})
            p['classes']=classes
            for f in p['frames']:
                p.setdefault('review_history',[]).append({'id':uuid4().hex,'frame_id':f['id'],'saved_at':time.time(),
                    'reason':'Class definitions changed; frame needs review','prior_boxes':f['boxes'],'prior_reviewed':f['reviewed'],'prior_verified_empty':f['verified_empty']})
                f.update(reviewed=False,verified_empty=False,revision=f.get('revision',0)+1)
            self.write(p);return p
    def label(self,id,fid,boxes,reviewed,verified_empty,proposal_decisions=None,expected_revision=None,ai_review_id=None):
        with self.lock:
            p=self.read(id);self.editable(p);frame=next((f for f in p['frames'] if f['id']==fid),None)
            if not frame:raise ValueError('Unknown frame')
            if expected_revision is not None and expected_revision!=frame.get('revision',0):raise ValueError('Frame changed; refresh before saving your review')
            if ai_review_id and (frame.get('ai_review',{}).get('id')!=ai_review_id or frame['ai_review']['revision']!=frame.get('revision',0)):
                raise ValueError('AI review changed or is stale; refresh before applying it')
            if len(boxes)>200:raise ValueError('Too many boxes in one frame')
            ids=set()
            for box in boxes:
                if box['id'] in ids or box['label'] not in p['classes']:raise ValueError('Box IDs must be unique and labels must be project classes')
                ids.add(box['id']);x,y,w,h=box['bbox']
                if not all(math.isfinite(v) for v in (x,y,w,h)) or not(w>0 and h>0 and x>=0 and y>=0 and x+w<=1.000001 and y+h<=1.000001):raise ValueError('Boxes must have positive size and stay inside the image')
            if reviewed and not boxes and not verified_empty:raise ValueError('Explicitly confirm there are no visible objects of the project classes')
            proposals={b['id']:b for b in frame.get('proposals',[])}
            decisions=proposal_decisions if proposal_decisions is not None else frame.get('proposal_decisions',{})
            if set(decisions)-set(proposals):raise ValueError('Unknown proposal decision')
            final={b['id']:b for b in boxes}
            for bid,decision in decisions.items():
                if decision not in ('accepted','adjusted','rejected'):raise ValueError('Invalid proposal decision')
                if decision=='rejected' and bid in final:raise ValueError('Rejected proposal must be removed from final boxes')
                if decision in ('accepted','adjusted') and bid not in final:raise ValueError('Accepted or adjusted proposal needs a final box')
                if decision=='accepted' and (final[bid]['label']!=proposals[bid]['label'] or final[bid]['bbox']!=proposals[bid]['bbox']):raise ValueError('Changed proposal must be marked adjusted')
            if reviewed and set(decisions)!=set(proposals):raise ValueError('Accept, reject, or adjust every proposal before marking the frame reviewed')
            p.setdefault('review_history',[]).append({'id':uuid4().hex,'frame_id':fid,'saved_at':time.time(),
                'prior_boxes':frame['boxes'],'boxes':boxes,'reviewed':reviewed,'verified_empty':bool(reviewed and not boxes and verified_empty),
                'proposal_decisions':dict(decisions),'proposal_run_id':frame.get('proposal_run',{}).get('id'),'ai_review_id':ai_review_id})
            frame.update(boxes=boxes,reviewed=reviewed,verified_empty=bool(reviewed and not boxes and verified_empty),
                         proposal_decisions=dict(decisions),revision=frame.get('revision',0)+1);self.write(p);return p
    def check(self,p):
        errors=[]
        needs_group_review=any(c.get('group_basis')=='automatic_collection' for c in p['clips']) and p.get('group_confirmation_sha256')!=self.grouping_signature(p)
        if needs_group_review:errors.append('Review recording separation before freezing: automatic collection groups are not verified independent sessions.')
        if not p['clips']:errors.append('Import separate source footage first.')
        if any(c['status']!='ready' for c in p['clips']):errors.append('Sample all imported clips before freezing.')
        if not p['frames'] or any(not f['reviewed'] for f in p['frames']):errors.append('Review every sampled frame, including explicit empty frames.')
        groups={};counts={s:{'clips':0,'frames':0,'reviewed':0,'boxes':0,'labels':{}} for s in SPLITS};clips={c['id']:c for c in p['clips']}
        for c in p['clips']:
            if c['group'] in groups and groups[c['group']]!=c['split']:errors.append('Recording group crosses split boundaries.')
            groups[c['group']]=c['split'];counts[c['split']]['clips']+=1
        for f in p['frames']:
            row=counts[clips[f['clip_id']]['split']];row['frames']+=1;row['reviewed']+=int(f['reviewed']);row['boxes']+=len(f['boxes'])
            for box in f['boxes']:row['labels'][box['label']]=row['labels'].get(box['label'],0)+1
        for split in ('train','valid','test'):
            if not counts[split]['frames']:errors.append(f'{split}: needs an independent recording group with sampled frames.')
            for label in p['classes']:
                if not counts[split]['labels'].get(label):errors.append(f'{split}: needs reviewed {label} examples.')
        return {'ready':not errors,'errors':list(dict.fromkeys(errors)),'splits':counts,'groups':groups,'needs_group_review':needs_group_review,'scope':'Exact duplicates and group overlap are checked. Re-encoded copies and related scenes require honest group assignments; this cannot guarantee absence of bias.'}
    def freeze(self,id):
        with self.lock:
            p=self.read(id);self.editable(p)
            reserved=self.analysis_hashes()
            if any(c['sha256'] in reserved for c in p['clips']):raise ValueError('Learning source now overlaps the current analysis library; freeze refused')
            check=self.check(p)
            if not check['ready']:raise ValueError(' '.join(check['errors']))
            dest=self.folder(id)/'snapshot';temporary=self.folder(id)/'snapshot-building'
            if temporary.exists():shutil.rmtree(temporary)
            temporary.mkdir();categories=[{'id':i+1,'name':n} for i,n in enumerate(p['classes'])];mapping={c['name']:c['id'] for c in categories};clips={c['id']:c for c in p['clips']}
            for split in ('train','valid','test'):
                out=temporary/split;out.mkdir();images=[];annotations=[]
                for index,f in enumerate(p['frames'],1):
                    if clips[f['clip_id']]['split']!=split:continue
                    shutil.copy2(self.folder(id)/'images'/f['file_name'],out/f['file_name']);images.append({'id':index,'file_name':f['file_name'],'width':f['width'],'height':f['height']})
                    for box in f['boxes']:
                        x,y,w,h=box['bbox'];bbox=[x*f['width'],y*f['height'],w*f['width'],h*f['height']]
                        annotations.append({'id':len(annotations)+1,'image_id':index,'category_id':mapping[box['label']],'bbox':bbox,'area':bbox[2]*bbox[3],'iscrowd':0})
                (out/'_annotations.coco.json').write_text(json.dumps({'images':images,'annotations':annotations,'categories':categories},indent=2))
            provenance={'source_assignments':{c['sha256']:c['split'] for c in p['clips']},'recording_groups':check['groups'],'human_review_confirmed':True,'test_present':True,'analysis_library_excluded':True,'project_id':id,'frozen_at':time.time(),'note':check['scope']}
            (temporary/'split-provenance.json').write_text(json.dumps(provenance,indent=2));temporary.replace(dest)
            digest=self.snapshot_hash(id);p.update(frozen=True,snapshot_sha256=digest,freeze_report=check);self.write(p)
            shutil.make_archive(str(self.folder(id)/'dataset'),'zip',dest);return p
    def snapshot_hash(self,id):
        digest=hashlib.sha256();folder=self.folder(id)/'snapshot'
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                digest.update(str(path.relative_to(folder)).encode());digest.update(path.read_bytes())
        return digest.hexdigest()
    def training_ready(self,id):
        p=self.read(id)
        if not p['frozen']:raise ValueError('Freeze a reviewed, isolated dataset first')
        if self.snapshot_hash(id)!=p['snapshot_sha256']:raise ValueError('Frozen snapshot has changed; training refused')
        reserved=self.analysis_hashes()
        if any(c['sha256'] in reserved for c in p['clips']):raise ValueError('Source overlaps the current analysis library; training refused')
        return p
    def dependencies(self):
        required=['pytorch_lightning','hotcoco','vernier']
        return {'ready':all(importlib.util.find_spec(name) for name in required),'missing':[name for name in required if importlib.util.find_spec(name) is None],'install':"./scripts/uv sync --frozen"}
