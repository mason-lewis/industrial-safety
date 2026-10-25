import {useEffect, useRef, type ReactNode} from 'react';
import Icon from './ReviewIcon';
export default function ReviewDialog({title, onClose, children, wide=false, full=false, actions}: {title:string; onClose:()=>void; children:ReactNode; wide?:boolean; full?:boolean; actions?:ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const dialog=ref.current!;
    const previousFocus=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const previousOverflow=document.body.style.overflow;
    dialog.showModal();
    dialog.querySelector<HTMLElement>('[data-dialog-autofocus]')?.focus();
    document.body.style.overflow='hidden';
    return()=>{
      dialog.close();
      document.body.style.overflow=previousOverflow;
      if(previousFocus?.isConnected)previousFocus.focus();
    };
  },[]);
  return <dialog ref={ref} className={`review-dialog${wide?' review-dialog-wide':''}${full?' review-dialog-full':''}`} aria-label={title} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget)onClose();}}><div className="dialog-surface"><header><h2>{title}</h2>{actions&&<div className="dialog-actions">{actions}</div>}<button className="quiet-icon" aria-label="Close dialog" onClick={onClose}><Icon name="close"/></button></header>{children}</div></dialog>;
}
