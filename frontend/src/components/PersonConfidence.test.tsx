import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import PersonConfidence from './PersonConfidence';
import ReviewSettings from './ReviewSettings';
import VideoPreview from '../features/monitoring/VideoPreview';
import {ReviewPreferencesProvider,validPersonConfidence} from '../hooks/useReviewPreferences';

describe('person confidence and preview',()=>{
 it('keeps saved thresholds separate from current preferences',()=>{
  const html=renderToStaticMarkup(<ReviewPreferencesProvider><ReviewSettings request={{person_confidence:.2}}/></ReviewPreferencesProvider>);
  expect(html).toContain('20%');
  expect(html).not.toContain('type="range"');
 });
 it('accepts valid preferences and recovers missing or corrupt values',()=>{
  for(const value of [.05,.5,.95]) expect(validPersonConfidence(value)).toBe(value);
  for(const value of [null,undefined,'0.2',0,1,NaN,Infinity]) expect(validPersonConfidence(value)).toBe(.5);
 });
 it('labels the bounded slider and its application clearly',()=>{
  const html=renderToStaticMarkup(<ReviewPreferencesProvider><PersonConfidence preview/></ReviewPreferencesProvider>);
  expect(html).toContain('type="range" min="5" max="95" step="5"');
  expect(html).toContain('aria-valuetext="50 percent"');
  expect(html).toContain('Updates the CV preview and your next review.');
 });
 it('opens the preview without an AI control provider or a review submission control',()=>{
  const html=renderToStaticMarkup(<ReviewPreferencesProvider><VideoPreview onHelp={()=>{}}/></ReviewPreferencesProvider>);
  expect(html).toContain('Video preview');
  expect(html).toContain('No AI review starts here.');
  expect(html).toContain('Person detection confidence');
  expect(html).toContain('type="range"');
  for (const clutter of ['Check this frame','Last CV check','Stronger matches','Local person detection','Lower values']) expect(html).not.toContain(clutter);
  expect(html).not.toContain('Start review');
 });
});
