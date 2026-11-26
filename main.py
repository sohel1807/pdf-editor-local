from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fontTools.ttLib import TTCollection, TTFont, TTLibError
import fitz
import atexit
from functools import lru_cache
import hashlib, io, logging, math, os, platform, re, secrets, tempfile, threading, time, uuid

app = FastAPI(title="Simple PDF Editor")
WORK = os.path.join(tempfile.gettempdir(), "simple-pdf-editor")
os.makedirs(WORK, exist_ok=True)
logger = logging.getLogger(__name__)
MAX_PDF_BYTES = 50 * 1024 * 1024

FONT_ALIASES = {
    "helvetica": "helv", "helveticabold": "hebo", "helveticaoblique": "heit",
    "helveticaitalic": "heit", "helveticaboldoblique": "hebi", "helveticabolditalic": "hebi",
    "courier": "cour", "courierbold": "cobo", "courieroblique": "coit",
    "courieritalic": "coit", "courierboldoblique": "cobi", "courierbolditalic": "cobi",
    "times": "tiro", "timesroman": "tiro", "timesbold": "tibo",
    "timesitalic": "tiit", "timesbolditalic": "tibi", "symbol": "symb",
    "zapfdingbats": "zadb",
}
FONT_FILE_EXTENSIONS = {".otc", ".otf", ".ttc", ".ttf"}


def font_roots():
    if platform.system() == "Windows":
        windows_root = os.environ.get("WINDIR", r"C:\Windows")
        local_root = os.environ.get("LOCALAPPDATA", "")
        roots = [os.path.join(windows_root, "Fonts")]
        if local_root:
            roots.append(os.path.join(local_root, "Microsoft", "Windows", "Fonts"))
        return roots
    if platform.system() == "Darwin":
        return [
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]
    return [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.local/share/fonts"),
        os.path.expanduser("~/.fonts"),
    ]


def font_name_record(font, name_id):
    name_table = font["name"]
    return (name_table.getDebugName(name_id) or "").strip()


def font_face_details(font):
    family = font_name_record(font, 16) or font_name_record(font, 1)
    style = font_name_record(font, 17) or font_name_record(font, 2) or "Regular"
    os2 = font.get("OS/2")
    head = font.get("head")
    post = font.get("post")
    fs_type = os2.fsType if os2 else 0
    bold = bool(
        (os2 and os2.fsSelection & (1 << 5))
        or (head and head.macStyle & 1)
        or re.search(r"\b(bold|demi|heavy|black)\b", style, re.IGNORECASE)
    )
    italic = bool(
        (os2 and os2.fsSelection & 1)
        or (head and head.macStyle & 2)
        or (post and post.italicAngle)
        or re.search(r"\b(italic|oblique)\b", style, re.IGNORECASE)
    )
    embedding_allowed = not (fs_type & (0x0002 | 0x0004 | 0x0200))
    return family, style, bold, italic, embedding_allowed


@lru_cache(maxsize=1)
def system_font_catalog():
    candidates = {}
    seen_paths = set()
    for root in font_roots():
        if not root or not os.path.isdir(root):
            continue
        for folder, _, filenames in os.walk(root):
            for filename in filenames:
                path = os.path.join(folder, filename)
                extension = os.path.splitext(filename)[1].lower()
                if extension not in FONT_FILE_EXTENSIONS:
                    continue
                canonical_path = os.path.normcase(os.path.realpath(path))
                if canonical_path in seen_paths:
                    continue
                seen_paths.add(canonical_path)
                collection = None
                try:
                    if extension in {".otc", ".ttc"}:
                        collection = TTCollection(path, lazy=False)
                        faces = collection.fonts
                    else:
                        faces = [TTFont(path, lazy=True)]
                    for face_index, face in enumerate(faces):
                        try:
                            family, style, bold, italic, embedding_allowed = font_face_details(face)
                            if not family or not embedding_allowed:
                                continue
                            postscript = font_name_record(face, 6)
                            font_id = hashlib.sha256(
                                f"{canonical_path}#{face_index}".encode("utf-8")
                            ).hexdigest()[:20]
                            entry = {
                                "id": f"system-{font_id}",
                                "family": family,
                                "style": style,
                                "bold": bold,
                                "italic": italic,
                                "postscript": postscript,
                                "path": path,
                                "index": face_index,
                                "glyph_count": face["maxp"].numGlyphs,
                            }
                            variant = (family.casefold(), style.casefold())
                            previous = candidates.get(variant)
                            if previous is None or entry["glyph_count"] > previous["glyph_count"]:
                                candidates[variant] = entry
                        finally:
                            if collection is None:
                                face.close()
                except (OSError, TTLibError, KeyError, ValueError, IndexError) as error:
                    logger.warning("Skipping unreadable font file %s: %s", path, error)
                finally:
                    if collection is not None:
                        collection.close()
    return sorted(candidates.values(), key=lambda entry: (entry["family"].casefold(), entry["style"].casefold()))


