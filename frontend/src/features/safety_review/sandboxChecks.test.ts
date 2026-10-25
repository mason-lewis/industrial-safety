import {describe,it,expect} from 'vitest';
import {readChecks,saveCheck,type SandboxCheck} from './sandboxChecks';

function storage(){const entries=new Map<string,string>();return {getItem:(key:string)=>entries.get(key)??null,setItem:(key:string,value:string)=>{entries.set(key,value);},entries};}
const check:SandboxCheck={jobId:'test-1',analysisId:'answer-1',verdict:'matches',notes:'Compared against the cited frame.',createdAt:'2026-10-08T19:00:00Z'};

describe('personal sandbox validation',()=>{
 it('appends corrections and keeps each test isolated',()=>{
  const local=storage();saveCheck(local,check);saveCheck(local,{...check,verdict:'unsure',notes:'Need the original clip.'});
  expect(readChecks(local,'test-1')).toEqual([check,{...check,verdict:'unsure',notes:'Need the original clip.'}]);
  expect(readChecks(local,'test-2')).toEqual([]);
 });
 it('preserves unreadable history rather than overwriting it',()=>{
  const local=storage();local.setItem('video-sandbox-checks-v1:test-1','broken historical data');
  expect(()=>saveCheck(local,check)).toThrow();
  expect(local.getItem('video-sandbox-checks-v1:test-1')).toBe('broken historical data');
 });
 it('rejects foreign-run checks without changing stored data',()=>{
  const local=storage();local.setItem('video-sandbox-checks-v1:test-1',JSON.stringify([{...check,jobId:'another-test'}]));
  expect(()=>readChecks(local,'test-1')).toThrow('Existing browser data was kept');
  expect(()=>saveCheck(local,check)).toThrow();
 });
 it('surfaces storage failures so a check is not presented as saved',()=>{
  const full={getItem:()=>null,setItem:()=>{throw new Error('Storage full');}};
  expect(()=>saveCheck(full,check)).toThrow('Storage full');
 });
});
