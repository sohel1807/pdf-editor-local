import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

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

    def test_removes_expired_app_pdfs_and_preserves_other_files(self):
        now = 100_000
        expired_path = self.make_file(f"{uuid.uuid4()}.pdf", now - main.PDF_RETENTION_SECONDS)
        recent_path = self.make_file(f"{uuid.uuid4()}.pdf", now - main.PDF_RETENTION_SECONDS + 1)
        unmanaged_path = self.make_file("keep.pdf", now - 48 * 60 * 60)
        other_file_path = self.make_file(f"{uuid.uuid4()}.txt", now - 48 * 60 * 60)

        removed = main.remove_expired_pdf_files(self.temp_dir.name, now=now)

        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(expired_path))
        self.assertTrue(os.path.exists(recent_path))
        self.assertTrue(os.path.exists(unmanaged_path))
        self.assertTrue(os.path.exists(other_file_path))

    def test_delete_document_removes_and_commits_the_current_pdf(self):
        doc_id = str(uuid.uuid4())
        path = self.make_file(f"{doc_id}.pdf", 100_000)

        with patch.object(main, "WORK", self.temp_dir.name), patch.object(main, "pdf_volume") as volume:
            response = main.delete_document(doc_id)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(os.path.exists(path))
        volume.reload.assert_called_once()
        volume.commit.assert_called_once()
