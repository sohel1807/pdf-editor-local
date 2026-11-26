# Simple PDF Editor (local prototype)

A small browser-based PDF text editor for testing on a company laptop.

## What it does
- Opens a PDF from your computer.
- Renders pages and extracts selectable text segments.
- Lets you click a text segment on the rendered page (or select it from the list) and replace its text.
- Lets you add single- or multi-line text to a blank area: choose **Fill blank area**, drag to select the exact writing area, then enter the text and apply it. The blue added-text area remains selectable so you can edit it again.
- Lets you search fonts installed on this computer and choose available regular, bold, italic, and combined faces, along with size, color, alignment, and adjustable word spacing.
- Shows a live preview rendered by the PDF engine while you edit, so embedded fonts and spacing match the exported PDF; download is still required to save the edited PDF.
- Supports Undo and Redo for applied changes.
- Exports a new PDF without changing the original file.

## Important limitations
This is a prototype, not a full Acrobat/Word replacement.
- It edits extracted text segments, not arbitrary page objects, semantic table cells, or complete flowing paragraphs. Table text may need to be edited one segment at a time.
- Added text is placed inside a fixed area on the page; it does not reflow existing content or automatically avoid nearby objects. Choose an actually blank area and review the preview before exporting.
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

Do not expose this local server directly to the public internet. For a Modal deployment, use the shared password and temporary-session cleanup described below. The Modal configuration intentionally allows only one container because active document sessions are held in that container's temporary filesystem.

## Deploy the frontend to Vercel and the API to Modal

The browser keeps the selected original PDF in memory. It is uploaded to the Modal API for editing, stored only in the container's temporary filesystem while the editing session is active, and never sent to Vercel. API responses use `Cache-Control: no-store`; edited PDF output is generated in memory.

The temporary server copy is deleted when **Close PDF** is clicked or another PDF is opened. Closing the browser also attempts cleanup; that request is best-effort. The API sweeps sessions after 30 minutes of inactivity when it next receives a request, and Modal scales the container down after five idle minutes, which also discards its temporary files. Reopen the PDF if the Modal container restarts or scales down during editing. Do not attach a Modal Volume or other persistent storage to this app.

This deployment is intended for a small trusted group sharing one password, not open public access. The browser asks for the password and keeps it only in memory. The API is limited to one Modal container to keep temporary document sessions available, so requests may queue during concurrent use. CORS is restricted to the configured Vercel origin; the password is still required.

1. Deploy this repository to Vercel. Keep the project root at the repository root; `vercel.json` publishes the `static/` directory. Note the production Vercel URL.
2. Install and authenticate the Modal CLI:

   ```powershell
   pip install modal
   modal setup
   ```

3. Generate a strong shared password and create the Modal secret. Replace the example origin with the Vercel URL from step 1:

   ```powershell
   $password = python -c "import secrets; print(secrets.token_urlsafe(24))"
   $password
   modal secret create simple-pdf-editor-secrets "PDF_EDITOR_SHARED_PASSWORD=$password" "PDF_EDITOR_ALLOWED_ORIGINS=https://your-project.vercel.app"
   ```

   Share the password securely with the trusted group; the app prompts for it when each person opens a PDF. Do not commit the password.
4. Deploy the API:

   ```powershell
   modal deploy modal_app.py
   ```

5. Set `window.PDF_API_BASE_URL` in `static/config.js` to the Modal URL printed by the deploy command, then push the change so Vercel redeploys the frontend:

   ```javascript
   window.PDF_API_BASE_URL = "https://your-modal-endpoint.modal.run";
   ```

6. Open the Vercel site, select a PDF, and enter the shared password when prompted. Click **Close PDF** when finished to remove the temporary server copy immediately.

The Modal endpoint is not deployed from this environment; you must run the commands using your own Modal and Vercel accounts.

## Technology
- FastAPI + PyMuPDF backend for local PDF processing.
- FontTools for listing installed font faces and extracting individual faces from font collections.
- Plain HTML/CSS/JavaScript frontend.
- PyMuPDF documentation: https://pymupdf.readthedocs.io/
