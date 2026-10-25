import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import type {AuditEvent,LabTelemetry} from '../api/generated';
import {buildAskOrchestration,buildOrchestration,layout} from './orchestration';
import AgentOrchestration from './AgentOrchestration';

let seq=0;
const at=(s:number)=>new Date(Date.UTC(2026,9,25,12,0,s)).toISOString();
const ev=(event_type:string,run_id:string,agent_name:string|null,s:number,extra:Partial<AuditEvent>={}):AuditEvent=>({sequence:++seq,event_type,run_id,agent_name,status:'ok',timestamp_utc:at(s),...extra});
const review=():AuditEvent[]=>[
 ev('run_started','job-root',null,0,{status:'running'}),
 ev('run_started','obs-1','video_observer',1,{parent_run_id:'review'}),ev('model_request','obs-1','video_observer',1),ev('model_response','obs-1','video_observer',2,{resources:{cost_usd:.002}}),ev('run_finished','obs-1','video_observer',3),
 ev('run_started','obs-2','video_observer',4,{parent_run_id:'review'}),ev('run_finished','obs-2','video_observer',5),
 ev('run_started','det-1','video_observer',5,{provenance:{role:'detail_observer',temporal_detail_id:'d1'}}),ev('run_finished','det-1','video_observer',6),
 ev('run_started','ver-1','challenge_reviewer',6,{provenance:{role:'visual_verifier',temporal_detail_id:'d1'}}),ev('run_failed','ver-1','challenge_reviewer',7,{status:'budget_exceeded'}),
 ev('video_safety_handoff','review',null,8),
 ev('run_started','lead-1','safety_reviewer',8),ev('tool_started','lead-1','safety_reviewer',8,{tool_name:'delegate_specialist',tool_call_id:'t1'}),
 ev('specialist_dispatched','lead-1','specialist_applicability',9,{provenance:{call_id:'delegation-1'}}),
 ev('run_started','spec-1','specialist_applicability',9,{parent_run_id:'lead-1',provenance:{call_id:'delegation-1'}}),
 ev('run_started','chal-1','challenge_reviewer',10),
];

describe('agent orchestration model',()=>{
 it('groups runs by role and links the hierarchy',()=>{
  const o=buildOrchestration(review(),'Review coordinator',Date.parse(at(10)));
  const byId=Object.fromEntries(o.nodes.map(n=>[n.id,n]));
  expect(o.nodes[0].label).toBe('Review coordinator');
  expect(byId.observer.runs).toHaveLength(2);
  expect(byId.observer.status).toBe('completed');
  expect(byId.observer.cost).toBeCloseTo(.002);
  expect(byId.detail.label).toBe('Detail observer');
  expect(byId.verifier.status).toBe('blocked');
  expect(byId.specialist_applicability.label).toBe('Applicability specialist');
  expect(byId.specialist_applicability.runs[0].detail).toBe('Delegation #1');
  expect(byId.lead.tools).toBe(1);
  const edge=(to:string)=>o.edges.find(e=>e.to===to)?.from;
  expect(edge('observer')).toBe('coordinator');
  expect(edge('detail')).toBe('observer');
  expect(edge('verifier')).toBe('detail');
  expect(edge('lead')).toBe('observer');
  expect(edge('specialist_applicability')).toBe('lead');
  expect(edge('challenger')).toBe('lead');
  expect(o.running).toBe(3);
  expect(o.edges.find(e=>e.to==='specialist_applicability')?.active).toBe(true);
  expect(o.edges.find(e=>e.to==='observer')?.active).toBe(false);
 });
 it('ignores job-level runs and lays rows out by depth without overlap',()=>{
  const o=buildOrchestration(review(),'Live coordinator',Date.parse(at(10)));
  expect(o.nodes.some(n=>n.id==='job-root')).toBe(false);
  const {positions,height}=layout(o);
  expect(positions.get('coordinator')!.y).toBeLessThan(positions.get('observer')!.y);
  expect(positions.get('lead')!.y).toBeLessThan(positions.get('specialist_applicability')!.y);
  expect(height).toBeGreaterThan(0);
 });
 it('maps Ask telemetry onto the same roles and settles interrupted agents',()=>{
  const t=(sequence:number,event_type:string,node_id:string,agent:string,state:LabTelemetry['state']='running'):LabTelemetry=>({sequence,timestamp_utc:at(sequence),event_type,node_id,agent,state});
  const events=[t(1,'agent_started','n1','Scope · route · freshness'),t(2,'agent_finished','n1','Scope · route · freshness','completed'),
   t(3,'handoff','n2','Inspect frames · 4'),t(4,'agent_started','n2','Inspect frames · 4'),t(5,'agent_started','n3','Explain the evidence'),t(6,'agent_started','n4','Verify evidence')];
  const live=buildAskOrchestration(events,Date.parse(at(6)));
  expect(live.nodes.map(n=>n.label)).toEqual(['Ask coordinator','Planner','Frame inspector','Answer writer','Verifier']);
  expect(live.edges.find(e=>e.to==='challenger')?.from).toBe('answer');
  expect(live.running).toBe(3);
  const settled=buildAskOrchestration(events,Date.parse(at(9)),true);
  expect(settled.running).toBe(0);
  expect(settled.nodes.find(n=>n.id==='observer')?.status).toBe('cancelled');
 });
});

