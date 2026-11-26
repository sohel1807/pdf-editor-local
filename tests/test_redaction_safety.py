import os
import tempfile
import unittest
import uuid

import pymupdf

import main


class RedactionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_path = os.path.join(self.temp_dir.name, "source.pdf")
        document = pymupdf.open()
        page = document.new_page(width=400, height=180)
        target_width = pymupdf.Font(fontname="helv").text_length("TARGET", fontsize=12)
        page.insert_text((60, 68), "ABOVE", fontname="helv", fontsize=12)
        page.insert_text((60, 80), "TARGET", fontname="helv", fontsize=12)
        page.insert_text((60, 94), "BELOW", fontname="helv", fontsize=12)
        page.insert_text((60, 120), "ONE TWO", fontname="helv", fontsize=12)
        page.insert_text((0, 80), "LEFT", fontname="helv", fontsize=12)
        page.insert_text((60 + target_width + 24, 80), "RIGHT", fontname="helv", fontsize=12)
        page.insert_text((200, 80), "MiXeD", fontname="helv", fontsize=12)
        document.save(self.source_path)
        document.close()
        self.doc_id = str(uuid.uuid4())
        main.documents[self.doc_id] = self.source_path

    def tearDown(self):
        main.documents.pop(self.doc_id, None)
        self.temp_dir.cleanup()

    def edit_for(self, block, replacement):
        return {
            "page": 0,
            "bbox": block["bbox"],
            "origin": block["origin"],
            "originalFont": block["font"],
            "originalText": block["text"],
            "sourceIds": block["sourceIds"],
            "originalSize": block["size"],
            "fontFlags": block["fontFlags"],
            "wordGap": block["wordGap"],
            "font": "original",
            "size": block["size"],
            "color": block["color"],
            "align": "left",
            "text": replacement,
        }

    def test_keeps_unselected_neighboring_text(self):
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        target = next(block for block in blocks if block["text"] == "TARGET")

        document = pymupdf.open(self.source_path)
        try:
            main.apply_edits(document, [self.edit_for(target, "CHANGED")])
            text = " ".join(document[0].get_text(sort=True).split())
        finally:
            document.close()

        for expected in ("ABOVE", "CHANGED", "BELOW", "LEFT", "RIGHT"):
            self.assertIn(expected, text)
        self.assertNotIn("TARGET", text)

    def test_edits_mixed_case_glyphs_without_rejecting_safe_text(self):
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        target = next(block for block in blocks if block["text"] == "MiXeD")

        document = pymupdf.open(self.source_path)
        try:
            main.apply_edits(document, [self.edit_for(target, "Changed")])
            text = " ".join(document[0].get_text(sort=True).split())
        finally:
            document.close()

        self.assertIn("Changed", text)
        self.assertNotIn("MiXeD", text)
        self.assertIn("TARGET", text)

    def test_batches_multiple_edits_without_losing_neighbors(self):
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        target = next(block for block in blocks if block["text"] == "TARGET")
        above = next(block for block in blocks if block["text"] == "ABOVE")
        edits = [self.edit_for(target, "CHANGED"), self.edit_for(above, "TOP")]

        document = pymupdf.open(self.source_path)
        try:
            main.apply_edits(document, edits)
            text = " ".join(document[0].get_text(sort=True).split())
        finally:
            document.close()

        for expected in ("TOP", "CHANGED", "BELOW", "LEFT", "RIGHT"):
            self.assertIn(expected, text)
        self.assertNotIn("TARGET", text)
        self.assertNotIn("ABOVE", text)

    def test_rejects_text_that_cannot_be_separated_safely(self):
        document = pymupdf.open(self.source_path)
        document[0].insert_text((45, 80), "NEAR", fontname="tiro", fontsize=12)
        overlapping_path = os.path.join(self.temp_dir.name, "overlapping.pdf")
        document.save(overlapping_path)
        document.close()
        main.documents[self.doc_id] = overlapping_path
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        target = next(block for block in blocks if block["text"] == "TARGET")

        document = pymupdf.open(overlapping_path)
        try:
            with self.assertRaises(main.HTTPException) as error:
                main.apply_edits(document, [self.edit_for(target, "CHANGED")])
            self.assertIn("nothing was removed", error.exception.detail)
            text = document[0].get_text()
        finally:
            document.close()

        self.assertIn("TARGET", text)
        self.assertNotIn("CHANGED", text)

    def test_applies_explicit_word_spacing(self):
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        block = next(block for block in blocks if block["text"] == "ONE TWO")
        edit = self.edit_for(block, "A B")
        edit["wordSpacing"] = 10

        document = pymupdf.open(self.source_path)
        try:
            main.apply_edits(document, [edit])
            words = document[0].get_text("words", sort=True)
            replacement_words = [word for word in words if word[4] in {"A", "B"}]
        finally:
            document.close()

        self.assertEqual([word[4] for word in replacement_words], ["A", "B"])
        self.assertAlmostEqual(replacement_words[1][0] - replacement_words[0][2], 10, delta=0.1)

    def test_rejects_word_spacing_that_cannot_fit(self):
        blocks = main.get_blocks(self.doc_id, 0)["blocks"]
        block = next(block for block in blocks if block["text"] == "ONE TWO")
        edit = self.edit_for(block, "A B")
        edit["wordSpacing"] = 100

        document = pymupdf.open(self.source_path)
        try:
            with self.assertRaises(main.HTTPException) as error:
                main.apply_edits(document, [edit])
            self.assertIn("Word spacing is too wide", error.exception.detail)
            text = document[0].get_text()
        finally:
            document.close()

        self.assertIn("ONE TWO", text)
        self.assertNotIn("A B", text)

    def test_adds_multiline_text_in_a_blank_page_area(self):
        document = pymupdf.open(self.source_path)
        try:
            main.apply_edits(document, [{
                "mode": "add",
                "page": 0,
                "bbox": [240, 24, 390, 72],
                "text": "Added text\nSecond line",
                "size": 12,
                "color": "#000000",
                "font": "original",
                "align": "left",
            }])
            text = " ".join(document[0].get_text(sort=True).split())
        finally:
            document.close()

        self.assertIn("Added text Second line", text)
        self.assertIn("TARGET", text)

    def test_rejects_added_text_area_outside_page(self):
        document = pymupdf.open(self.source_path)
        try:
            with self.assertRaises(main.HTTPException) as error:
                main.apply_edits(document, [{
                    "mode": "add",
                    "page": 0,
                    "bbox": [390, 160, 410, 190],
                    "text": "Out of bounds",
                    "size": 12,
                    "color": "#000000",
                    "font": "original",
                    "align": "left",
                }])
            self.assertIn("inside the page", error.exception.detail)
            text = document[0].get_text()
        finally:
            document.close()

        self.assertNotIn("Out of bounds", text)

    def test_exports_added_text_to_a_new_pdf(self):
        response = main.export_pdf(
            self.doc_id,
            main.EditRequest(edits=[{
                "mode": "add",
                "page": 0,
                "bbox": [240, 24, 390, 72],
                "text": "Exported note",
                "size": 12,
                "color": "#000000",
                "font": "original",
                "align": "left",
            }]),
        )
        exported = pymupdf.open(stream=response.body, filetype="pdf")
        try:
            text = exported[0].get_text()
        finally:
            exported.close()

        self.assertIn("Exported note", text)
        self.assertIn("TARGET", text)
