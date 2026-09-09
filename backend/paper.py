from collections.abc import Callable
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from threading import Lock
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
import pymupdf
import qrcode
import reportlab
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


PAGE_WIDTH = 612
PAGE_HEIGHT = 792
SCALE = 3
ROWS_PER_PAGE = 10
MODEL_ID = "microsoft/trocr-small-handwritten"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_OCR_CHARACTERS = 250
MAX_OCR_TOKENS = 64
OCR_LOCK = Lock()
ANCHORS = ((18, 18), (566, 18), (566, 746), (18, 746))
MARKER_SIZE = 28


@lru_cache(maxsize=1)
def _font() -> str:
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    pdfmetrics.registerFont(TTFont("PaperPatch", str(font_path)))
    return "PaperPatch"


def _shorten(text: str, width: float, size: float) -> str:
    if pdfmetrics.stringWidth(text, _font(), size) <= width:
        return text
    while text and pdfmetrics.stringWidth(f"{text}...", _font(), size) > width:
        text = text[:-1]
    return f"{text}..."


def _label_lines(text: str, width: float, size: float) -> list[str]:
    words = text.split()
    if not words:
        return []
    first = words.pop(0)
    while words and pdfmetrics.stringWidth(f"{first} {words[0]}", _font(), size) <= width:
        first = f"{first} {words.pop(0)}"
    lines = [_shorten(first, width, size)]
    if words:
        lines.append(_shorten(" ".join(words), width, size))
    return lines


def _text(pdf: canvas.Canvas, text: str, x: float, top: float, size: float = 9,
          color: str = "#263143", width: float | None = None) -> None:
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if width is not None:
        text = _shorten(text, width, size)
    pdf.setFont(_font(), size)
    pdf.setFillColor(HexColor(color))
    pdf.drawString(x, PAGE_HEIGHT - top, text)


def _draw_image(pdf: canvas.Canvas, image: Image.Image, x: float, top: float,
                width: float, height: float) -> None:
    pdf.drawImage(ImageReader(image), x, PAGE_HEIGHT - top - height,
                  width=width, height=height, mask="auto")


def _marker_image(marker_id: int) -> Image.Image:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    return Image.fromarray(cv2.aruco.generateImageMarker(dictionary, marker_id, 168))


