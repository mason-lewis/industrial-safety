import type {PersonFrame} from '../api/generated';

export default function PersonEvidence({frame,alt}:{frame:PersonFrame;alt:string}){
 return <div className="person-evidence-image"><img src={`/api/media/frames/${frame.frame_id}`} alt={alt}/>{frame.status==='available'&&<div className="person-boxes" aria-label={`${frame.people?.length??0} person detections`}>{frame.people?.map((p,i)=><span key={p.id} className="person-box" style={{left:`${p.box[0]*100}%`,top:`${p.box[1]*100}%`,width:`${p.box[2]*100}%`,height:`${p.box[3]*100}%`}}><span>Person {i+1} · {Math.round(p.confidence*100)}%</span></span>)}</div>}</div>;
}
