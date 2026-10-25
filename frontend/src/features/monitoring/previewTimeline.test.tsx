import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import type {PersonPreview} from '../../api/generated';
import {overlayAt,nextOverlayPosition} from './previewTimeline';
import PersonOverlay from './PersonOverlay';
import VideoThumbnail from '../../components/VideoThumbnail';
import {personCount} from '../../components/reviewPresentation';

const sample=(position: number,patch: Partial<PersonPreview> = {}): PersonPreview => ({source_id:'video',position_ms:position,timestamp_ms:position,
 status:'available',confidence_threshold:.5,image_data_url:'',elapsed_ms:200,
 provenance:{input_width:640,input_height:480},people:[{id:'one',box:[.1,.2,.3,.4],confidence:.8}],...patch});

describe('live overlay timeline',()=>{
 it('never leaks future, stale, other-source, old-threshold or failed boxes',()=>{
  const cases=[sample(600),sample(0),sample(500,{source_id:'other'}),sample(500,{confidence_threshold:.9}),sample(500,{status:'failed'})];
  for(const value of cases) expect(overlayAt([value],'video',.5,550)).toBeNull();
 });
 it('advances with playback and an empty detection replaces earlier boxes',()=>{
  const frames=[sample(0),sample(250),sample(500,{people:[]})];
  expect(overlayAt(frames,'video',.5,200)?.position_ms).toBe(0);
  expect(overlayAt(frames,'video',.5,400)?.position_ms).toBe(250);
  expect(overlayAt(frames,'video',.5,500)?.people).toEqual([]);
  expect(overlayAt(frames,'video',.5,1001)).toBeNull();
  expect(overlayAt(frames,'video',.5,100)?.position_ms).toBe(0);
 });
 it('checks paused frames once and bounds lookahead and the recording endpoint',()=>{
  expect(nextOverlayPosition(0,2000,true,-Infinity)).toBe(0);
  expect(nextOverlayPosition(0,2000,true,0)).toBeNull();
  expect(nextOverlayPosition(100,2000,false,0)).toBe(350);
  expect(nextOverlayPosition(500,2000,false,0)).toBe(1100);
  expect(nextOverlayPosition(100,2000,false,1200)).toBeNull();
  expect(nextOverlayPosition(1900,2000,false,1600)).toBe(2000);
  expect(nextOverlayPosition(2000,2000,false,2000)).toBeNull();
 });
 it('interpolates nearby boxes without changing source observations or detector scores',()=>{
  const first=sample(0), next=sample(250,{people:[{id:'other-rank',box:[.15,.25,.35,.45],confidence:.95}]});
  const result=overlayAt([first,next],'video',.5,125)!;
  [.125,.225,.325,.425].forEach((value,index)=>expect(result.people?.[0].box[index]).toBeCloseTo(value));
  expect(result.people?.[0].confidence).toBe(.8);
  expect(first.people?.[0].box).toEqual([.1,.2,.3,.4]);
  expect(overlayAt([first,next],'video',.5,250)?.people?.[0].box).toEqual(next.people?.[0].box);
 });
 it('matches by position when confidence ranks reorder and does not add future people',()=>{
  const left={id:'1',box:[.1,.1,.1,.2] as [number,number,number,number],confidence:.8};
  const right={id:'2',box:[.7,.1,.1,.2] as [number,number,number,number],confidence:.7};
  const result=overlayAt([sample(0,{people:[left,right]}),sample(250,{people:[
   {...right,id:'1',box:[.72,.1,.1,.2]}, {...left,id:'2',box:[.12,.1,.1,.2]},
   {id:'3',box:[.4,.1,.1,.2],confidence:.6},
  ]})],'video',.5,125)!;
  expect(result.people).toHaveLength(2);
  expect(result.people?.[0].box[0]).toBeCloseTo(.11);
  expect(result.people?.[1].box[0]).toBeCloseTo(.71);
 });
 it('does not blend ambiguous overlaps, distant boxes, large gaps or changed image dimensions',()=>{
  const first=sample(0), person=first.people![0];
  for(const next of [
   sample(250,{people:[{...person,id:'a'},{...person,id:'b'}]}),
   sample(250,{people:[{...person,box:[.8,.2,.1,.4]}]}),
   sample(750,{people:[{...person,box:[.2,.2,.3,.4]}]}),
   sample(250,{provenance:{input_width:1920,input_height:1080}}),
  ]) expect(overlayAt([first,next],'video',.5,125)?.people?.[0].box).toEqual(person.box);
 });
 it('uses singular and plural counts',()=>{
  expect([0,1,2].map(personCount)).toEqual(['0 people','1 person','2 people']);
 });
 it('fits overlays to the source aspect ratio and does not render failed detections',()=>{
  const html=renderToStaticMarkup(<PersonOverlay sample={sample(100)}/>);
  expect(html).toContain('viewBox="0 0 640 480"');
  expect(html).toContain('preserveAspectRatio="xMidYMid meet"');
  expect(html).toContain('x="64" y="96" width="192" height="192"');
  expect(html).toContain('Person · 80%');
  expect(html).toContain('aria-label="1 person detected"');
  expect(renderToStaticMarkup(<PersonOverlay sample={sample(100,{status:'unavailable'})}/>)).toBe('');
 });
 it('loads library thumbnails lazily through the authenticated local route',()=>{
  const html=renderToStaticMarkup(<VideoThumbnail sourceId="local-video"/>);
  expect(html).toContain('/api/preview/local-video/thumbnail');
  expect(html).toContain('loading="lazy"');
 });
});
