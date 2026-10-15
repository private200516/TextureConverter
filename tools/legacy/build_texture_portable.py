"""GPL-3.0: transparent offline folder build; no executable archive/packer."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

SOURCE=Path(__file__).resolve().parent
PROJECT=SOURCE.parents[1]
RUNTIME=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python'
WORK=PROJECT/'work/compatibility-20261006'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):h.update(data)
    return h.hexdigest()

def copy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)

def ignore(directory,names):
    return [n for n in names if n in ('__pycache__','tests','test','.git','idlelib','tkinter','turtledemo','ensurepip','venv','site-packages') or n.endswith(('.pyc','.pyo'))]

QT_MODULES={'QtCore','QtGui','QtWidgets','QtTest','QtOpenGL','QtOpenGLWidgets','QtSvg'}
QT_DLLS={'Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll','Qt6Test.dll','Qt6OpenGL.dll',
         'Qt6OpenGLWidgets.dll','Qt6Svg.dll','pyside6.abi3.dll','opengl32sw.dll'}
QT_PLUGIN_DIRS={'platforms','styles','imageformats','iconengines'}


def ignore_qt(directory,names):
    # A transparent deployment of existing Qt binaries, not another packaging
    # layer. Keep native Widgets/OpenGL, image plugins and unmodified notices.
    skipped=set(ignore(directory,names))
    base=WORK/'qt-vendor';relative=Path(directory).relative_to(base).as_posix()
    if relative=='PySide6':
        for name in names:
            path=Path(directory)/name
            if path.is_dir():
                if name not in {'support','plugins','translations'}:skipped.add(name)
            elif path.suffix.lower()=='.pyd' and path.stem not in QT_MODULES:skipped.add(name)
            elif path.suffix.lower()=='.pyi' and path.stem not in QT_MODULES|{'__feature__'}:skipped.add(name)
            elif path.suffix.lower() in {'.exe','.lib'}:skipped.add(name)
            elif path.suffix.lower()=='.dll' and name not in QT_DLLS and not name.lower().startswith(('msvcp','vcruntime','concrt','vcamp','vcomp','vccorlib')):skipped.add(name)
    elif relative=='PySide6/plugins':
        skipped.update(n for n in names if n not in QT_PLUGIN_DIRS)
    elif relative=='PySide6/plugins/imageformats':
        skipped.update(n for n in names if n=='qpdf.dll')
    elif relative=='PySide6/translations':
        skipped.update(n for n in names if not n.startswith('qtbase_'))
    return sorted(skipped)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=PROJECT/'releases/SpeedPreview_20261007/TextureConverter')
    parser.add_argument('--no-zip',action='store_true')
    args=parser.parse_args();out=args.output.resolve()
    if out.exists():raise FileExistsError('Use a fresh output directory: '+str(out))
    out.mkdir(parents=True)
    record={'built_utc':datetime.now(timezone.utc).isoformat(),'source':str(SOURCE),'version':'ALPHA 1.0','packer':None,'onefile_extraction':False,'runtime_network_downloads':False,'input_game_assets':False,'game_validated':False,'dependencies':[]}
    record['packaging']={'layout':'flat ZIP root','max_zip_member_chars':100,'excluded_qt_dirs':['qml','include','doc','glue','metatypes','scripts','typesystems'],'reason':'Qt Widgets/OpenGL deployment with short Windows paths','qt_modules':sorted(QT_MODULES),'qt_plugins':sorted(QT_PLUGIN_DIRS),'sdk_executables':False}
    # Python standard runtime is copied without executing any package installation code.
    for name in ('python.exe','pythonw.exe','python3.dll','python312.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
        copy(RUNTIME/name,out/'runtime'/name)
    shutil.copytree(RUNTIME/'Lib',out/'runtime/Lib',ignore=ignore)
    shutil.copytree(RUNTIME/'DLLs',out/'runtime/DLLs',ignore=ignore)
    (out/'runtime/python312._pth').write_text('.\nLib\nDLLs\n../vendor\n../app\nimport site\n',encoding='ascii')
    record['runtime']={'version':subprocess.check_output([str(RUNTIME/'python.exe'),'-I','--version'],text=True).strip(),'source':'Local bundled runtime; not downloaded by this builder','python_sha256':sha(RUNTIME/'python.exe')}
    shutil.copytree(WORK/'qt-vendor',out/'vendor',ignore=ignore_qt)
    for p in (WORK/'qt-wheels').glob('*.whl'):record['dependencies'].append({'file':p.name,'sha256':sha(p),'source':'PyPI official Qt distribution'})
    packages=RUNTIME/'Lib/site-packages'
    for name in ('numpy','numpy.libs','numpy-2.3.5.dist-info','PIL','pillow-12.3.0.dist-info'):
        shutil.copytree(packages/name,out/'vendor'/name,ignore=ignore)
    record['dependencies'] += [{'name':'NumPy','version':'2.3.5','source':str(packages/'numpy')},{'name':'Pillow','version':'12.3.0','source':str(packages/'PIL')}]
    for p in SOURCE.iterdir():
        if p.is_file() and p.suffix in ('.py','.txt','.md','.cs') or p.name=='LICENSE':copy(p,out/'app'/p.name)
    shutil.copytree(SOURCE/'assets',out/'app/assets',ignore=ignore)
    shutil.copytree(SOURCE/'licenses',out/'licenses')
    shutil.copytree(SOURCE/'tests',out/'app/tests',ignore=lambda d,n:[x for x in n if x=='__pycache__'])
    copy(SOURCE/'tools/texconv.exe',out/'app/tools/texconv.exe')
    for helper in (SOURCE/'tools').glob('*.py'):copy(helper,out/'app/tools'/helper.name)
    record['texconv']={'sha256':sha(SOURCE/'tools/texconv.exe'),'source':'Restored original converter bundle; DirectXTex texconv executable','authenticode_status_at_audit':'Valid'}
    for name in ('LICENSE','THIRD_PARTY_NOTICES.txt','README.md','development_audit.txt','METAL_WORKFLOW_REPORT.txt','UI_PERFORMANCE_REPORT.txt','SPEED_PREVIEW_NOTES.txt'):
        if (SOURCE/name).is_file():copy(SOURCE/name,out/name)
    # Default config is generated from defaults, never from the user's paths/masks.
    sys.path.insert(0,str(SOURCE));from config import Config
    (out/'app/config.json').write_text(Config().to_json(),encoding='utf8')
    launch_source=SOURCE/'TextureLauncher.cs'
    csc=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    manifest=out/'app/launcher.manifest'
    manifest.write_text('''<?xml version="1.0" encoding="utf-8"?><assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1"><assemblyIdentity version="1.0.0.0" name="private0516.TextureConverter"/><trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo></assembly>''',encoding='utf8')
    command=[str(csc),'/nologo','/target:winexe','/platform:x64','/optimize+','/reference:System.Windows.Forms.dll','/out:'+str(out/'TextureConverter.exe'),'/win32manifest:'+str(manifest),'/win32icon:'+str(SOURCE/'assets/icon.ico'),str(launch_source)]
    subprocess.run(command,check=True)
    record['launcher']={'bytes':(out/'TextureConverter.exe').stat().st_size,'sha256':sha(out/'TextureConverter.exe'),'elevation':'asInvoker','signed':False}
    (out/'BUILD_INFO.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
    hashes=[sha(p)+'  '+p.relative_to(out).as_posix() for p in sorted(out.rglob('*')) if p.is_file()]
    (out/'SHA256SUMS.txt').write_text('\n'.join(hashes)+'\n',encoding='utf8')
    if not args.no_zip:
        archive=out.parent/'TextureConverter.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for p in sorted(out.rglob('*')):
                if p.is_file():
                    relative=p.relative_to(out).as_posix()
                    if len(relative)>100:raise ValueError('Distribution path exceeds 100 characters: '+relative)
                    z.write(p,relative)
        archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n',encoding='ascii')
        print(archive)
    print(out)

if __name__=='__main__':main()
