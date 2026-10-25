import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import PersonEvidence from './PersonEvidence';
import type {PersonFrame} from '../api/generated';

const frame={id:'record',review_run_id:'run',frame_id:'retained-frame',media_id:'media',timestamp_ms:100,content_hash:'hash',status:'available',people:[{id:'frame-only-p1',box:[.1,.2,.3,.4],confidence:.91}]} as PersonFrame;
describe('retained person evidence',()=>{
 it('places boxes on the exact retained image and labels their confidence',()=>{
  const html=renderToStaticMarkup(<PersonEvidence frame={frame} alt="Retained evidence"/>);
  expect(html).toContain('/api/media/frames/retained-frame');
  expect(html).toContain('left:10%;top:20%;width:30%;height:40%');
  expect(html).toContain('Person 1 · 91%');
 });
 it('never draws a box when inference is unavailable',()=>{
  const html=renderToStaticMarkup(<PersonEvidence frame={{...frame,status:'unavailable'}} alt="Retained evidence"/>);
  expect(html).toContain('/api/media/frames/retained-frame');
  expect(html).not.toContain('person-boxes');
 });
});
