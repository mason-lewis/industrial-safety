"""Register a local, safely loadable RF-DETR checkpoint; never assert site accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'backend'))
from app.cv.detector import RFDETRDetector
ROOT=Path(__file__).resolve().parents[4]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--weights',type=Path,required=True);p.add_argument('--classes',nargs='+',required=True);p.add_argument('--id',required=True);p.add_argument('--name',required=True);p.add_argument('--license',required=True);p.add_argument('--device',default='cpu');args=p.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',args.id):p.error('Use a simple lowercase model ID')
    if args.weights.suffix not in ('.pth','.ckpt') or not args.weights.is_file():p.error('Choose an existing .pth or .ckpt checkpoint')
    if len(set(args.classes))!=len(args.classes) or any(not label.strip() for label in args.classes):p.error('Class names must be unique and nonempty')
    folder=ROOT/'data/artifacts/models';folder.mkdir(parents=True,exist_ok=True);dest=folder/f'{args.id}{args.weights.suffix}';manifest=folder/f'{args.id}.detector.json'
    if dest.exists() or manifest.exists():p.error('Model ID already exists; use a new version ID')
    model=RFDETRDetector('custom',str(args.weights.resolve()),{i:label for i,label in enumerate(args.classes)},args.device)
    model.predict(np.zeros((256,256,3),dtype=np.uint8),.5)
    shutil.copy2(args.weights,dest)
    with dest.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    record={'id':args.id,'name':args.name,'format':'rfdetr-checkpoint','weights':dest.name,'sha256':digest,'classes':args.classes,'license':args.license,'compatibility':'safe checkpoint load and blank inference passed','site_accuracy':'unmeasured; evaluate on separately held-out footage'}
    manifest.write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2))
if __name__=='__main__':main()