def font_catalog_by_id():
    return {entry["id"]: entry for entry in system_font_catalog()}


@lru_cache(maxsize=24)
def system_font_buffer(font_id):
    entry = font_catalog_by_id().get(font_id)
    if entry is None:
        raise KeyError("The selected system font is no longer available.")
    collection = None
    font = None
    try:
        if os.path.splitext(entry["path"])[1].lower() in {".otc", ".ttc"}:
            collection = TTCollection(entry["path"], lazy=False)
            font = collection.fonts[entry["index"]]
        else:
            font = TTFont(entry["path"], lazy=False)
        font.flavor = None
        output = io.BytesIO()
        font.save(output)
        return output.getvalue()
    finally:
        if collection is not None:
            collection.close()
        elif font is not None:
            font.close()


def matching_system_font(font_name, font_flags=0):
    source_key = font_key(font_name)
    if not source_key:
        return None
    source_bold = bool(font_flags & 16) or "bold" in source_key
    source_italic = bool(font_flags & 2) or any(term in source_key for term in ("italic", "oblique"))
    if "black" in source_key:
        wanted_weight = "black"
    elif "semibold" in source_key or "demi" in source_key:
        wanted_weight = "semibold"
    else:
        wanted_weight = "bold" if source_bold else "regular"
    wanted_style = wanted_weight + ("italic" if source_italic else "")
    candidates = [
        entry for entry in system_font_catalog()
        if source_key in {
            font_key(entry["family"]),
            font_key(entry["postscript"]),
        }
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda entry: (
            font_key(entry["style"]) != font_key(wanted_style),
            entry["bold"] != source_bold,
            entry["italic"] != source_italic,
            "black" in font_key(entry["style"]) and wanted_weight != "black",
        ),
    )


def font_key(name):
    name = re.sub(r"^[A-Z]{6}\+", "", str(name)).lower()
    key = re.sub(r"[^a-z0-9]", "", name)
    changed = True
    while changed:
        changed = False
        for suffix in ("regular", "roman", "psmt", "mt"):
            if key.endswith(suffix) and len(key) > len(suffix):
                key = key[:-len(suffix)]
                changed = True
                break
    return key


def resolve_system_font(font_id, font_cache):
    cache_key = ("system", font_id)
    if cache_key not in font_cache:
        try:
            font_buffer = system_font_buffer(font_id)
            font = fitz.Font(fontbuffer=font_buffer)
        except (KeyError, RuntimeError, ValueError) as error:
            raise HTTPException(400, f"Could not load the selected system font: {error}") from error
        font_name = "sys_" + font_id.removeprefix("system-")
        font_cache[cache_key] = (font_name, font, font_buffer)
    return font_cache[cache_key]


