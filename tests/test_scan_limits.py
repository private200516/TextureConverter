"""Small owned trees check bounded, ordered asset scanning and cancellation."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import material_jobs as jobs


class ScanLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='texture-scan-limits-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'source'
        self.root.mkdir()

    def file(self,relative,payload=b'x'):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(payload)
        return path

    def test_file_order_matches_sorted_files_then_depth_first_directories(self):
        for name in ('z.txt','A.dds','b.bgem','b_dir/z.dds','a_dir/z.dds','a_dir/a.bgsm'):
            self.file(name)
        self.assertEqual([p.relative_to(self.root).as_posix() for p in jobs.walk_files(self.root)],
                         ['A.dds','b.bgem','z.txt','a_dir/a.bgsm','a_dir/z.dds','b_dir/z.dds'])

    def test_entry_budget_stops_stream_before_collecting_whole_directory(self):
        for i in range(12):self.file(f'{i:02}.txt')
        actual=os.scandir;visited=[]
        class Stream:
            def __init__(self,path):self.inner=actual(path)
            def __enter__(self):return self
            def __exit__(self,*args):self.inner.close()
            def __iter__(self):return self
            def __next__(self):
                entry=next(self.inner);visited.append(entry.name);return entry
        with patch('material_jobs.os.scandir',Stream),patch('material_jobs.MAX_SCAN_ENTRIES',3):
            with self.assertRaisesRegex(ValueError,'SCAN_ENTRY_LIMIT.*process in batches'):
                list(jobs.walk_files(self.root))
        self.assertEqual(len(visited),4)

    def test_asset_file_budget_counts_uppercase_and_ignores_unrelated_files(self):
        self.file('first.DDS');self.file('second.BGEM');self.file('third.txt')
        with patch('material_jobs.MAX_SCAN_FILES',1):
            with self.assertRaisesRegex(ValueError,'SCAN_FILE_LIMIT'):list(jobs.walk_files(self.root))
        with patch('material_jobs.MAX_SCAN_FILES',2):
            self.assertEqual(len(list(jobs.walk_files(self.root))),3)

    def test_resource_byte_budget_is_shared_across_directories(self):
        self.file('a/item.bgsm',b'123');self.file('b/item.dds',b'456')
        self.file('large_unrelated.txt',b'x'*100)
        with patch('material_jobs.MAX_SCAN_BYTES',5):
            with self.assertRaisesRegex(ValueError,'SCAN_BYTE_LIMIT'):list(jobs.walk_files(self.root))
        with patch('material_jobs.MAX_SCAN_BYTES',6):
            self.assertEqual(len(list(jobs.walk_files(self.root))),3)

    def test_directory_budget_counts_root_and_pending_directories(self):
        (self.root/'a').mkdir();(self.root/'b').mkdir()
        with patch('material_jobs.MAX_SCAN_DIRECTORIES',2):
            with self.assertRaisesRegex(ValueError,'SCAN_DIRECTORY_LIMIT'):list(jobs.walk_files(self.root))
        with patch('material_jobs.MAX_SCAN_DIRECTORIES',3):
            self.assertEqual(list(jobs.walk_files(self.root)),[])

    def test_pre_cancel_does_not_open_or_enumerate_root(self):
        event=threading.Event();event.set()
        with patch('material_jobs.assert_no_reparse',side_effect=AssertionError('must not open')):
            self.assertEqual(list(jobs.walk_files(self.root,cancel_event=event)),[])

    def test_cancel_during_entry_stream_stops_without_finishing_directory(self):
        for i in range(8):self.file(f'{i}.dds')
        actual=os.scandir;event=threading.Event();visited=[]
        class Stream:
            def __init__(self,path):self.inner=actual(path)
            def __enter__(self):return self
            def __exit__(self,*args):self.inner.close()
            def __iter__(self):return self
            def __next__(self):
                entry=next(self.inner);visited.append(entry.name)
                if len(visited)==2:event.set()
                return entry
        with patch('material_jobs.os.scandir',Stream):
            self.assertEqual(list(jobs.walk_files(self.root,cancel_event=event)),[])
        self.assertEqual(len(visited),2)

    def test_cancel_between_yields_stops_precollected_files(self):
        for name in ('a.dds','b.dds','c.dds'):self.file(name)
        event=threading.Event();walker=jobs.walk_files(self.root,cancel_event=event)
        self.assertEqual(next(walker).name,'a.dds');event.set()
        self.assertEqual(list(walker),[])

    def test_directory_stamps_support_cache_and_detect_new_file(self):
        self.file('materials/a.bgsm');self.file('textures/a.dds')
        stamps={};list(jobs.walk_files(self.root,directory_stamps=stamps))
        self.assertEqual(set(stamps),{str(self.root),str(self.root/'materials'),str(self.root/'textures')})
        context=dict(input_root=str(self.root.resolve()),direction='76-to-4',include_textures=True,directory_stamps=stamps)
        self.assertTrue(jobs.scan_context_matches(context,self.root))
        self.file('textures/new.dds')
        self.assertFalse(jobs.scan_context_matches(context,self.root))

    def test_single_resource_file_obeys_budget_and_records_stamp(self):
        path=self.file('one.dds',b'1234');stamps={}
        self.assertEqual(list(jobs.walk_files(path,directory_stamps=stamps)),[path])
        self.assertEqual(stamps,{str(path):jobs._stamp(path)})
        with patch('material_jobs.MAX_SCAN_FILES',0):
            with self.assertRaisesRegex(ValueError,'SCAN_FILE_LIMIT'):list(jobs.walk_files(path))
        with patch('material_jobs.MAX_SCAN_BYTES',3):
            with self.assertRaisesRegex(ValueError,'SCAN_BYTE_LIMIT'):list(jobs.walk_files(path))

    def test_directory_reparse_is_skipped_and_not_stamped(self):
        outside=Path(self.temp.name)/'outside';outside.mkdir();(outside/'hidden.dds').write_bytes(b'x')
        self.file('visible.dds');link=self.root/'linked'
        if os.name=='nt':
            shell=str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/cmd.exe')
            result=subprocess.run([shell,'/d','/c','mklink','/J',str(link),str(outside)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False)
            if result.returncode:self.skipTest('Owned junction fixture unavailable')
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(outside,target_is_directory=True);self.addCleanup(link.unlink)
        stamps={}
        self.assertEqual(list(jobs.walk_files(self.root,directory_stamps=stamps)),[self.root/'visible.dds'])
        self.assertNotIn(str(link),stamps)


if __name__=='__main__':unittest.main()
