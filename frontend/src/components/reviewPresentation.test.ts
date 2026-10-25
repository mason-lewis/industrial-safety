import {describe,it,expect} from 'vitest';
import type {LivePersonSample,ReviewRunStatus,VideoSafetyCaseView} from '../api/generated';
import {categoryName,groupIssues,issueDescription,issueInterval,liveSampleForDisplay,formatTime,issueState,knownCost,runHeadline} from './reviewPresentation';

describe('truthful review progress',()=>{
  it('uses the independently reviewed conclusion with its qualifications intact',()=>{
    const item={screenings:[{check:{condition:'Original provisional concern.'}}],assessment:{findings:[
      {status:'challenged',conclusion:'Unverified assertion.'},
      {status:'reviewed',conclusion:'Apparent contact in the sampled frames; injury is not established.'}]}} as VideoSafetyCaseView;
    expect(issueDescription(item)).toBe('Apparent contact in the sampled frames; injury is not established.');
    item.assessment!.findings=item.assessment!.findings.filter(f=>f.status==='challenged');
    expect(issueDescription(item)).toContain('remains uncertain');
    expect(issueDescription(item)).not.toContain('Original provisional');
    expect(issueState(item)).toBe('Needs your review');
    item.assessment!.findings=[];
    expect(issueDescription(item)).toBe('Original provisional concern.');
  });
  const status=(patch:Partial<ReviewRunStatus>)=>({run:{kind:'review',state:'ended'},coverage_complete:true,pending_specialists:0,...patch} as ReviewRunStatus);
  it('never labels complete coverage with pending specialists a completed review',()=>{
    expect(runHeadline(status({pending_specialists:2}))).toBe('2 team reviews unfinished');
    expect(runHeadline(status({coverage_complete:false}))).toContain('coverage incomplete');
    expect(runHeadline(status({}))).toBe('Review complete');
  });
  it('keeps stopped and budget-limited work explicit',()=>{
    expect(runHeadline(status({run:{state:'budget_exceeded'} as ReviewRunStatus['run']}))).toContain('unfinished');
    expect(runHeadline(status({run:{state:'cancelled'} as ReviewRunStatus['run']}))).toBe('Stopped · results saved');
  });
  it('does not mislabel a historical token-count service failure as an exhausted budget',()=>{
    expect(runHeadline(status({run:{state:'budget_exceeded'} as ReviewRunStatus['run'],windows:[{error:'Image token preflight unavailable; generation was not dispatched'}] as ReviewRunStatus['windows']}))).toBe('Provider preflight unavailable · review unfinished');
  });
  it('does not call omitted occurrences team-reviewed',()=>{
    const item={assessment:{},unassessed_screening_ids:['omitted']} as VideoSafetyCaseView;
    expect(issueState(item)).toBe('Partly reviewed');
    expect(issueState({status:'failed'} as VideoSafetyCaseView)).toBe('Review unfinished');
  });
  it('does not turn missing usage into zero spend',()=>{
    expect(knownCost({})).toBe('Unknown');
    expect(knownCost({usage_cost_usd:null})).toBe('Unknown');
    expect(knownCost({usage_cost_usd:0})).toBe('$0.0000');
  });
  it('keeps final fractional intervals distinguishable',()=>{
    expect(formatTime(10000,true)).toBe('0:10');
    expect(formatTime(10440,true)).toBe('0:10.44');
    expect(formatTime(600000,true)).toBe('10:00');
  });
});


describe('grouped findings and exact Live CV images',()=>{
  const item=(id:string,category:string,start:number,priority='routine')=>({case:{id,category,start_ms:start,end_ms:start+1000},screenings:[{id}],priority}) as VideoSafetyCaseView;
  it('shows one category card while preserving each distinct case and priority',()=>{
    const a=item('a','slip_trip',0),b=item('b','slip_trip',10000),impact=item('impact','struck_by',5400,'high');
    const grouped=groupIssues([a,b,impact]);
    expect(grouped).toHaveLength(2);expect(grouped[0].lead).toBe(impact);
    expect(grouped[1].items).toEqual([a,b]);expect(grouped[1].observations).toBe(2);
    expect(grouped[1].end).toBe(11000);
    expect(groupIssues([a,item('possible-impact','struck_by',5400)])[0].category).toBe('struck_by');
  });
  it('names fire concerns and leads with them like other acute events',()=>{
    const fire=item('flare','fire_burn',44400);
    expect(categoryName('fire_burn')).toBe('Fire or burn concern');
    expect(groupIssues([item('trip','slip_trip',0),fire])[0].category).toBe('fire_burn');
  });
  const run={id:'r',source_id:'v',session_id:'s',generation:2} as ReviewRunStatus['run'];
  const sample={review_run_id:'r',source_id:'v',session_id:'s',run_generation:2,timestamp_ms:1000,status:'available',image_data_url:'exact image'} as LivePersonSample;
  it('uses the matching CV image, suppressing stale, future or disconnected samples',()=>{
    expect(liveSampleForDisplay(sample,run,1500,true)).toBe(sample);
    expect(liveSampleForDisplay(sample,run,999,true)).toBeNull();
    expect(liveSampleForDisplay(sample,run,2501,true)).toBeNull();
    expect(liveSampleForDisplay(sample,run,1000,false)).toBeNull();
    expect(liveSampleForDisplay({...sample,run_generation:1},run,1000,true)).toBeNull();
    expect(liveSampleForDisplay({...sample,source_id:'other'},run,1000,true)).toBeNull();
    expect(liveSampleForDisplay({...sample,review_run_id:'old'},run,1000,true)).toBeNull();
    expect(liveSampleForDisplay({...sample,status:'failed'},run,1000,true)).toBeNull();
  });
});


it('uses observed evidence times instead of the enclosing ten-second grouping bucket',()=>{
  const item={case:{start_ms:0,end_ms:10000},screenings:[{check:{start_ms:5100,end_ms:5600}}]} as VideoSafetyCaseView;
  expect(issueInterval(item)).toEqual({start:5100,end:5600});
  expect(item.case.start_ms).toBe(0);
  expect(issueInterval({...item,screenings:[]})).toEqual({start:0,end:10000});
});
