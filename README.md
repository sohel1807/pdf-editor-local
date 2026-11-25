# Simple PDF Editor (local prototype)

A small browser-based PDF text editor for testing on a company laptop.

## What it does
- Opens a PDF from your computer.
- Renders pages and extracts selectable text segments.
- Lets you click a text segment on the rendered page (or select it from the list) and replace its text.
- Lets you search fonts installed on this computer and choose available regular, bold, italic, and combined faces, along with size, color, alignment, and adjustable word spacing.
- Shows a live preview rendered by the PDF engine while you edit, so embedded fonts and spacing match the exported PDF; download is still required to save the edited PDF.
- Supports Undo and Redo for applied changes.
- Exports a new PDF without changing the original file.

## Important limitations
This is a prototype, not a full Acrobat/Word replacement.
- It edits extracted text segments, not arbitrary page objects, semantic table cells, or complete flowing paragraphs. Table text may need to be edited one segment at a time.
- It reuses the original PDF font when available, including a matching installed face when the PDF font is not embedded. Installed font files and collection faces are available as replacements. Word spacing is preserved by default and can be overridden in points; spacing that cannot fit the selected segment is rejected rather than allowed to overlap neighboring text.
- Only fonts embedded in the PDF or installed on this computer can be used. No library can supply every proprietary font; a font absent from both sources cannot be reproduced exactly.
- Some PDFs position words separately without storing spaces; extracted text adds spaces where their positions indicate a word gap. Exact original kerning and tracking may still differ.
- Replacement text is laid out as separate words to retain the selected text's measured spacing. Exact glyph-by-glyph kerning and tracking can still differ for unusually encoded PDFs.
- Text removal is constrained to the selected source text and batched before replacements are drawn, so later edits do not erase earlier replacements. If nearby glyphs overlap so they cannot be separated safely, the edit is rejected instead of removing neighboring text.
- Text is removed without painting over the area, preserving underlying page graphics and backgrounds. Complex overlapping content may still need manual checking.
- It is not secure redaction and must not be used to conceal confidential information.
- Scanned/image-only PDFs need OCR and are not editable in this version.
- Password-protected PDFs are not supported.
- Font availability depends on the local operating system and embedding rights. Fonts marked as restricted for editable embedding are not offered.

## Run on Windows

1. Install Python 3.10 or newer.
2. Extract this folder.
3. Open PowerShell in this folder.
4. Create and activate a virtual environment:

```powershell
py -m venv .venv
.venv\\Scripts\\Activate.ps1
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

5. Install packages:

```powershell
pip install -r requirements.txt
```

6. Start the app:

```powershell
uvicorn main:app --host 127.0.0.1 --port 8000
```

7. Open http://127.0.0.1:8000 in Chrome or Edge.

## Run on a company network
For testing on a trusted internal network only, you can use:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000
```

Do not expose this prototype to the public internet. Before multi-user/company deployment, add authentication, per-user temporary files, cleanup, upload limits, CSRF protections, and authorization. The current in-memory document map is intended for a single-user local test.

## Technology
- FastAPI + PyMuPDF backend for local PDF processing.
- FontTools for listing installed font faces and extracting individual faces from font collections.
- Plain HTML/CSS/JavaScript frontend.
- PyMuPDF documentation: https://pymupdf.readthedocs.io/
