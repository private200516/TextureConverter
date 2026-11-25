"""Exercise the material-only workflow through the same buttons as the desktop app."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import config
import main
from material_codec import Material, parse, serialize


class MaterialsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"TEXTURE_CONVERTER_CONFIG": str(self.root / "cfg.json")})
        self.env.start()
        self.w = main.MainWindow()
        self.w.motion.reduced_motion = True
        self.w.motion.startup()
        self.source = self.root / "Data"
        materials = self.source / "materials" / "fixtures"
        materials.mkdir(parents=True)
        self.files = []
        for kind, slot in (("BGSM", "DiffuseTexture"), ("BGEM", "BaseTexture")):
            path = materials / ("sample." + kind.lower())
            path.write_bytes(serialize(Material(kind, 22, {slot: "textures/absent_d.dds"})))
            self.files.append(path)
        (self.source / "orphan_d.dds").write_bytes(b"Not a texture")
        self.w.tabs.setCurrentIndex(self.w.materials_tab_index)
        self.w.materials_input.setText(str(self.source))
        self.w.materials_output.setText(str(self.root / "output"))

    def tearDown(self):
        self.w._closing = True
        self.w.close()
        self.w.deleteLater()
        QTest.qWait(10)
        self.env.stop()
        self.temp.cleanup()

    def finish(self):
        deadline = time.monotonic() + 20
        while (self.w._busy() or not self.w.tabs.isEnabled()) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(self.w.tabs.isEnabled())
        self.assertFalse(self.w._busy())

    def test_inspect_and_convert_only_materials_in_mixed_directory(self):
        self.w.cb_bgsm.setChecked(False)
        self.w.in_edit.setText(str(self.root / "home-only"))
        self.w.btn_analyze.clicked.emit()
        self.finish()
        self.assertFalse(self.w.scanner.include_textures)
        self.assertEqual(len(self.w._prepared_jobs), 2)
        self.assertTrue(all(job["material_path"] for job in self.w._prepared_jobs))
        self.w._on_lang('en')
        self.assertIn('0 with format or direction issues', self.w.materials_summary.text())
        self.w.btn_start.clicked.emit()
        self.finish()
        self.assertEqual(self.w.worker.converter.log.errors, 0)
        self.assertTrue(self.w.worker.converter.scan_reused)
        exported = Path(self.w._last_output)
        self.assertEqual(exported, self.root / "output")
        self.assertTrue(self.w.btn_log.isEnabled())
        self.assertTrue((Path(self.w._last_report_dir) / "conversion_log.txt").is_file())
        for path in self.files:
            converted = parse(exported / path.relative_to(self.source))
            self.assertEqual((converted.kind, converted.version), (path.suffix[1:].upper(), 2))
            self.assertEqual(parse(path).version, 22)
        self.assertEqual(list(exported.rglob("*.dds")), [])
        self.assertEqual(self.w._last_material_source, str(self.source))
        saved = config.load_config()
        self.assertFalse(saved.convert_bgsm)
        self.assertEqual(saved.materials_input, str(self.source))
        self.assertEqual(saved.input_dir, str(self.root / "home-only"))
        self.w.tabs.setCurrentIndex(0)
        self.w.in_edit.setText(str(self.source))
        self.w.btn_analyze.clicked.emit()
        self.finish()
        self.assertTrue(self.w.scanner.include_textures)
        self.assertTrue(any(job["kind"] == "texture_group" for job in self.w._prepared_jobs))

    def test_inspection_flags_malformed_and_target_game_materials(self):
        self.files[0].write_bytes(b'invalid BGSM')
        self.files[1].write_bytes(serialize(Material('BGEM', 2, {})))
        self.w._on_lang('en')
        self.w.btn_analyze.clicked.emit()
        self.finish()
        self.assertIn('2 with format or direction issues', self.w.materials_summary.text())

    def test_file_picker_and_single_file_output(self):
        with patch.object(main.QFileDialog, "getOpenFileName", return_value=(str(self.files[1]), "")):
            self.w.materials_pick_file.click()
        self.w.btn_start.clicked.emit()
        self.finish()
        self.assertEqual(self.w.worker.converter.log.errors, 0)
        exported = Path(self.w._last_output)
        self.assertEqual(len(list(exported.rglob("*.bgem"))), 1)
        self.assertEqual(list(exported.rglob("*.bgsm")), [])
        self.w.materials_input.setText(str(self.source / "orphan_d.dds"))
        with patch.object(QMessageBox, "warning") as warning:
            self.assertIsNone(self.w._validate())
        warning.assert_called_once()

    def test_report_choice_is_shared_persisted_and_controls_real_exports(self):
        self.assertTrue(self.w.cb_report.isVisible() and self.w.cb_report.isChecked())
        self.w._on_lang("en")
        self.assertIn("reports", self.w.cb_report.text())
        self.assertIn("live log", self.w.cb_report.toolTip())
        self.w.cb_report.click()
        self.assertFalse(config.load_config().generate_report)
        self.w.btn_start.clicked.emit()
        self.finish()
        output = self.root / "output"
        self.assertEqual(sorted(p.name for p in output.iterdir()), ["materials"])
        self.assertEqual(self.w.worker.converter.log.errors, 0)
        self.assertFalse(self.w.btn_log.isEnabled())
        self.assertIsNone(self.w._last_report_dir)
        self.w.tabs.setCurrentIndex(0)
        self.w._on_lang("zh")
        self.assertEqual(self.w.cb_report.text(), "保存转换报告")
        self.assertFalse(self.w.cb_report.isChecked())
        self.assertTrue(self.w.cb_report.isVisible())

    def test_category_output_requires_confirmation_and_updates_both_pages(self):
        root = self.root / "My mod"
        root.mkdir()
        category = root / "textures"
        category.mkdir()
        self.w.materials_output.setText(str(category))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Cancel) as question:
            self.assertIsNone(self.w._validate())
        question.assert_called_once()
        self.assertIn(str(root), question.call_args.args[2])
        self.assertEqual(list(root.iterdir()), [category])
        self.assertEqual(self.w.materials_output.text(), str(category))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            settings = self.w._validate()
        self.assertEqual(settings.output_dir, str(root))
        self.assertEqual(self.w.materials_output.text(), self.w.out_edit.text())
        self.assertEqual(config.load_config().output_dir, str(root))

    def test_bilingual_tab_and_action_fit(self):
        self.w.setFixedSize(1060, 940)
        for lang, title in (("en", "Materials"), ("zh", "材质转换")):
            self.w._on_lang(lang)
            self.assertEqual(self.w.tabs.tabText(self.w.materials_tab_index), title)
            for button in (self.w.materials_pick_file, self.w.materials_pick_folder):
                self.assertLess(button.fontMetrics().horizontalAdvance(button.text()), button.width())
                self.assertTrue(button.toolTip())


if __name__ == "__main__":
    unittest.main()
