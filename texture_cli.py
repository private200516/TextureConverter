"""Offline batch CLI; shares GUI preview/export algorithms."""
import argparse,json,threading,signal
from config import Config
from options_policy import processing_options
from texture_api import scan_jobs,run_jobs

def main():
    p=argparse.ArgumentParser(description='Texture Converter ALPHA 1.0 - local material and texture adaptation')
    p.add_argument('input',nargs='?');p.add_argument('output',nargs='?')
    p.add_argument('--input-dir',dest='input_dir');p.add_argument('--output-dir',dest='output_dir')
    p.add_argument('--direction',choices=['76-to-4','4-to-76'],default='76-to-4')
    p.add_argument('--metal-mode',choices=['off','auto','manual'],default='off')
    p.add_argument('--options',help='JSON file of explicit metal/color-space options')
    p.add_argument('--dry-run',action='store_true');p.add_argument('--analyze-only',action='store_true')
    p.add_argument('--materials-only',action='store_true');p.add_argument('--no-mipmaps',action='store_true')
    a=p.parse_args();src=a.input_dir or a.input;dst=a.output_dir or a.output
    if not src or not dst:p.error('input and output are required')
    if a.dry_run and a.analyze_only:p.error('choose dry-run or analyze-only')
    cfg=Config(input_dir=src,output_dir=dst,metal_mode=a.metal_mode,generate_mipmap=not a.no_mipmaps,overwrite=False)
    cfg.direction=a.direction
    if a.options:
        with open(a.options,encoding='utf8') as f:cfg.metal_options=processing_options(json.load(f), '--options')
    cancel=threading.Event()
    previous=signal.signal(signal.SIGINT,lambda *_:cancel.set())
    jobs=scan_jobs(src,a.direction,include_textures=not a.materials_only,cancel_event=cancel)
    log=run_jobs(jobs,cfg,log_cb=lambda level,path,msg:print(json.dumps(dict(level=level,path=path,message=msg),ensure_ascii=True),flush=True),cancel_event=cancel,dry_run=a.dry_run,analyze_only=a.analyze_only)
    print(json.dumps(dict(output_dir=log.output_dir,errors=log.errors,game_verified=False)))
    signal.signal(signal.SIGINT,previous)
    return 130 if cancel.is_set() else 1 if log.errors else 0

if __name__=='__main__':raise SystemExit(main())
