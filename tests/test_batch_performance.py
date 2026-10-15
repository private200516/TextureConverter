"""Batch-scale correctness, without depending on a wall-clock performance threshold."""
import csv,json,os,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from material_codec import Material,serialize
from material_jobs import scan_jobs,scan_context_matches,PreparedJobs
from texture_api import run_jobs,run_job
from converter import Converter
from config import Config
from logger import ConversionLog

class NoDeepCopy(dict):
    def __deepcopy__(self,memo):raise AssertionError('Do not copy all job overrides per job')

class BatchPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.source=self.base/'input';self.out=self.base/'output'
        (self.source/'materials').mkdir(parents=True);(self.source/'textures').mkdir()
        self.file=self.source/'materials/a.bgsm';self.file.write_bytes(serialize(Material('BGSM',22)))
        self.cfg=Config(input_dir=str(self.source),output_dir=str(self.out))
    def tearDown(self):self.temp.cleanup()
    def fake(self,job,*a,**kw):
        self.assertNotIn('job_overrides',kw.get('options',{}));self.assertNotIn('metal_options',kw.get('options',{}))
        return dict(id=job['id'],name=job['name'],status='committed',outputs=[],warnings=[],validation={})
    def test_prepared_queue_reused(self):
        jobs=scan_jobs(self.source);self.assertIsInstance(jobs,PreparedJobs)
        engine=Converter(self.cfg,jobs=jobs)
        with patch('texture_api.scan_jobs',side_effect=AssertionError('redundant scan')),patch('texture_api.run_job',side_effect=self.fake):log=engine.run()
        self.assertTrue(engine.scan_reused);self.assertEqual(log.ok,1)
    def test_new_file_invalidates_queue(self):
        jobs=scan_jobs(self.source)
        (self.source/'materials/new.bgsm').write_bytes(self.file.read_bytes())
        engine=Converter(self.cfg,jobs=jobs)
        with patch('texture_api.run_job',side_effect=self.fake):log=engine.run()
        self.assertFalse(engine.scan_reused);self.assertEqual(log.ok,2)
    def test_missing_reference_added_outside_scan_root_invalidates(self):
        self.file.write_bytes(serialize(Material('BGSM',22,dict(DiffuseTexture='textures/sub/new.dds'))))
        jobs=scan_jobs(self.source/'materials')
        (self.source/'textures/sub').mkdir()
        self.assertFalse(scan_context_matches(jobs.scan_context,self.source/'materials'))
    def test_existing_material_changed_is_rejected_despite_same_file_stamp(self):
        jobs=scan_jobs(self.source);stamp=self.file.stat()
        self.file.write_bytes(serialize(Material('BGSM',22,dict(Alpha=.3))))
        os.utime(self.file,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'MATERIAL_CHANGED'):run_job(jobs[0],self.out)
    def test_context_scope_and_direction_must_match(self):
        jobs=scan_jobs(self.source)
        self.assertFalse(scan_context_matches(jobs.scan_context,self.source,'4-to-76'))
        self.assertFalse(scan_context_matches(jobs.scan_context,self.source,include_textures=False))
    def test_full_reports_survive_bounded_ram_log(self):
        template=scan_jobs(self.source)[0];jobs=[dict(template,id=str(i),name='job-'+str(i)) for i in range(40)]
        self.cfg.job_overrides=NoDeepCopy({str(i):{'reconstruction_strength':.2} for i in range(40)})
        with patch('texture_api.run_job',side_effect=self.fake),patch('texture_api.ConversionLog',side_effect=lambda:ConversionLog(max_entries=5)):
            log=run_jobs(jobs,self.cfg)
        reports=Path(log.output_dir)/'reports';report=json.loads((reports/'report.json').read_text())
        self.assertEqual(len(log.entries),5);self.assertEqual(log.total_entries,40);self.assertEqual(report['processed'],40)
        self.assertEqual(len(report['results']),40);self.assertEqual(len(json.loads((reports/'validation_results.json').read_text())),40)
        self.assertEqual(len(json.loads((reports/'conversion_manifest.json').read_text())),40)
        with (reports/'report.csv').open(encoding='utf-8-sig') as stream:self.assertEqual(len(list(csv.reader(stream))),41)
        self.assertEqual((reports/'conversion_log.txt').read_text(encoding='utf8').count('[OK]'),40)
    def test_cancel_during_planning_finalizes_partial_report(self):
        jobs=scan_jobs(self.source);event=threading.Event()
        def progress(*_):event.set()
        log=run_jobs(jobs,self.cfg,progress_cb=progress,cancel_event=event)
        report=json.loads((Path(log.output_dir)/'reports/report.json').read_text())
        self.assertTrue(report['cancelled']);self.assertEqual(report['processed'],0);self.assertTrue((Path(log.output_dir)/'PARTIAL').is_file())
    def test_bad_mask_isolated_to_single_job(self):
        template=scan_jobs(self.source)[0];jobs=[dict(template,id='bad'),dict(template,id='good')]
        self.cfg.job_overrides={'bad':{'edit_mask_path':str(self.base/'missing.png')}}
        with patch('texture_api.run_job',side_effect=self.fake):log=run_jobs(jobs,self.cfg)
        self.assertEqual(log.errors,1);self.assertEqual(log.ok,1)
    def test_shared_reference_metadata_bounded_with_exact_count(self):
        payload=serialize(Material('BGSM',22,dict(DiffuseTexture='textures/shared.dds')))
        (self.source/'textures/shared.dds').write_bytes(b'placeholder')
        for i in range(40):(self.source/'materials'/f'{i}.bgsm').write_bytes(payload)
        jobs=scan_jobs(self.source)
        shared=next(job for job in jobs if job['texture_paths'])
        self.assertEqual(shared['shared_reference_counts']['d'],40)
        self.assertEqual(len(shared['shared_references']['d']),32);self.assertTrue(shared['shared_references_truncated'])

if __name__=='__main__':unittest.main()
