import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import type {VideoSafetyCaseView} from '../api/generated';
import WelcomeGuide from './WelcomeGuide';
import ReviewIssueDetails from './ReviewIssueDetails';
import VideoPreview from '../features/monitoring/VideoPreview';
import {ReviewPreferencesProvider} from '../hooks/useReviewPreferences';

describe('Daylight Hi-Vis shell',()=>{
 it('keeps the first-run guide short while preserving AI, data and spending disclosures',()=>{
  const html=renderToStaticMarkup(<WelcomeGuide onClose={()=>{}}/>);
  for(const step of ['Drop','Watch','Report','Preview','Live','Review','Get started']) expect(html).toContain(step);
  for(const disclosure of ['send sampled images to the AI provider','$0.15 per review, $2 per day and $20 per month','audio is not assessed','Preview uses local CV only']) expect(html).toContain(disclosure);
  expect(html).toContain('<details');
 });
 it('opens Preview on a drop target and the video library',()=>{
  const html=renderToStaticMarkup(<ReviewPreferencesProvider><VideoPreview onHelp={()=>{}}/></ReviewPreferencesProvider>);
  expect(html).toContain('aria-label="Open a video"');
  expect(html).toContain('Open video');
  expect(html).toContain('Your videos');
 });
 it('offers one-tap decisions that still require a written reason',()=>{
  const item={case:{id:'case-1',category:'blocked_access',start_ms:0,end_ms:1000},screenings:[{id:'s1',check:{condition:'Pallet in the walkway.',visibility:'clear',evidence_refs:[],start_ms:0,end_ms:1000}}],priority:'routine',manager_attention:[],status:'queued',report_condition:'scheduled'} as unknown as VideoSafetyCaseView;
  const html=renderToStaticMarkup(<ReviewIssueDetails item={item} citations={[]} decisions={[]} busy={false} onClose={()=>{}} onDecide={async()=>true}/>);
  for(const choice of ['Accept','Needs more evidence','Dismiss']) expect(html).toContain(choice);
  expect(html).toContain('aria-pressed="true"');
  expect(html).toMatch(/<textarea required=""/);
  expect(html).not.toContain('<select');
 });
});
