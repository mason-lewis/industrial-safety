"""Prepare local labeling frames and enforce clip-separated, human-reviewed training exports."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'backend'))
from app.video.sources import FileReplaySource

ROOT=Path(__file__).resolve().parents[4]
CLASSES=['person','forklift','reach truck','pallet jack','car','truck','bus']

def prepare(folder,output,interval_s=3,max_per_clip=40):
    import cv2
    if output.exists():raise ValueError('Output already exists; use a new dataset name to preserve labels')
    (output/'images').mkdir(parents=True);images=[];sources=[];manifest=[]
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in ('.mp4','.mov','.mkv','.avi','.webm'):continue
        if not path.resolve().is_relative_to(folder.resolve()):continue
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        source=FileReplaySource(path);source.open();last=None;count=0
        try:
            while count<max_per_clip:
                try:frame=source.read()
                except StopIteration:break
                if last is not None and frame.timestamp_ms-last<interval_s*1000:continue
                name=f'{digest[:12]}_{frame.timestamp_ms:09d}.jpg';h,w=frame.image.shape[:2]
                if not cv2.imwrite(str(output/'images'/name),frame.image):raise ValueError('Could not save a source frame')
                id=len(images)+1;images.append({'id':id,'file_name':name,'width':w,'height':h})
                manifest.append({'image_id':id,'file_name':name,'source_sha256':digest,'timestamp_ms':frame.timestamp_ms,'frame_id':frame.frame_id,'reviewed':False})
                last=frame.timestamp_ms;count+=1
        finally:source.close()
        sources.append({'sha256':digest,'name':path.name,'sampled_frames':count})
    categories=[{'id':i+1,'name':name} for i,name in enumerate(CLASSES)]
    (output/'annotations.coco.json').write_text(json.dumps({'images':images,'annotations':[],'categories':categories},indent=2))
    data={'id':output.name,'status':'Unlabeled source frames — human bounding boxes required; not training-ready','classes':CLASSES,'sources':sources,'frames':manifest,'sampling_interval_s':interval_s,'accuracy':'unmeasured; no labels or forklift detection claims'}
    (output/'manifest.json').write_text(json.dumps(data,indent=2))
    (output/'labeling-instructions.txt').write_text('Label every visible instance of the declared classes, including people and forklifts, in a COCO-compatible tool. Empty annotations are not verified negatives until human review. Do not label a truck as forklift unless visibly supported. Keep source IDs/timestamps. Assign whole source clips to train/valid/test; do not randomly split adjacent frames. Follow backend/app/trainer/docs/cv-industrial.md to validate and export.\n')
    with zipfile.ZipFile(output/'labeling-bundle.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file() and path.name!='labeling-bundle.zip':archive.write(path,path.relative_to(output))
    return {'images':len(images),'sources':len(sources),'status':data['status']}

def validate_annotations(data,manifest,root):
    images={i['id']:i for i in data['images']};categories={c['id']:c['name'] for c in data['categories']}
    if len(images)!=len(data['images']) or len(categories)!=len(data['categories']):raise ValueError('Duplicate image or category ID')
    if set(categories.values())!=set(manifest['classes']):raise ValueError('Annotation classes must match the labeling manifest')
    by_name={f['file_name']:f for f in manifest['frames']}
    if {i['file_name'] for i in images.values()}!=set(by_name):raise ValueError('Annotated image filenames must match the complete prepared source frame set')
    for image in images.values():
        path=(root/'images'/image['file_name']).resolve()
        if not path.is_relative_to((root/'images').resolve()) or not path.is_file():raise ValueError('Image path is missing or escapes dataset images')
    ids=set()
    for a in data['annotations']:
        if a['id'] in ids:raise ValueError('Duplicate annotation ID')
        ids.add(a['id']);image=images.get(a['image_id'])
        if not image or a['category_id'] not in categories:raise ValueError('Annotation references unknown image or class')
        x,y,w,h=a['bbox']
        if not (w>0 and h>0 and 0<=x<=image['width']-w and 0<=y<=image['height']-h):raise ValueError('Box must have positive size and remain inside the image')
    if not data['annotations']:raise ValueError('No human object annotations: this dataset is not training-ready')
    return images,categories,by_name

def split_dataset(root,annotation_file,splits_file,output,confirm_reviewed):
    if not confirm_reviewed:raise ValueError('Human review must be explicitly confirmed; unlabeled frames cannot become negatives automatically')
    if output.exists():raise ValueError('Export output already exists')
    manifest=json.loads((root/'manifest.json').read_text());data=json.loads(annotation_file.read_text());splits=json.loads(splits_file.read_text())
    images,categories,provenance=validate_annotations(data,manifest,root)
    source_ids={s['sha256'] for s in manifest['sources']}
    if set(splits)!=source_ids or any(v not in ('train','valid','test') for v in splits.values()):raise ValueError('Assign each entire source SHA256 to exactly one train/valid/test split')
    if not {'train','valid'}.issubset(set(splits.values())):raise ValueError('Separate train and validation source clips are required')
    split_images={name:[i for i in images.values() if splits[provenance[i['file_name']]['source_sha256']]==name] for name in ('train','valid','test')}
    for name in ('train','valid'):
        selected={i['id'] for i in split_images[name]}
        present={categories[a['category_id']] for a in data['annotations'] if a['image_id'] in selected}
        if not {'person','forklift'}.issubset(present):raise ValueError(f'{name} needs human person and forklift examples before training/evaluation')
    for name,selected in split_images.items():
        if not selected:continue
        dest=output/name;dest.mkdir(parents=True);ids={i['id'] for i in selected}
        for image in selected:shutil.copy2(root/'images'/image['file_name'],dest/image['file_name'])
        (dest/'_annotations.coco.json').write_text(json.dumps({'images':selected,'categories':data['categories'],'annotations':[a for a in data['annotations'] if a['image_id'] in ids]},indent=2))
    (output/'split-provenance.json').write_text(json.dumps({'source_assignments':splits,'human_review_confirmed':True,'test_present':bool(split_images['test']),'note':'Clip-separated split does not by itself establish site generalization.'},indent=2))
    return {name:len(rows) for name,rows in split_images.items()}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('prepare');a.add_argument('--input',type=Path,default=ROOT/'data/input');a.add_argument('--output',type=Path,required=True);a.add_argument('--interval-s',type=float,default=3);a.add_argument('--max-per-clip',type=int,default=40)
    a=sub.add_parser('split');a.add_argument('--dataset',type=Path,required=True);a.add_argument('--annotations',type=Path,required=True);a.add_argument('--splits',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--confirm-reviewed',action='store_true')
    args=p.parse_args()
    if args.action=='prepare':
        if args.interval_s<=0 or not 1<=args.max_per_clip<=1000:p.error('Choose a positive interval and 1–1000 frames per clip')
        result=prepare(args.input,args.output,args.interval_s,args.max_per_clip)
    else:result=split_dataset(args.dataset,args.annotations,args.splits,args.output,args.confirm_reviewed)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