def generate_paper(table: dict, run_id: str, output_dir: Path) -> dict:
    rows = table.get("rows", [])
    fields = table.get("editableColumns", [])
    if not rows or len(rows) > 500 or not 1 <= len(fields) <= 2:
        raise ValueError("Choose a table with 1-500 rows and one or two editable fields.")
    if not run_id:
        raise ValueError("A print run ID is required.")
    context_field = next((header for header in table.get("headers", [])
                          if header != table["idColumn"] and header not in fields
                          and any(row["cells"].get(header, {}).get("kind") == "text"
                                  and str(row["cells"][header]["value"]).strip()
                                  for row in rows)), None)
    context_labels = {
        row["id"]: str(row["cells"].get(context_field, {}).get("value", ""))
        if context_field else ""
        for row in rows
    }
    font = pdfmetrics.getFont(_font())
    printable = [table["sheet"], table["idColumn"], *fields]
    printable.extend(str(row["label"]) for row in rows)
    printable.extend(context_labels.values())
    printable.extend(str(row["cells"][field]["value"]) for row in rows for field in fields)
    if any(ord(char) not in font.face.charToGlyph for value in printable for char in value
           if char not in "\n\r\t"):
        raise ValueError("This print template cannot render a character in this table. Use basic Latin labels and values for this local version.")

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "paper.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=1)
    pdf.setTitle(f"PaperPatch - {table['sheet']}")
    pdf.setAuthor("PaperPatch")
    pages = []
    page_count = math.ceil(len(rows) / ROWS_PER_PAGE)
    field_width = 388 / len(fields)
    for page_index in range(page_count):
        page_number = page_index + 1
        boxes = []
        for marker_id, (x, top) in enumerate(ANCHORS):
            _draw_image(pdf, _marker_image(marker_id), x, top, MARKER_SIZE, MARKER_SIZE)
        payload = json.dumps({"v": 1, "run": run_id, "page": page_number}, separators=(",", ":"))
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=8)
        qr.add_data(payload)
        qr.make(fit=True)
        _draw_image(pdf, qr.make_image().convert("RGB"), 472, 58, 80, 80)
        _text(pdf, "PaperPatch", 62, 59, 25)
        _text(pdf, f"{table['sheet']}  /  field update sheet", 63, 80, 10, width=390)
        _text(pdf, "Write one line per box. Leave blank for no change.", 63, 103, 8.5, width=400)
        _text(pdf, "Photograph this entire page, including all four corner markers.", 63, 117, 8.5, width=400)
        _text(pdf, table["idColumn"], 69, 155, 9, width=82)
        for field_index, field in enumerate(fields):
            _text(pdf, field, 168 + field_index * field_width, 155, 9, width=field_width - 16)
        pdf.setStrokeColor(HexColor("#526476"))
        pdf.setLineWidth(0.8)
        pdf.line(62, PAGE_HEIGHT - 164, 550, PAGE_HEIGHT - 164)

        for row_index, row in enumerate(rows[page_index * ROWS_PER_PAGE:(page_index + 1) * ROWS_PER_PAGE]):
            row_top = 169 + row_index * 51
            context_label = context_labels[row["id"]]
            if context_label:
                _text(pdf, str(row["label"]), 69, row_top + 12, 7.5, "#536170", width=82)
                for line_index, line in enumerate(_label_lines(context_label, 82, 8)):
                    _text(pdf, line, 69, row_top + 27 + line_index * 10, 8)
            else:
                _text(pdf, str(row["label"]), 69, row_top + 26, 9, width=82)
            for field_index, field in enumerate(fields):
                cell = row["cells"][field]
                x = 162 + field_index * field_width
                old_value = str(cell["value"]) if cell["value"] != "" else "(empty)"
                _text(pdf, f"Was: {old_value}", x + 6, row_top + 10, 7.5, "#536170", field_width - 14)
                if cell.get("protected") or cell["kind"] == "formula":
                    _text(pdf, "Read-only formula / protected cell", x + 6, row_top + 33, 8, "#536170", field_width - 14)
                    continue
                box_top = row_top + 16
                box_width = field_width - 12
                pdf.setStrokeColor(HexColor("#8796a4"))
                pdf.setLineWidth(0.55)
                pdf.rect(x + 6, PAGE_HEIGHT - box_top - 27, box_width, 27)
                box_id = sha256(f"{run_id}\0{row['id']}\0{field}".encode()).hexdigest()[:20]
                boxes.append({"id": box_id, "rowId": row["id"], "field": field,
                              "rowLabel": str(row["label"]), "contextLabel": context_label,
                              "rect": [x + 8, box_top + 2, box_width - 4, 23]})
            pdf.setStrokeColor(HexColor("#e1e6eb"))
            pdf.setLineWidth(0.4)
            pdf.line(62, PAGE_HEIGHT - row_top - 49, 550, PAGE_HEIGHT - row_top - 49)

        _text(pdf, "Review every transcription before updating your workbook.", 63, 712, 8)
        _text(pdf, "Long printed values may be abbreviated. Blank boxes never clear a cell.", 63, 727, 7.5, "#536170")
        _text(pdf, f"Copy {run_id[:12]}  /  Page {page_number} of {page_count}", 63, 750, 8)
        pages.append({"number": page_number, "boxes": boxes, "qrPayload": payload,
                      "previewUrl": f"page-{page_number}.png"})
        pdf.showPage()
    pdf.save()

    with pymupdf.open(pdf_path) as document:
        for index, page in enumerate(document):
            page.get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False).save(output_dir / pages[index]["previewUrl"])
    return {"version": 1, "runId": run_id, "pageWidth": PAGE_WIDTH, "pageHeight": PAGE_HEIGHT,
            "scale": SCALE, "pdf": "paper.pdf", "pages": pages, "contextField": context_field}


