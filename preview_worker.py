"""Fixed local worker entry point. No shell, dynamic entry point or pickle IPC."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))


def main():
    # Parent assigns the Job Object before allowing parsing/native decoding.
    if sys.stdin.buffer.read(1)!=b'1':return 2
    directory=Path(sys.argv[1])
    from preview_process import read_wire,write_wire
    try:
        request=read_wire(directory,'request')
        if request['kind']=='nif':
            from nif_preview_data import load_scene
            result=load_scene(*request['args'])
        elif request['kind']=='metal':
            from texture_api import preview_job
            result=preview_job(*request['args'])
            # Qt consumes 900px thumbnails. Keep exact source-sized candidate
            # masks but avoid transporting full-sized comparison view copies.
            from PIL import Image
            import numpy as np
            for key,value in result.get('views',{}).items():
                data=np.asarray(value)
                if data.dtype!=np.uint8:data=np.rint(np.clip(data,0,1)*255).astype(np.uint8)
                image=Image.fromarray(data);image.thumbnail((900,900),Image.Resampling.BILINEAR)
                result['views'][key]=np.asarray(image).copy()
        else:raise ValueError('Invalid preview action')
        write_wire(directory,'response',result)
        return 0
    except Exception as exc:
        (directory/'error.txt').write_text((type(exc).__name__+': '+str(exc))[:4000],encoding='utf8')
        return 1


if __name__=='__main__':raise SystemExit(main())
