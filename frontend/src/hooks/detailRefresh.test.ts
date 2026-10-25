import {afterEach,describe,expect,it,vi} from 'vitest';
import {startDetailRefresh} from './detailRefresh';
describe('asynchronous selected review updates',()=>{
 afterEach(()=>vi.useRealTimers());
 it('shows a result that completes after the immediate queued response',async()=>{vi.useFakeTimers();let report:string|null=null;const fetch=vi.fn().mockResolvedValueOnce({report:null}).mockResolvedValueOnce({report:'completed'});const stop=startDetailRefresh<{report:string|null}>(fetch,d=>{report=d.report;});await vi.advanceTimersByTimeAsync(2000);expect(report).toBe(null);await vi.advanceTimersByTimeAsync(2000);expect(report).toBe('completed');stop();});
 it('does not restore stale detail after selection or generation changes',async()=>{vi.useFakeTimers();let resolve!:(v:string)=>void;const commit=vi.fn();const stop=startDetailRefresh(()=>new Promise<string>(r=>{resolve=r;}),commit);await vi.advanceTimersByTimeAsync(2000);stop();resolve('old generation');await Promise.resolve();expect(commit).not.toHaveBeenCalled();});
 it('does not overlap slow detail requests',async()=>{vi.useFakeTimers();const fetch=vi.fn(()=>new Promise(()=>{}));const stop=startDetailRefresh(fetch,vi.fn());await vi.advanceTimersByTimeAsync(8000);expect(fetch).toHaveBeenCalledTimes(1);stop();});
});