@lru_cache(maxsize=1)
def load_ocr() -> tuple:
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    processor = TrOCRProcessor.from_pretrained(MODEL_ID, local_files_only=True, use_fast=False)
    model = VisionEncoderDecoderModel.from_pretrained(MODEL_ID, local_files_only=True)
    model.eval()
    return processor, model


def _read_photo(image_bytes: bytes) -> np.ndarray:
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Photo exceeds the 20 MB limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_bytes)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Photo exceeds the 25 megapixel limit.")
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((3300, 3300), Image.Resampling.LANCZOS)
                return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Could not read this photo. Upload a JPEG, PNG, or WebP under 25 megapixels.") from exc


def _align_photo(photo: np.ndarray, capture_warnings: list[str] | None = None) -> np.ndarray:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(dictionary, parameters)
    most_markers = 0
    for reflected in (False, True):
        candidate = cv2.flip(photo, 1) if reflected else photo
        corners, ids, _ = detector.detectMarkers(cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY))
        found = {} if ids is None else {int(marker_id): corner.reshape(4, 2)
                                        for marker_id, corner in zip(ids.flatten(), corners)
                                        if int(marker_id) in range(4)}
        most_markers = max(most_markers, len(found))
        if len(found) == 4:
            photo = candidate
            break
    else:
        detail = f"Only {most_markers} of the 4 corner markers could be read." if most_markers else "No PaperPatch corner markers could be read."
        raise ValueError(f"{detail} Photograph the printed sheet with all four corners fully in frame. Keep the camera steady and avoid glare over the markers.")
    if ids is not None and any(np.count_nonzero(ids == marker_id) != 1 for marker_id in range(4)):
        raise ValueError("Multiple PaperPatch pages were detected. Photograph one page at a time.")
    source = np.concatenate([found[marker_id] for marker_id in range(4)]).astype(np.float32)
    destination = np.array([[(x, y), (x + MARKER_SIZE, y), (x + MARKER_SIZE, y + MARKER_SIZE),
                             (x, y + MARKER_SIZE)] for x, y in ANCHORS], dtype=np.float32).reshape(-1, 2) * SCALE
    if any(np.linalg.norm(points[1] - points[0]) < 14 for points in found.values()):
        raise ValueError("The page is too small in this photo. Move closer while keeping all corner markers visible.")
    transform, _ = cv2.findHomography(source, destination, method=0)
    if transform is None or not np.isfinite(transform).all():
        raise ValueError("Could not align this page. Place it flat and photograph it again.")
    projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), transform).reshape(-1, 2)
    if np.sqrt(np.mean(np.sum((projected - destination) ** 2, axis=1))) > 7:
        raise ValueError("The page appears bent or the markers are unclear. Flatten the paper and retake the photo.")
    if reflected and capture_warnings is not None:
        capture_warnings.append("This photo was mirrored. The aligned page has been flipped back so its writing can be reviewed normally.")
    return cv2.warpPerspective(photo, transform, (PAGE_WIDTH * SCALE, PAGE_HEIGHT * SCALE),
                               flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _photo_quality(aligned: np.ndarray) -> dict:
    writing_area = aligned[169 * SCALE:679 * SCALE, 62 * SCALE:550 * SCALE]
    gray = cv2.cvtColor(writing_area, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    low, high = np.percentile(gray, [0.5, 99.5])
    contrast = float(high - low)
    background = [float(np.percentile(tile, 90))
                  for row in np.array_split(gray, 4)
                  for tile in np.array_split(row, 4, axis=1)]
    quality_warnings = []
    if blur_score < 40:
        quality_warnings.append("The photo may be soft or out of focus. Inspect each reading against the page before accepting it.")
    if contrast < 50:
        quality_warnings.append("The page has low contrast. Check for faint writing that may have been missed.")
    if float(np.median(gray)) < 90:
        quality_warnings.append("The page appears dark. Better lighting may make the handwriting easier to read.")
    if max(background) - min(background) > 65:
        quality_warnings.append("Lighting varies across the page. Inspect shaded areas for missed or incorrect readings.")
    return {"blurScore": round(blur_score, 2), "contrast": round(contrast, 2),
            "warnings": quality_warnings}


def _ink_bounds(crop: np.ndarray, sensitive: bool = False) -> tuple[int, int, int, int] | None:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 31, 8 if sensitive else 17)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    components = [stats[index] for index in range(1, count)
                  if stats[index, cv2.CC_STAT_AREA] >= (3 if sensitive else 5)
                  and stats[index, cv2.CC_STAT_HEIGHT] >= (2 if sensitive else 3)]
    if not components or sum(int(item[4]) for item in components) < max(10 if sensitive else 15, gray.size * (0.0002 if sensitive else 0.0004)):
        return None
    if max(int(item[3]) for item in components) < (3 if sensitive else 6):
        return None
    left = max(0, min(int(item[0]) for item in components) - 10)
    top = max(0, min(int(item[1]) for item in components) - 10)
    right = min(crop.shape[1], max(int(item[0] + item[2]) for item in components) + 10)
    bottom = min(crop.shape[0], max(int(item[1] + item[3]) for item in components) + 10)
    return left, top, right, bottom


def _ocr_image(crop: np.ndarray, bounds: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = bounds
    line = Image.fromarray(cv2.cvtColor(crop[top:bottom, left:right], cv2.COLOR_BGR2GRAY))
    line = ImageOps.autocontrast(line).convert("RGB")
    width = max(line.width, line.height)
    padded = Image.new("RGB", (width, line.height), "white")
    padded.paste(line, ((width - line.width) // 2, 0))
    return padded


def _usable_transcription(text: str, ended: bool) -> bool:
    if not ended or not text or len(text) > MAX_OCR_CHARACTERS:
        return False
    if re.search(r"(.)\1{7,}", re.sub(r"\s", "", text)):
        return False
    words = text.casefold().split()
    for width in range(1, min(4, len(words) // 3) + 1):
        for start in range(len(words) - width * 3 + 1):
            phrase = words[start:start + width]
            if words[start:start + width * 3] == phrase * 3:
                return False
    return True


def scan_photo(image_bytes: bytes, manifest: dict, output_dir: Path, run_id: str,
               progress: Callable[[str, int, int], None] | None = None) -> dict:
    if manifest.get("version") != 1 or manifest.get("runId") != run_id:
        raise ValueError("This print manifest does not match the current copy.")
    notify = progress or (lambda _stage, _done, _total: None)
    notify("Aligning page", 0, 1)
    capture_warnings = []
    aligned = _align_photo(_read_photo(image_bytes), capture_warnings)
    payload, _, _ = cv2.QRCodeDetector().detectAndDecode(aligned)
    if not payload:
        raise ValueError("Could not read the page QR code. Retake a sharper photo with the entire QR code visible.")
    page = next((item for item in manifest["pages"] if item["qrPayload"] == payload), None)
    if page is None:
        raise ValueError("This paper belongs to another copy or page. Upload a photo from this print run.")
    if json.loads(payload) != {"v": 1, "run": run_id, "page": page["number"]}:
        raise ValueError("This paper does not match the current print run.")
    notify("Aligning page", 1, 1)
    quality = _photo_quality(aligned)
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_id = sha256(image_bytes).hexdigest()[:16]
    aligned_name = f"aligned-{scan_id}.png"
    Image.fromarray(cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB)).save(output_dir / aligned_name)
    boxes = []
    crops = []
    notify("Finding writing", 0, len(page["boxes"]))
    for index, box in enumerate(page["boxes"]):
        x, y, width, height = box["rect"]
        left, top = round(x * SCALE), round(y * SCALE)
        right, bottom = round((x + width) * SCALE), round((y + height) * SCALE)
        crop = aligned[top:bottom, left:right]
        bounds = _ink_bounds(crop)
        ink_status = "detected" if bounds is not None else "blank"
        if bounds is None:
            bounds = _ink_bounds(crop, sensitive=True)
            if bounds is not None:
                ink_status = "uncertain"
        crop_name = f"crop-{scan_id}-{box['id']}.png"
        Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)).save(output_dir / crop_name)
        # Normalized [left, top, width, height] in the saved aligned page, not the source photo.
        rect = [left / aligned.shape[1], top / aligned.shape[0],
                (right - left) / aligned.shape[1], (bottom - top) / aligned.shape[0]]
        record = {"boxId": box["id"], "rowId": box["rowId"], "field": box["field"],
                  "rect": rect, "inkStatus": ink_status, "cropPath": crop_name,
                  "recognitionState": "blank" if bounds is None else "needs_review", "text": ""}
        boxes.append(record)
        if bounds is not None:
            crops.append((record, _ocr_image(crop, bounds)))
        notify("Finding writing", index + 1, len(page["boxes"]))

    proposals = []
    scan_warnings = [*capture_warnings, *quality["warnings"]]
    failed_crops = 0
    unusable_crops = 0
    notify("Reading handwriting", 0, len(crops))
    if crops:
        with OCR_LOCK:
            try:
                import torch

                processor, model = load_ocr()
                ocr_available = True
            except (ImportError, OSError, RuntimeError, ValueError):
                ocr_available = False
                scan_warnings.append("Local handwriting recognition is unavailable. Enter each value from its crop, or finish the model setup and retry this scan.")
            for index, (record, ink_image) in enumerate(crops):
                raw_text = ""
                usable = False
                if ocr_available:
                    try:
                        pixel_values = processor(images=ink_image, return_tensors="pt").pixel_values
                        with torch.inference_mode():
                            token_ids = model.generate(pixel_values, max_new_tokens=MAX_OCR_TOKENS,
                                                       num_beams=4, early_stopping=True)
                        raw_text = processor.batch_decode(token_ids, skip_special_tokens=True)[0]
                        eos = model.generation_config.eos_token_id
                        eos_ids = eos if isinstance(eos, list) else [eos]
                        usable = _usable_transcription(raw_text.strip(), int(token_ids[0, -1]) in eos_ids)
                        if not usable:
                            unusable_crops += 1
                    except (OSError, RuntimeError, ValueError):
                        failed_crops += 1
                text = raw_text.strip() if usable else ""
                state = "read" if usable and record["inkStatus"] == "detected" else "needs_review"
                record.update(text=text, recognitionState=state)
                proposals.append({"id": record["boxId"], "boxId": record["boxId"],
                                  "rowId": record["rowId"], "field": record["field"],
                                  "page": page["number"], "rect": record["rect"],
                                  "text": text, "ocrText": raw_text,
                                  "cropUrl": record["cropPath"], "recognitionState": state,
                                  "source": "ocr"})
                notify("Reading handwriting", index + 1, len(crops))
        if failed_crops:
            scan_warnings.append(f"Recognition failed for {failed_crops} marked boxes. Other readings were kept; enter these values from their crops.")
        if unusable_crops:
            scan_warnings.append(f"Recognition produced incomplete or repetitive output in {unusable_crops} boxes. Inspect those crops and enter their values.")
        if any(record["inkStatus"] == "uncertain" for record, _ in crops):
            scan_warnings.append("Some boxes contain faint or uncertain marks. Check whether each mark is a correction before accepting its reading.")
        if any(not item["text"] for item in proposals):
            scan_warnings.append("Some marked boxes have no transcription. Inspect their crops and enter a value or explicitly keep the current value.")
    else:
        scan_warnings.append("No writing was detected inside the editable boxes. Inspect the page to check for missed marks; nothing will change automatically.")
    return {"proposals": proposals, "page": page["number"], "warnings": scan_warnings,
            "alignedPath": aligned_name, "quality": quality, "boxes": boxes}