def resolve_edit_font(doc, page, font_choice, original_font, font_cache, original_flags=0):
    if font_choice != "original":
        if font_choice in font_catalog_by_id():
            return resolve_system_font(font_choice, font_cache)
        if font_choice not in {
            "helv", "hebo", "heit", "hebi", "cour", "cobo", "coit",
            "cobi", "tiro", "tibo", "tiit", "tibi", "symb", "zadb",
        }:
            raise HTTPException(400, "Choose a supported font.")
        return font_choice, fitz.Font(fontname=font_choice), None

    source_key = font_key(original_font)
    for font_info in page.get_fonts(full=True):
        xref, ext, basefont = font_info[0], font_info[1], font_info[3]
        if font_key(basefont) != source_key:
            continue
        alias = FONT_ALIASES.get(source_key) or FONT_ALIASES.get(font_key(basefont))
        if ext == "n/a" and alias:
            return alias, fitz.Font(fontname=alias), None

        cache_key = (page.number, xref)
        if cache_key in font_cache:
            return font_cache[cache_key]
        try:
            _, _, _, font_buffer = doc.extract_font(xref)
            if not font_buffer:
                break
            font = fitz.Font(fontbuffer=font_buffer)
            alias = f"source_{page.number}_{xref}"
        except (RuntimeError, ValueError) as error:
            raise HTTPException(400, f"Could not reuse the original PDF font: {error}") from error
        font_cache[cache_key] = (alias, font, font_buffer)
        return alias, font, font_buffer

    if source_key in FONT_ALIASES:
        alias = FONT_ALIASES[source_key]
        return alias, fitz.Font(fontname=alias), None
    system_font = matching_system_font(original_font, original_flags)
    if system_font:
        return resolve_system_font(system_font["id"], font_cache)
    raise HTTPException(400, f"Could not find the original PDF font '{original_font}'. Choose another font.")


documents = {}
document_last_access = {}
documents_lock = threading.Lock()

class EditRequest(BaseModel):
    edits: list[dict]


def get_document_path(doc_id):
    path = documents.get(doc_id)
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "Document not found.")
    return path