describe('agent orchestration view',()=>{
 it('draws connectors, statuses, counts and timeline lanes',()=>{
  const html=renderToStaticMarkup(<AgentOrchestration orchestration={buildOrchestration(review(),'Review coordinator',Date.parse(at(10)))} now={Date.parse(at(10))}/>);
  expect(html).toContain('<path');
  expect(html).toContain('is-active');
  expect(html).toContain('Video observer');
  expect(html).toContain('×2');
  expect(html).toContain('orch-node is-blocked role-verifier');
  expect(html).toContain('3 working');
  expect(html).toContain('aria-label="Timeline"');
 });
 it('shows an empty state before any agent starts',()=>{
  const html=renderToStaticMarkup(<AgentOrchestration orchestration={buildOrchestration([])} empty="Waiting for the first agent…"/>);
  expect(html).toContain('Waiting for the first agent…');
  expect(html).not.toContain('<svg');
 });
});

describe('activity polling',()=>{
 it('keeps polling after Live playback ends while windows are still being screened',async()=>{
  const {activityActive}=await import('../features/safety_review/ReviewActivity');
  const status=(state:string,windows:string[],jobs:string[]=[])=>({run:{state},windows:windows.map(s=>({status:s})),safety:{cases:jobs.map(s=>({review_job:{status:s}}))}}) as never;
  expect(activityActive(status('ended',['completed','queued']))).toBe(true);
  expect(activityActive(status('ended',['completed'],['running']))).toBe(true);
  expect(activityActive(status('ended',['completed','completed'],['completed']))).toBe(false);
 });
});

describe('agent tree detail',()=>{
 it('shows tool calls, parallel specialists, a live feed and timeline ticks',()=>{
  const rows=[...review(),ev('run_started','spec-2','specialist_vehicles',10,{parent_run_id:'lead-1',provenance:{call_id:'delegation-2'}}),
   ev('tool_started','spec-2','specialist_vehicles',10,{tool_name:'compare_person_positions',tool_call_id:'t2'})];
  const o=buildOrchestration(rows,'Review coordinator',Date.parse(at(11)));
  expect(o.totals.tools).toBe(2);
  expect(o.feed[0].text).toContain('compare person positions');
  expect(o.nodes.find(n=>n.id==='specialist_vehicles')?.activeTool).toBe('compare_person_positions');
  const html=renderToStaticMarkup(<AgentOrchestration orchestration={o} now={Date.parse(at(11))}/>);
  expect(html).toMatch(/[23] in parallel/);
  expect(html).toContain('orch-tools');
  expect(html).toContain('delegate specialist');
  expect(html).toContain('class="is-active"');
  expect(html).toContain('<animateMotion');
  expect(html).toContain('aria-label="Latest activity"');
  expect(html).toContain('class="tool"');
  expect(html).toContain('class="req"');
 });
});
