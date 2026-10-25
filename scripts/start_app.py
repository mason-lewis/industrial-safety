"""One local app process group. Ctrl+C stops the worker and API together."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(PROJECT/'backend'),str(PROJECT/'backend/src')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    permission=parser.add_mutually_exclusive_group()
    permission.add_argument('--allow-hosted',action='store_true',help='Compatibility flag: real AI is enabled by default')
    permission.add_argument('--offline',action='store_true',help='Disable hosted analysis for local fixture verification')
    parser.add_argument('--workspace',type=Path,default=PROJECT,help='Separate data/config root for a clean trial')
    parser.add_argument('--chat-testing',action='store_true',help='Enable the fixture-default Chat Lab and dedicated worker')
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    from safety_app.infrastructure.environment import load_environment
    load_environment(PROJECT)
    env=dict(os.environ)
    env.update(SAFETY_CHAT_TESTING='1' if args.chat_testing else '0',SAFETY_PAID_REVIEW='0' if args.offline else '1',AI_ALWAYS_ON='false',
               AI_ENABLED='false',HOSTED_IMAGES_ALLOWED='false',SAFETY_WORKSPACE=str(args.workspace.resolve()),
               PYTHONPATH=os.pathsep.join((str(PROJECT/'backend'),str(PROJECT/'backend/src'))))
    from safety_app.infrastructure.demo import prepare_demo
    prepare_demo(args.workspace.resolve(),PROJECT)
    import shutil
    target=args.workspace.resolve()/'configs/video-chat.json'
    if not target.exists():shutil.copy2(PROJECT/'configs/video-chat.json',target)
    if not (PROJECT/'frontend/dist/index.html').is_file():
        raise SystemExit('Run npm --prefix frontend run build first, or use ./start')
    if args.chat_testing:
        import shutil
        target=args.workspace.resolve()/'configs/chat-lab.json'
        if not target.exists():shutil.copy2(PROJECT/'configs/chat-lab.json',target)
        print(f'Chat Lab (fixture default): http://127.0.0.1:{args.port}/test/chat',flush=True)
    processes=[]
    interrupted=False
    def stop(*_):
        nonlocal interrupted
        if _: interrupted=True
        for process in processes:
            if process.poll() is None:process.send_signal(signal.SIGINT)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','safety_app.api.video_app:app','--host','127.0.0.1','--port',str(args.port),'--no-access-log'],env=env,cwd=PROJECT,start_new_session=True))
        processes.append(subprocess.Popen([sys.executable,'-m','safety_app.workers.review','--kind','video'],env=env,cwd=PROJECT,start_new_session=True))
        processes.append(subprocess.Popen([sys.executable,'-m','safety_app.workers.review','--kind','video-chat'],env=env,cwd=PROJECT,start_new_session=True))
        if args.chat_testing:processes.append(subprocess.Popen([sys.executable,'-m','safety_app.workers.review','--kind','chat'],env=env,cwd=PROJECT,start_new_session=True))
        print(f'Industrial Safety: http://127.0.0.1:{args.port} · real AI {"disabled (offline)" if args.offline else "enabled in Live/Review; Preview stays local; global stop and budgets apply"}',flush=True)
        while all(p.poll() is None for p in processes):time.sleep(.25)
        failed=any(p.poll() not in (None,0) for p in processes)
    finally:
        stop()
        for process in processes:
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
    if failed and not interrupted:raise SystemExit(1)


if __name__=='__main__':main()
