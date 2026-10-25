import {lazy,Suspense,useEffect,useState} from 'react';
import Navigation,{type Screen} from './app/Navigation';
import LiveReview from './features/monitoring/LiveReview';
import VideoPreview from './features/monitoring/VideoPreview';
import RecordedReview from './features/safety_review/RecordedReview';
import {request} from './api/client';
import {AiControlProvider} from './hooks/useAiControl';
import {ReviewPreferencesProvider} from './hooks/useReviewPreferences';
import WelcomeGuide, {rememberWelcome, shouldShowWelcome} from './components/WelcomeGuide';

const ChatLab=lazy(()=>import('./features/safety_review/chat_lab/ChatLab'));
export default function App(){
 const [homeEpoch,setHomeEpoch]=useState(0);
 const [guideOpen,setGuideOpen]=useState(shouldShowWelcome);
 function closeGuide(){rememberWelcome();setGuideOpen(false);}
 const [screen,setScreen]=useState<Screen>('Preview'),[ready,setReady]=useState(false),[error,setError]=useState(''),[token,setToken]=useState('');
 async function login(){try{await request('/auth/session',token?{operator_token:token}:{});setReady(true);}catch(e){setError(String(e));}}
 useEffect(()=>{void login();},[]);
 if(!ready)return <main className="consumer-login"><div className="login-card"><h1>Industrial Safety</h1>{error?<form onSubmit={e=>{e.preventDefault();void login();}}><p role="alert">{error}</p><label>Operator token<input type="password" autoFocus value={token} onChange={e=>setToken(e.target.value)}/></label><button type="submit">Open workspace</button></form>:<p>Opening your workspace…</p>}</div></main>;
 if(window.location.pathname==='/test/chat')return <Suspense fallback={<main>Opening Chat Lab…</main>}><ChatLab/></Suspense>;
 return <AiControlProvider><ReviewPreferencesProvider>
  <Navigation screen={screen} onSelect={setScreen} onHome={()=>{setScreen('Preview');setHomeEpoch(value=>value+1);}}/>

   {screen==='Preview'&&<VideoPreview key={homeEpoch} onHelp={()=>setGuideOpen(true)}/>}
   {screen==='Live'&&<LiveReview key={homeEpoch} onHelp={()=>setGuideOpen(true)}/>}
   {screen==='Review'&&<RecordedReview onHelp={()=>setGuideOpen(true)}/>}
   {guideOpen&&<WelcomeGuide onClose={closeGuide}/>}

 </ReviewPreferencesProvider></AiControlProvider>;
}
