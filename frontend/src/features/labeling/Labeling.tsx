import ReviewQueue from './ReviewQueue';
import LearningWorkspace from '../../trainer/LearningWorkspace';
import {request} from '../../api/client';
import type {LabConfig} from '../../types/contracts';
import {useState} from 'react';
export default function Labeling({onBack}:{onBack:()=>void}){
 const [showMonitoring,setShowMonitoring]=useState(false);
 return <><LearningWorkspace onBack={onBack} onActivate={async(changes:Partial<LabConfig>)=>{await request('/config',changes,'PUT');return true;}}/><details className="panel" onToggle={e=>setShowMonitoring(e.currentTarget.open)}><summary>Review retained Monitoring frames</summary>{showMonitoring&&<ReviewQueue/>}</details></>;
}
