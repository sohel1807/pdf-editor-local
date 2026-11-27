import os
import tempfile
import unittest
import uuid

import main


class VolumeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_file(self, name, modified_at):
        path = os.path.join(self.temp_dir.name, name)
        with open(path, "wb") as file:
            file.write(b"test")
        os.utime(path, (modified_at, modified_at))
        return path

    def test_removes_only_app_pdfs_at_least_24_hours_old(self):
        now = 100_000
        expired_path = self.make_file(f"{uuid.uuid4()}.pdf", now - 24 * 60 * 60)
        recent_path = self.make_file(f"{uuid.uuid4()}.pdf", now - 23 * 60 * 60)
        unmanaged_path = self.make_file("keep.pdf", now - 48 * 60 * 60)
        other_file_path = self.make_file(f"{uuid.uuid4()}.txt", now - 48 * 60 * 60)

        removed = main.remove_expired_pdf_files(self.temp_dir.name, now=now)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(expired_path))
        self.assertTrue(os.path.exists(recent_path))
        self.assertTrue(os.path.exists(unmanaged_path))
        self.assertTrue(os.path.exists(other_file_path))
