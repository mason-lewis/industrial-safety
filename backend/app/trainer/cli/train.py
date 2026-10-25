"""Explicit local RF-DETR training/evaluation of a reviewed clip-separated dataset."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'backend'))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--required-classes',nargs='+',default=['person','forklift']);p.add_argument('--variant',choices=['nano','small'],default='small');p.add_argument('--epochs',type=int,default=30);p.add_argument('--batch-size',type=int,default=2);p.add_argument('--device',choices=['cpu','mps','cuda'],default='mps');p.add_argument('--run-training',action='store_true');p.add_argument('--evaluate-weights',type=Path);args=p.parse_args()
    if args.epochs<1 or args.batch_size<1:p.error('Epochs and batch size must be positive')
    provenance=json.loads((args.dataset/'split-provenance.json').read_text())
    if not provenance.get('human_review_confirmed'):p.error('Dataset human review was not confirmed')
    for split in ('train','valid'):
        data=json.loads((args.dataset/split/'_annotations.coco.json').read_text());present={a['category_id'] for a in data['annotations']};names={c['name'] for c in data['categories'] if c['id'] in present}
        if not set(args.required_classes).issubset(names):p.error(f'{split} lacks reviewed classes: '+', '.join(sorted(set(args.required_classes)-names)))
    if not args.run_training and not args.evaluate_weights:
        print('Dataset structure ready. No training launched; pass --run-training explicitly. Site accuracy remains unmeasured.');return
    from rfdetr import RFDETR,RFDETRSmall,RFDETRNano
    if args.evaluate_weights:
        model=RFDETR.from_checkpoint(str(args.evaluate_weights),device=args.device,trust_checkpoint=False)
        if not provenance.get('test_present'):p.error('An independent held-out test source is required for this evaluation')
        metrics=model.evaluate(dataset_dir=str(args.dataset),split='test')
        args.output.mkdir(parents=True,exist_ok=True);(args.output/'evaluation.json').write_text(json.dumps({'metrics':metrics,'scope':'this held-out dataset; no site-wide performance guarantee'},indent=2));print(metrics)
    else:
        model=(RFDETRNano if args.variant=='nano' else RFDETRSmall)(device=args.device)
        model.train(dataset_dir=str(args.dataset),output_dir=str(args.output),epochs=args.epochs,batch_size=args.batch_size,run_test=False,tensorboard=False,wandb=False)
        print('Training completed; evaluate the saved checkpoint on held-out test footage before capability claims.')
if __name__=='__main__':main()
