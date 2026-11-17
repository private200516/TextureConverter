"""Internal native/offscreen Qt smoke. Uses only this application's own widgets.

Run with the normal source dependencies on sys.path:
    python tests/qt_smoke.py --output <validation directory> [--offscreen]
Configuration, masks and local data are isolated in a temporary directory.
The log load is synthetic; this script does not claim to test DDS conversion.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--app-root',help='Import the source or portable app directory to validate')
    parser.add_argument('--offscreen',action='store_true')
    parser.add_argument('--reduced-motion',action='store_true')
    args=parser.parse_args()
    if args.offscreen:os.environ['QT_QPA_PLATFORM']='offscreen'
    os.environ['CONVERTER_REDUCED_MOTION']='1' if args.reduced_motion else '0'
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    app_root=Path(args.app_root).resolve() if args.app_root else Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(app_root))
    from PySide6.QtCore import QTimer,Qt,QObject,QEvent
    from PySide6.QtWidgets import QApplication
    import config
    import main
    import ui_theme as theme
    from ui_events import UiEventBuffer

    result={'reduced_motion':args.reduced_motion,'screenshots':[],'errors':[],
        'app_root':str(app_root),
        'main_file':str(Path(main.__file__).resolve()),
        'motion_timeline_ms':{'startup':theme.MOTION_STARTUP_MS,'shutdown':theme.MOTION_SHUTDOWN_MS,
            'page':theme.MOTION_REVEAL_MS,'dialog_close':theme.MOTION_DIALOG_CLOSE_MS},
        'captured_phases':[],
        'source_sha256':{name:hashlib.sha256((app_root/name).read_bytes()).hexdigest()
                         for name in ('main.py','app_motion.py','ui_widgets.py','ui_theme.py')}}
    with tempfile.TemporaryDirectory(prefix='texture-qt-smoke-') as temporary:
        os.environ['TEXTURE_CONVERTER_CONFIG']=str(Path(temporary)/'config.json')
        os.environ['LOCALAPPDATA']=str(Path(temporary)/'local')
        app=QApplication([]);app.setQuitOnLastWindowClosed(False)
        result['qt_platform']=app.platformName()
        result['fonts_registered']=sorted(set(config.register_fonts()))
        window=main.MainWindow()
        intervals=[];feed_cost=[];last=[time.perf_counter()];count=[0]
        mailbox=UiEventBuffer()
        fake=SimpleNamespace(events=mailbox,warning_count=0,error_count=0,isRunning=lambda:False)
        dialog=[None]

        class PaintCounter(QObject):
            paints=0
            def eventFilter(self,obj,event):
                if event.type()==QEvent.Paint:self.paints+=1
                return False
        paints=PaintCounter(window);window.logview.installEventFilter(paints)

        def capture(name,widget=window):
            path=output/(name+'.png')
            if not widget.grab().save(str(path)):raise RuntimeError('Could not save '+str(path))
            result['screenshots'].append(path.name)
            overlay=window.motion.overlay
            result['captured_phases'].append({'name':name,'state':window.motion.state,
                'phase':overlay.phase if overlay is not None else None,
                'elapsed_ms':round(overlay.elapsed,1) if overlay is not None else None,
                'native_opacity':round(window.windowOpacity(),3)})

        def heartbeat():
            now=time.perf_counter();intervals.append((now-last[0])*1000);last[0]=now
        clock=QTimer(interval=16,timeout=heartbeat);clock.setTimerType(Qt.PreciseTimer);clock.start()

        def finish():
            feed.stop();clock.stop()
            result['closed']=not window.isVisible() and window.motion.closed
            result['synthetic_entries_produced']=count[0]
            result['live_entries_retained']=len(window.logview._entries)
            result['log_paints']=paints.paints
            if intervals:
                ordered=sorted(intervals)
                result['heartbeat_ms']={'median':round(statistics.median(ordered),2),
                    'p95':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],2),'max':round(max(ordered),2)}
            if feed_cost:result['feed_batch_ms_max']=round(max(feed_cost),3)
            if not result['closed']:result['errors'].append('Animated close did not finish')
            (output/'smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if dialog[0] is not None:dialog[0].close()
            window.motion.dispose();window._closing=True;window.close();app.quit()

        def schedule(milliseconds,callback):
            def guarded():
                try:callback()
                except Exception:
                    result['errors'].append(traceback.format_exc());finish()
            QTimer.singleShot(milliseconds,guarded)

        def advanced():
            window.lang_pills.set_key('en',animate=False)
            window.tabs.setCurrentIndex(window.advanced_tab_index)
        def about():
            dialog[0]=main.TextDialog('About & licenses',
                'Author: private0516\nVersion: ALPHA 1.0\n\nDeveloped using GPT-6 Astra.\nProject code: GPL-3.0\n\nSee bundled LICENSE and THIRD_PARTY_NOTICES.txt.',window)
            dialog[0].show()
        def load():
            window.tabs.setCurrentIndex(0)
            window.worker=fake;window._active_task=fake;window._begin_task('running');feed.start()
        def produce():
            started=time.perf_counter()
            for _ in range(250):
                count[0]+=1;mailbox.log('OK',f'textures/synthetic_{count[0]:06}.dds','synthetic UI load')
            mailbox.progress(count[0],20000,'synthetic UI load')
            feed_cost.append((time.perf_counter()-started)*1000)
        feed=QTimer(interval=16,timeout=produce);feed.setTimerType(Qt.PreciseTimer)
        def minimize():
            window.showMinimized()
        def check_minimized():
            result['minimized_frame_timers_stopped']=all(not widget._timer.isActive() for widget in (window.statusbar,window.banner,window.logview))
            if not result['minimized_frame_timers_stopped']:raise AssertionError('A hidden frame timer remains active')
            window.showNormal()
        def settle():
            feed.stop();window._drain_worker_events(final=True);window.worker=None;window._active_task=None
            window.statusbar.set_state('done');window.proc_panel.set_stage('done')
            window.proc_panel.set_progress(count[0],count[0],'synthetic UI load')
            window.banner.set_count(count[0],count[0]);window.logview.set_running(False);window.banner.set_running(False)
            window._re_enable()
        def idle():
            capture('06-completed-home')
            result['idle_subsecond_timers']=[timer.interval() for timer in window.findChildren(QTimer) if timer.isActive() and timer.interval()<1000]
            if result['idle_subsecond_timers']:raise AssertionError('An idle frame timer remains active')

        window.motion.startup()
        for index,milliseconds in enumerate((120,360,560,760,1040,1280)):
            schedule(milliseconds,lambda i=index,t=milliseconds:capture(f'01-startup-{i+1}-{t:04}ms'))
        home=theme.MOTION_STARTUP_MS+180
        schedule(home,lambda:capture('02-home-zh'))
        schedule(home+150,advanced)
        schedule(home+150+theme.MOTION_REVEAL_MS+80,lambda:capture('03-advanced-en'))
        dialog_start=home+650
        schedule(dialog_start,about)
        schedule(dialog_start+120,lambda:capture('04-about-opening',dialog[0]))
        schedule(dialog_start+theme.MOTION_REVEAL_MS+80,lambda:capture('04-about',dialog[0]))
        dialog_close=dialog_start+theme.MOTION_REVEAL_MS+160
        schedule(dialog_close,lambda:dialog[0].close())
        schedule(dialog_close+100,lambda:capture('04-about-closing',dialog[0]))
        load_start=dialog_close+theme.MOTION_DIALOG_CLOSE_MS+100
        schedule(load_start,load)
        schedule(load_start+600,minimize);schedule(load_start+720,check_minimized)
        schedule(load_start+1050,lambda:capture('05-live-load'))
        schedule(load_start+1200,settle)
        schedule(load_start+1650,idle)
        close_start=load_start+1830
        schedule(close_start,window.close)
        for index,milliseconds in enumerate((120,260,380,470,565,610)):
            schedule(close_start+milliseconds,lambda i=index,t=milliseconds:capture(f'07-shutdown-{i+1}-{t:04}ms'))
        schedule(close_start+theme.MOTION_SHUTDOWN_MS+200,finish)
        schedule(close_start+theme.MOTION_SHUTDOWN_MS+6000,lambda:(_ for _ in ()).throw(TimeoutError('Smoke deadline exceeded')))
        app.exec()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result['errors'] else 0


if __name__=='__main__':raise SystemExit(run())
