#!/usr/bin/env python3
"""Offline release-gate report from human-labeled, exported operator runs. No API calls."""
import argparse,json
from pathlib import Path

def evaluate(manifest,results):
    expected={(case['id'],repeat) for case in manifest['cases'] for repeat in (1,2)}
    keys=[(r['case_id'],r['repeat']) for r in results]
    if len(keys)!=len(set(keys)) or set(keys)-expected:raise ValueError('Duplicate or unknown evaluation case/repetition')
    lookup={case['id']:case for case in manifest['cases']};categories={};missing_labels=[]
    supported=claims=missed=critical=failures=useful=answerable=0;latency=[];cost=0
    for row in results:
        case=lookup[row['case_id']];required=manifest['required_per_case']+['useful','supported_claims','substantive_claims','unsupported_critical_assertions','missed_relevant_events','timestamp_error_ms','boundary_correct','execution_ms','calculated_usd','unknown_requests','turn_id','technical_failure']
        if any(k not in row or row[k] is None for k in required):missing_labels.append([row['case_id'],row['repeat']]);continue
        item=categories.setdefault(case['category'],{'completed':0,'answerable':0,'useful':0,'boundary_correct':0})
        item['completed']+=1;item['boundary_correct']+=bool(row['boundary_correct'])
        if case['answerable']:answerable+=1;useful+=bool(row['useful']);item['answerable']+=1;item['useful']+=bool(row['useful'])
        supported+=row['supported_claims'];claims+=row['substantive_claims'];missed+=row['missed_relevant_events']
        critical+=row['unsupported_critical_assertions'];failures+=bool(row['technical_failure'])
        latency.append(row['execution_ms']);cost+=row['calculated_usd']
    unfinished=sorted(expected-set(keys));unknown=sum(r.get('unknown_requests',0) for r in results)
    useful_rate=useful/answerable if answerable else None
    timestamp_errors=[r['timestamp_error_ms'] for r in results if r.get('timestamp_error_ms') is not None]
    gate=not unfinished and not missing_labels and not unknown and not critical and not failures and useful_rate is not None and useful_rate>=manifest['release_gate']['useful_answer_rate'] and all(r.get('boundary_correct') for r in results)
    return {'version':manifest['version'],'completed':len(results),'planned':len(expected),'unfinished':unfinished,'missing_human_labels':missing_labels,'answerable_sample':answerable,'useful_responses':useful,'useful_rate':useful_rate,'supported_claims':supported,'substantive_claims':claims,'missed_relevant_events':missed,'unsupported_critical_assertions':critical,'technical_failures':failures,'unknown_requests':unknown,'mean_execution_ms':sum(latency)/len(latency) if latency else None,'max_timestamp_error_ms':max(timestamp_errors) if timestamp_errors else None,'calculated_usd':cost,'categories':categories,'release_gate_passed':gate,'limitation':'Task-specific evaluation only; not a general hazard-detection accuracy guarantee.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--results',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();manifest=json.loads((Path(__file__).resolve().parents[1]/'configs/video-chat-evaluation-v1.json').read_text())
    result=evaluate(manifest,json.loads(args.results.read_text()) if args.results else [])
    text=json.dumps(result,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text)
if __name__=='__main__':main()
