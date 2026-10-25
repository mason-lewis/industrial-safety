import type {LabConfig} from '../types/contracts';
export function matchesClass(label:string,targets:string[],groups:LabConfig['class_groups']){return targets.some(target=>target.startsWith('group:')?(groups?.[target.slice(6)]??[]).includes(label):label===target);}
export const displayClass=(label:string)=>label.startsWith('group:')?label.slice(6).replaceAll('_',' '):label;
export function resolveClasses(targets:string[],groups:LabConfig['class_groups']){return Array.from(new Set(targets.flatMap(t=>t.startsWith('group:')?groups?.[t.slice(6)]??[]:[t])));}