@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.get("/api/fonts")
def get_fonts():
    return [
        {
            "id": entry["id"],
            "family": entry["family"],
            "style": entry["style"],
            "bold": entry["bold"],
            "italic": entry["italic"],
        }
        for entry in system_font_catalog()
    ]

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        await file.close()
        raise HTTPException(400, "Please upload a PDF file.")
    try:
        data = await file.read(MAX_PDF_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "Choose a PDF smaller than 50 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "The uploaded file is not a valid PDF.")
    doc_id = str(uuid.uuid4())
    path = os.path.join(WORK, doc_id + ".pdf")
    with open(path, "wb") as f:
        f.write(data)
    try:
        doc = fitz.open(path)
    except Exception as error:
        if os.path.exists(path):
            os.remove(path)
        raise HTTPException(400, f"Could not open PDF: {error}") from error
    if doc.needs_pass:
        doc.close()
        os.remove(path)
        raise HTTPException(400, "Password-protected PDFs are not supported in this prototype.")
    page_count = len(doc)
    doc.close()
    documents[doc_id] = path
    return {"id": doc_id, "pages": page_count, "filename": file.filename}

@app.get("/api/document/{doc_id}/page/{page_num}")
def get_page(doc_id: str, page_num: int):
    path = get_document_path(doc_id)
    doc = fitz.open(path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise HTTPException(404, "Page not found.")
        pix = doc[page_num].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return Response(content=pix.tobytes("png"), media_type="image/png")
    finally:
        doc.close()

@app.get("/api/document/{doc_id}/blocks/{page_num}")
def get_blocks(doc_id: str, page_num: int):
    path = get_document_path(doc_id)
    doc = fitz.open(path)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise HTTPException(404, "Page not found.")
        page = doc[page_num]
        result = get_page_blocks(page)
    finally:
        doc.close()
    return result


def get_page_blocks(page):
    words = page.get_text("words", sort=True)
    spans = []
    raw = page.get_text("dict")
    for block_index, block in enumerate(raw["blocks"]):
        if "lines" not in block: continue
        for line_index, line in enumerate(block["lines"]):
            for span_index, span in enumerate(line["spans"]):
                if span["text"].strip():
                    span["sourceId"] = [block_index, line_index, span_index]
                    spans.append(span)
    spans.sort(key=lambda span: (span.get("origin", (0, 0))[1], span["bbox"][0]))

    groups = []
    for span in spans:
        if groups:
            previous = groups[-1][-1]
            style = (span.get("font"), span.get("size"), span.get("color"), span.get("flags"))
            previous_style = (previous.get("font"), previous.get("size"), previous.get("color"), previous.get("flags"))
            gap = span["bbox"][0] - previous["bbox"][2]
            baseline_gap = abs(span.get("origin", (0, 0))[1] - previous.get("origin", (0, 0))[1])
            size = span.get("size", 11)
            if (
                style == previous_style
                and baseline_gap <= 0.5
                and gap >= -max(1, size * 0.25)
                and gap <= max(2, size * 1.5)
            ):
                groups[-1].append(span)
                continue
        groups.append([span])

    result = []
    for group_index, group in enumerate(groups):
        first = group[0]
        text_parts = []
        for span_index, span in enumerate(group):
            if span_index:
                previous = group[span_index - 1]
                gap = span["bbox"][0] - previous["bbox"][2]
                threshold = max(0.5, first.get("size", 11) * 0.2)
                if (
                    gap > threshold
                    and not previous["text"][-1].isspace()
                    and not span["text"][0].isspace()
                ):
                    text_parts.append(" ")
            text_parts.append(span["text"])
        text = "".join(text_parts)
        x0 = min(span["bbox"][0] for span in group)
        y0 = min(span["bbox"][1] for span in group)
        x1 = max(span["bbox"][2] for span in group)
        y1 = max(span["bbox"][3] for span in group)
        color = first.get("color", 0)
        group_center_y = (y0 + y1) / 2
        group_words = [
            word for word in words
            if word[0] >= x0 - 0.1
            and word[2] <= x1 + 0.1
            and abs((word[1] + word[3]) / 2 - group_center_y) <= max(2, first.get("size", 11) * 0.4)
        ]
        word_gap = None
        if len(group_words) > 1:
            gaps = sorted(
                max(0, group_words[index][0] - group_words[index - 1][2])
                for index in range(1, len(group_words))
            )
            word_gap = gaps[len(gaps) // 2]
        result.append({
            "key": f"segment-{group_index}",
            "text": text,
            "bbox": [x0, y0, x1, y1],
            "origin": first.get("origin", [x0, y1]),
            "font": first.get("font", "helv"),
            "fontFlags": first.get("flags", 0),
            "size": first.get("size", 11),
            "color": "#{:06x}".format(color & 0xFFFFFF),
            "wordGap": word_gap,
            "sourceIds": [span["sourceId"] for span in group]
        })
    page_size = {"width": page.rect.width, "height": page.rect.height}
    return {"page": page_size, "blocks": result}

def page_text_characters(page):
    characters = []
    for block_index, block in enumerate(page.get_text("rawdict")["blocks"]):
        for line_index, line in enumerate(block.get("lines", [])):
            for span_index, span in enumerate(line["spans"]):
                for char in span["chars"]:
                    characters.append({
                        "rect": fitz.Rect(char["bbox"]),
                        "baseline": char.get("origin", span.get("origin", [0, 0]))[1],
                        "font": span.get("font", ""),
                        "size": span.get("size", 11),
                        "text": char.get("c", ""),
                        "source_id": (block_index, line_index, span_index),
                    })
    return characters


def target_character_indices(characters, edit, rect):
    source_ids = edit.get("sourceIds")
    if source_ids:
        selected_sources = {tuple(source_id) for source_id in source_ids}
        return {
            index for index, char in enumerate(characters)
            if char["source_id"] in selected_sources
        }
    original_font = font_key(edit.get("originalFont", ""))
    origin = edit.get("origin", [rect.x0, rect.y1])
    baseline = float(origin[1])
    size = float(edit.get("originalSize", edit.get("size", 11)))
    size_tolerance = max(0.1, size * 0.01)
    line_indices = [
        index for index, char in enumerate(characters)
        if abs(char["baseline"] - baseline) <= 0.5
        and (not original_font or font_key(char["font"]) == original_font)
        and abs(char["size"] - size) <= size_tolerance
    ]
    line_indices.sort(key=lambda index: characters[index]["rect"].x0)
    original_text = "".join(str(edit.get("originalText", "")).split())
    if original_text:
        text_indices = [
            index for index in line_indices
            if characters[index]["text"] and not characters[index]["text"].isspace()
        ]
        line_text = "".join(characters[index]["text"] for index in text_indices)
        search_from = 0
        matches = []
        while True:
            start = line_text.find(original_text, search_from)
            if start < 0:
                break
            end = start + len(original_text)
            candidate = text_indices[start:end]
            if candidate and rect.x0 - 0.1 <= characters[candidate[0]]["rect"].x0 <= rect.x1:
                matches.append(candidate)
            search_from = start + 1
        if matches:
            matches.sort(key=lambda match: abs(characters[match[0]]["rect"].x0 - rect.x0))
            if (
                len(matches) > 1
                and abs(characters[matches[0][0]]["rect"].x0 - rect.x0)
                == abs(characters[matches[1][0]]["rect"].x0 - rect.x0)
            ):
                raise HTTPException(
                    400,
                    "The selected text overlaps duplicate PDF text; no content was removed.",
                )
            first_x = characters[matches[0][0]]["rect"].x0
            last_x = characters[matches[0][-1]]["rect"].x1
            return {
                index for index in line_indices
                if first_x - 0.1 <= characters[index]["rect"].x0
                and characters[index]["rect"].x1 <= last_x + 0.1
            }

    return {
        index for index in line_indices
        if characters[index]["rect"].x0 >= rect.x0 - 0.1
        and characters[index]["rect"].x1 <= rect.x1 + 0.1
    }


def safe_redaction_rects(characters, target_indices, selected_indices):
    epsilon = 0.05
    covers = []

    for target_index in sorted(target_indices):
        target = characters[target_index]
        target_rect = target["rect"]
        if target["text"].isspace() or target_rect.is_empty:
            continue

        intervals = [(target_rect.y0, target_rect.y1)]
        for index, char in enumerate(characters):
            if index in selected_indices or char["text"].isspace():
                continue
            rect = char["rect"]
            if rect.x1 <= target_rect.x0 + epsilon or rect.x0 >= target_rect.x1 - epsilon:
                continue
            overlap_y0 = max(target_rect.y0, rect.y0)
            overlap_y1 = min(target_rect.y1, rect.y1)
            if overlap_y1 <= overlap_y0:
                continue
            remaining = []
            for interval_y0, interval_y1 in intervals:
                if overlap_y1 <= interval_y0 or overlap_y0 >= interval_y1:
                    remaining.append((interval_y0, interval_y1))
                    continue
                if interval_y0 < overlap_y0:
                    remaining.append((interval_y0, overlap_y0))
                if overlap_y1 < interval_y1:
                    remaining.append((overlap_y1, interval_y1))
            intervals = remaining
            if not intervals:
                break

        if not intervals:
            raise HTTPException(
                400,
                "Nearby text overlaps the selected text; no safe edit area was found, so nothing was removed.",
            )
        redaction_y0, redaction_y1 = max(intervals, key=lambda interval: interval[1] - interval[0])
        cover = fitz.Rect(
            target_rect.x0 + epsilon,
            redaction_y0 + epsilon,
            target_rect.x1 - epsilon,
            redaction_y1 - epsilon,
        )
        if cover.is_empty or cover.height < 1.0:
            raise HTTPException(
                400,
                "The selected text is too close to nearby content for a safe edit; nothing was removed.",
            )
        covers.append(cover)

    if not covers:
        raise HTTPException(400, "The selected text has no removable characters; nothing was removed.")
    return covers


def apply_edits(doc, edits):
    font_cache = {}
    prepared = []
    characters_by_page = {}
    target_indices_by_page = {}
    pages_with_replacements = set()

    for edit in edits:
        page_num = int(edit["page"])
        if page_num < 0 or page_num >= len(doc):
            continue
        page = doc[page_num]
        rect = fitz.Rect(edit["bbox"])
        if not rect.is_valid or rect.is_empty:
            raise HTTPException(400, "The selected text bounds are invalid.")
        is_addition = edit.get("mode") == "add"
        if is_addition:
            if not page.rect.contains(rect):
                raise HTTPException(400, "The writing area must stay inside the page.")
            if not str(edit.get("text", "")).strip():
                raise HTTPException(400, "Enter text before adding it to the page.")
            target_indices = set()
            raw_text = str(edit.get("text", "")).replace("\r\n", "\n").replace("\r", "\n")
            lines = raw_text.split("\n")
        else:
            if page_num not in characters_by_page:
                characters_by_page[page_num] = page_text_characters(page)
            target_indices = target_character_indices(characters_by_page[page_num], edit, rect)
            if not target_indices:
                raise HTTPException(
                    400,
                    "The selected text could not be isolated safely; nothing was removed.",
                )
            target_indices_by_page.setdefault(page_num, set()).update(target_indices)
            raw_text = " ".join(str(edit.get("text", "")).splitlines())
            lines = [raw_text]

        line_tokens = [line.split() for line in lines]
        text_for_font = "\n".join(lines)
        try:
            size = float(edit.get("size", 11))
            original_size = float(edit.get("originalSize", size))
        except (TypeError, ValueError) as error:
            raise HTTPException(400, "Font size must be a number.") from error
        if not math.isfinite(size) or not math.isfinite(original_size):
            raise HTTPException(400, "Font size must be a finite number.")
        size = max(4, min(size, 96))
        original_size = max(4, min(original_size, 96))
        color_hex = str(edit.get("color", "#000000")).lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color_hex):
            raise HTTPException(400, "Text color must be a six-digit hex color.")
        color = tuple(int(color_hex[i:i+2], 16)/255 for i in (0,2,4))
        align_name = edit.get("align", "left")
        if align_name not in {"left", "center", "right"}:
            raise HTTPException(400, "Choose left, center, or right alignment.")
        font_flags = int(edit.get("fontFlags", 0))
        font_choice = edit.get("font", "original")
        if is_addition and font_choice == "original":
            font_choice = "helv"
        font_name, font, font_buffer = resolve_edit_font(
            doc, page, font_choice,
            edit.get("originalFont", ""), font_cache, font_flags
        )
        unsupported = [
            char for char in text_for_font
            if not char.isspace() and not font.has_glyph(ord(char))
        ]
        if unsupported and font_choice == "original":
            system_font = matching_system_font(edit.get("originalFont", ""), font_flags)
            if system_font:
                font_name, font, font_buffer = resolve_system_font(system_font["id"], font_cache)
                unsupported = [
                    char for char in text_for_font
                    if not char.isspace() and not font.has_glyph(ord(char))
                ]
        if unsupported:
            raise HTTPException(
                400,
                f"No available font variant supports '{unsupported[0]}'. Choose an installed font with the required characters.",
            )
        try:
            source_word_gap = None if is_addition else edit.get("wordGap")
            source_word_gap = None if source_word_gap is None else float(source_word_gap)
        except (TypeError, ValueError) as error:
            raise HTTPException(400, "Original word spacing is invalid.") from error
        if source_word_gap is not None and (not math.isfinite(source_word_gap) or source_word_gap < 0):
            raise HTTPException(400, "Original word spacing is invalid.")
        try:
            word_spacing = edit.get("wordSpacing")
            word_spacing = None if word_spacing is None else float(word_spacing)
        except (TypeError, ValueError) as error:
            raise HTTPException(400, "Word spacing must be a finite, nonnegative number.") from error
        if word_spacing is not None and (not math.isfinite(word_spacing) or word_spacing < 0):
            raise HTTPException(400, "Word spacing must be a finite, nonnegative number.")

        def measure_lines(font_size):
            gap = (
                word_spacing
                if word_spacing is not None
                else (
                    source_word_gap * font_size / original_size
                    if source_word_gap is not None
                    else font.text_length(" ", fontsize=font_size)
                )
            )
            widths = [
                sum(font.text_length(token, fontsize=font_size) for token in tokens)
                + gap * max(0, len(tokens) - 1)
                for tokens in line_tokens
            ]
            return widths, gap

        line_height_factor = 1.2
        line_widths, word_gap = measure_lines(size)
        widest_line = max(line_widths, default=0)
        total_height = size * (1 + max(0, len(line_tokens) - 1) * line_height_factor)
        width_scale = rect.width / widest_line if widest_line > rect.width and widest_line else 1
        height_scale = (
            rect.height / total_height
            if is_addition and total_height > rect.height
            else 1
        )
        scale = min(width_scale, height_scale)
        if scale < 1:
            if is_addition or word_spacing is None:
                size *= scale
            else:
                gap_width = word_gap * max(
                    (len(tokens) - 1 for tokens in line_tokens),
                    default=0,
                )
                available_text_width = rect.width - gap_width
                unspaced_width = max(
                    (
                        sum(font.text_length(token, fontsize=size) for token in tokens)
                        for tokens in line_tokens
                    ),
                    default=0,
                )
                if available_text_width <= 0 or unspaced_width <= 0:
                    raise HTTPException(400, "Word spacing is too wide for the selected text area.")
                size *= min(
                    available_text_width / unspaced_width,
                    height_scale,
                )
            if size < 4:
                message = (
                    "The text is too long to fit in the selected writing area."
                    if is_addition
                    else "The replacement is too long to fit in the original text area."
                )
                raise HTTPException(400, message)
            line_widths, word_gap = measure_lines(size)
            if word_spacing is not None and max(line_widths, default=0) > rect.width + 0.01:
                raise HTTPException(400, "Word spacing is too wide for the selected text area.")

        origin = edit.get("origin")
        if origin is not None:
            baseline_y = float(origin[1])
        elif is_addition:
            baseline_y = rect.y0 + size
        else:
            baseline_y = rect.y1
        if is_addition:
            baseline_offset = max(0, len(line_tokens) - 1) * size * line_height_factor
            if baseline_y < rect.y0 + size or baseline_y + baseline_offset > rect.y1:
                baseline_y = rect.y0 + size
            last_baseline = baseline_y + baseline_offset
            if last_baseline > rect.y1 + 0.01:
                raise HTTPException(400, "The text is too long to fit in the selected writing area.")
        if align_name == "center":
            line_starts = [
                rect.x0 + (rect.width - width) / 2 for width in line_widths
            ]
        elif align_name == "right":
            line_starts = [rect.x1 - width for width in line_widths]
        else:
            line_starts = [rect.x0] * len(line_widths)

        prepared.append({
            "page": page,
            "page_num": page_num,
            "rect": rect,
            "target_indices": target_indices,
            "is_addition": is_addition,
            "line_tokens": line_tokens,
            "line_widths": line_widths,
            "font_name": font_name,
            "font": font,
            "font_buffer": font_buffer,
            "font_size": size,
            "color": color,
            "baseline_y": baseline_y,
            "line_starts": line_starts,
            "line_height": size * line_height_factor,
            "word_gap": word_gap,
        })
        if not is_addition:
            pages_with_replacements.add(page_num)

    for item in prepared:
        if item["is_addition"]:
            item["covers"] = []
            continue
        page_num = item["page_num"]
        item["covers"] = safe_redaction_rects(
            characters_by_page[page_num],
            item["target_indices"],
            target_indices_by_page[page_num],
        )

    for item in prepared:
        for cover in item["covers"]:
            item["page"].add_redact_annot(cover, fill=None)

    for page_num in pages_with_replacements:
        doc[page_num].apply_redactions(images=0, graphics=0, text=0)

    inserted_fonts = set()
    for item in prepared:
        if not any(item["line_tokens"]):
            continue
        page = item["page"]
        font_name = item["font_name"]
        font_buffer = item["font_buffer"]
        font_key_on_page = (item["page_num"], font_name)
        if font_buffer and font_key_on_page not in inserted_fonts:
            page.insert_font(fontname=font_name, fontbuffer=font_buffer)
            inserted_fonts.add(font_key_on_page)
        for line_index, tokens in enumerate(item["line_tokens"]):
            text_x = item["line_starts"][line_index]
            baseline_y = item["baseline_y"] + line_index * item["line_height"]
            for index, token in enumerate(tokens):
                page.insert_text(
                    (text_x, baseline_y),
                    token,
                    fontname=font_name,
                    fontsize=item["font_size"],
                    color=item["color"],
                )
                text_x += item["font"].text_length(token, fontsize=item["font_size"])
                if index < len(tokens) - 1:
                    text_x += item["word_gap"]

@app.post("/api/document/{doc_id}/preview/{page_num}")
def preview_page(doc_id: str, page_num: int, request: EditRequest):
    source = get_document_path(doc_id)
    doc = fitz.open(source)
    try:
        if page_num < 0 or page_num >= len(doc):
            raise HTTPException(404, "Page not found.")
        apply_edits(doc, request.edits)
        pixmap = doc[page_num].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        return Response(content=pixmap.tobytes("png"), media_type="image/png")
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(400, f"Preview failed: {error}") from error
    finally:
        doc.close()

@app.post("/api/document/{doc_id}/export")
def export_pdf(doc_id: str, request: EditRequest):
    source = get_document_path(doc_id)
    # Work on a fresh copy every time, so edits are not cumulatively applied.
    doc = fitz.open(source)
    try:
        apply_edits(doc, request.edits)
        output = doc.tobytes(garbage=4, deflate=True)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(400, f"Export failed: {error}") from error
    finally:
        doc.close()
    return Response(
        content=output,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="edited.pdf"'},
    )


if os.environ.get("PDF_EDITOR_SERVE_STATIC", "1") == "1":
    app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
