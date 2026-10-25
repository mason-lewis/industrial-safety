import type {FramePacket,LabConfig,TrackObservation} from '../types/contracts';
import type {Point} from './geometry';
import {matchesClass} from './classes';
export const anchor=(track:TrackObservation):Point=>[(track.box[0]+track.box[2])/2,track.box[3]];
export function insidePolygon([x,y]:Point,points:Point[]){
 let inside=false;
 for(let i=0,j=points.length-1;i<points.length;j=i++){
  const [ax,ay]=points[j],[bx,by]=points[i];
  const cross=(bx-ax)*(y-ay)-(by-ay)*(x-ax);
  if(Math.abs(cross)<1e-9&&x>=Math.min(ax,bx)&&x<=Math.max(ax,bx)&&y>=Math.min(ay,by)&&y<=Math.max(ay,by))return true;
  if((ay>y)!==(by>y)&&x<(bx-ax)*(y-ay)/(by-ay)+ax)inside=!inside;
 }
 return inside;
}
export const imageDistance=(a:Point,b:Point,width:number,height:number)=>Math.hypot((a[0]-b[0])*width,(a[1]-b[1])*height)/Math.hypot(width,height);
export function proximityLinks(frame:FramePacket,config:LabConfig){
 const links:{id:string;ruleId:string;a:Point;b:Point;distance:number}[]=[];
 for(const rule of config.rules??[]){
  if(rule.kind!=='proximity'||rule.enabled===false)continue;
  const zone=config.zones?.find(z=>z.id===rule.zone_id),seen=new Set<string>();
  const primary=(frame.tracks??[]).filter(t=>matchesClass(t.label,rule.target_labels??['person'],config.class_groups));
  const secondary=(frame.tracks??[]).filter(t=>matchesClass(t.label,rule.secondary_labels??['car','truck','bus'],config.class_groups));
  for(const p of primary)for(const s of secondary){
   if(p.track_id===s.track_id)continue;
   const id=`${rule.id}:${[p.track_id,s.track_id].sort((a,b)=>a-b).join(':')}`;
   if(seen.has(id))continue;seen.add(id);
   const a=anchor(p),b=anchor(s),distance=imageDistance(a,b,frame.width,frame.height);
   if(distance>(rule.distance_threshold??.1))continue;
   if(rule.zone_id&&(!zone||!insidePolygon(a,zone.points)||!insidePolygon(b,zone.points)))continue;
   links.push({id,ruleId:rule.id,a,b,distance});
   if(links.length>=100)return links;
  }
 }
 return links;
}
