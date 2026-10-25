import {describe,it,expect} from 'vitest';
import {imageDistance,insidePolygon,proximityLinks} from './ruleGeometry';
import type {FramePacket,LabConfig,TrackObservation} from '../types/contracts';
const context={source_id:'s',camera_id:'c',session_id:'r'};
const track=(id:number,x:number,y:number):TrackObservation=>({...context,track_id:id,label:'person',score:.9,box:[x-.01,y-.02,x+.01,y],timestamp_ms:0,frame_id:0});
const frame:FramePacket={...context,frame_id:0,timestamp_ms:0,width:200,height:100,evidence_cutoff_ms:0,tracks:[track(1,.4,.5),track(2,.5,.5)]};
describe('rule overlay measurements',()=>{
 it('uses pixel aspect ratio and image diagonal, not normalized x/y Euclidean distance',()=>{expect(imageDistance([.4,.5],[.5,.5],200,100)).toBeCloseTo(20/Math.hypot(200,100));});
 it('includes polygon boundaries and excludes outside anchors',()=>{const points:[number,number][]=[[0,0],[1,0],[1,1],[0,1]];expect(insidePolygon([0,.5],points)).toBe(true);expect(insidePolygon([.5,.5],points)).toBe(true);expect(insidePolygon([1.1,.5],points)).toBe(false);});
 it('draws one link per pair, honors scope and disabled rules',()=>{const config:LabConfig={rules:[{id:'p',kind:'proximity',target_labels:['person'],secondary_labels:['person'],distance_threshold:.1}]};expect(proximityLinks(frame,config)).toHaveLength(1);config.rules![0].enabled=false;expect(proximityLinks(frame,config)).toHaveLength(0);config.rules![0].enabled=true;config.rules![0].zone_id='z';config.zones=[{id:'z',label:'left',points:[[0,0],[.45,0],[.45,1],[0,1]]}];expect(proximityLinks(frame,config)).toHaveLength(0);});
});
