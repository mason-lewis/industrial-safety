import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import SafetyLayers from './SafetyLayers';
import type {VideoSafetyOverview} from '../../api/generated';
const view={analysis_id:'a',screenings:[{id:'s',mode:'hosted',check:{category:'blocked_access',result:'suspected',condition:'Fictional obstruction.',limitations:[]}}],cases:[{case:{id:'c',category:'blocked_access'},screenings:[],priority:'high',status:'queued',report_condition:'immediate',manager_attention:[],review_job:{id:'child',status:'queued'}}]} as unknown as VideoSafetyOverview;
const render=(value:VideoSafetyOverview)=>renderToStaticMarkup(<SafetyLayers value={value} onStop={()=>{}}/>);
describe('two safety layers',()=>{
 it('keeps provisional urgency and delivery boundaries visible',()=>{
  const html=render(view);
  expect(html).toContain('Layer 1');expect(html).toContain('Layer 2');expect(html).toContain('1 suspected concerns');
  expect(html).toContain('Provisional concern');expect(html).toContain('Report delivery is not configured');expect(html).toContain('Stop specialist review');
 });
 it('does not present fixture urgency as a real manager alert',()=>{
  const demo=structuredClone(view);demo.screenings[0].mode='fixture';
  const html=render(demo);expect(html).toContain('Demo high priority');expect(html).toContain('not real hazard findings');expect(html).not.toContain('Manager attention now');
 });
 it('retains challenged findings, uncertainty and concrete verification actions',()=>{
  const assessed=structuredClone(view);assessed.cases[0].status='needs_review';assessed.cases[0].review_job!.status='completed';
  assessed.cases[0].assessment={findings:[{id:'f',conclusion:'Fictional proposed concern.',classification:'general_hazard',status:'challenged',uncertainty:['Visibility needs inspection.'],corrective_actions:[{action:'Inspect this route.',verification:'Confirm the obstruction in the original footage.'}]}],limitations:[],triage_supported:false} as unknown as NonNullable<typeof assessed.cases[0]['assessment']>;
  const html=render(assessed);expect(html).toContain('challenged');expect(html).toContain('Visibility needs inspection');expect(html).toContain('Confirm the obstruction');expect(html).toContain('Provisional concern');
 });
});
