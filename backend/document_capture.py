from collections.abc import Callable
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pymupdf
import qrcode
import reportlab
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from backend.paper import (
    ANCHORS, MARKER_SIZE, OCR_LOCK, PAGE_HEIGHT, PAGE_WIDTH, SCALE,
    _align_photo, _draw_image, _marker_image, _read_photo,
    _usable_transcription,
)
from backend.document_ocr import METADATA, recognize_handwriting_candidates


LEFT = 64
RIGHT = 548
TOP = 148
BOTTOM = 706
MAX_PAGES = 8
MAX_PARAGRAPHS = 60
MAX_EDITS = 18
RECOGNIZER = {
    **METADATA,
    "capturePreprocessing": "White balance; isolate complete replacement region above crossed-out text",
    "pipelineSha256": sha256(Path(__file__).read_bytes()).hexdigest(),
}


@lru_cache(maxsize=1)
def _fonts() -> tuple[str, str]:
    root = Path(reportlab.__file__).parent / "fonts"
    choices = [
        (Path("/System/Library/Fonts/Supplemental/Georgia.ttf"),
         Path("/System/Library/Fonts/Supplemental/Georgia Bold.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")),
        (root / "Vera.ttf", root / "VeraBd.ttf"),
    ]
    regular, bold = next(pair for pair in choices if all(path.exists() for path in pair))
    pdfmetrics.registerFont(TTFont("PaperPatchDocument", str(regular)))
    pdfmetrics.registerFont(TTFont("PaperPatchDocumentBold", str(bold)))
    return "PaperPatchDocument", "PaperPatchDocumentBold"


def _write(pdf: canvas.Canvas, text: str, x: float, top: float,
           size: float, bold: bool = False, gray: float = 0.15) -> None:
    pdf.setFillGray(gray)
    pdf.setFont(_fonts()[int(bold)], size)
    pdf.drawString(x, PAGE_HEIGHT - top, text)


def _validate_paragraphs(paragraphs: list[dict]) -> None:
    if not paragraphs or len(paragraphs) > MAX_PARAGRAPHS:
        raise ValueError(f"Choose a short document with 1–{MAX_PARAGRAPHS} paragraphs.")
    if any(not isinstance(paragraph.get("text"), str)
           or not isinstance(paragraph.get("id"), str) or not paragraph["id"]
           for paragraph in paragraphs):
        raise ValueError("The document contains an invalid paragraph.")
    if sum(len(paragraph.get("text", "")) for paragraph in paragraphs) > 24_000:
        raise ValueError("This review copy supports at most 24,000 characters.")
    if len({paragraph.get("id") for paragraph in paragraphs}) != len(paragraphs):
        raise ValueError("Document paragraph identifiers must be unique.")
    supported = pdfmetrics.getFont(_fonts()[0]).face.charToGlyph
    for paragraph in paragraphs:
        if any(ord(char) not in supported for char in paragraph["text"]
               if char not in "\t\n\r"):
            raise ValueError("The review font cannot render a character in this document. Use a document with supported Latin-script text.")


def generate_document_proof(paragraphs: list[dict], run_id: str,
                            output_dir: Path) -> dict:
    _validate_paragraphs(paragraphs)
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("A valid document review ID is required.")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "review-copy.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=1)
    pdf.setTitle("PaperPatch — document review copy")
    pdf.setAuthor("PaperPatch")
    pages: list[dict] = []
    page: dict = {}
    cursor = TOP
    line_index = 0

    def new_page() -> None:
        nonlocal page, cursor, line_index
        if pages:
            pdf.showPage()
        if len(pages) >= MAX_PAGES:
            raise ValueError(f"This document needs more than {MAX_PAGES} review pages. Choose a shorter draft.")
        number = len(pages) + 1
        payload = json.dumps({"v": 2, "kind": "document", "run": run_id, "page": number},
                             separators=(",", ":"))
        for marker_id, (x, top) in enumerate(ANCHORS):
            _draw_image(pdf, _marker_image(marker_id), x, top, MARKER_SIZE, MARKER_SIZE)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=8)
        qr.add_data(payload)
        qr.make(fit=True)
        _draw_image(pdf, qr.make_image().convert("RGB"), 475, 54, 72, 72)
        _write(pdf, "PaperPatch", LEFT, 72, 22, bold=True)
        _write(pdf, "DOCUMENT REVIEW COPY", LEFT, 91, 7.4, gray=0.4)
        _write(pdf, "Cross out text. Write a replacement directly above it.", LEFT, 111, 8.2)
        _write(pdf, "Use red or blue pen. Keep all four corner markers in your photo.", LEFT, 125, 7.4, gray=0.4)
        pdf.setStrokeColor(HexColor("#d5d3ce"))
        pdf.line(LEFT, PAGE_HEIGHT - 135, RIGHT, PAGE_HEIGHT - 135)
        _write(pdf, "Review layout · original Word formatting is kept on export", LEFT, 732, 7.2, gray=0.45)
        _write(pdf, f"{number:02d}  /  {run_id[:8]}", 460, 732, 7.2, gray=0.45)
        page = {"number": number, "previewPath": f"page-{number}.png", "qrPayload": payload,
                "words": [], "paragraphIds": []}
        pages.append(page)
        cursor = TOP
        line_index = 0

    new_page()
    for paragraph in paragraphs:
        text = paragraph["text"]
        if not text.strip():
            cursor += 12
            continue
        style = str(paragraph.get("style", "")).lower()
        heading = "heading" in style or style in ("title", "subtitle")
        size = 16 if style == "title" else 12.5 if heading else 11.2
        font = _fonts()[int(heading)]
        line_height = 34 if not heading else 35
        if cursor + line_height > BOTTOM:
            new_page()
        x = LEFT
        previous_end = 0
        first_word = True
        for token in re.finditer(r"\S+", text):
            gap = text[previous_end:token.start()]
            if not first_word:
                if "\n" in gap or "\r" in gap:
                    cursor += line_height
                    line_index += 1
                    x = LEFT
                else:
                    x += pdfmetrics.stringWidth(gap.replace("\t", "    "), font, size)
            value = token.group()
            fragments = []
            start = token.start()
            while value:
                count = len(value)
                while count > 1 and pdfmetrics.stringWidth(value[:count], font, size) > RIGHT - LEFT:
                    count -= 1
                fragments.append((value[:count], start, start + count))
                value, start = value[count:], start + count
            for fragment, start, end in fragments:
                width = pdfmetrics.stringWidth(fragment, font, size)
                if x > LEFT and x + width > RIGHT:
                    cursor += line_height
                    line_index += 1
                    x = LEFT
                if cursor + size > BOTTOM:
                    new_page()
                    x = LEFT
                baseline = cursor + size
                _write(pdf, fragment, x, baseline, size, bold=heading,
                       gray=0.12 if paragraph.get("editable", False) else 0.46)
                page["words"].append({"text": fragment, "start": start, "end": end,
                                      "paragraphId": paragraph["id"], "line": line_index,
                                      "editable": bool(paragraph.get("editable", False)),
                                      "rect": [round(x, 3), round(baseline - size * 0.82, 3),
                                               round(width, 3), round(size, 3)]})
                if paragraph["id"] not in page["paragraphIds"]:
                    page["paragraphIds"].append(paragraph["id"])
                x += width
            previous_end = token.end()
            first_word = False
        cursor += line_height + (7 if heading else 9)
        line_index += 1
    pdf.save()
    with pymupdf.open(pdf_path) as document:
        for page_data, rendered in zip(pages, document):
            rendered.get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False).save(output_dir / page_data["previewPath"])
    return {"version": 2, "kind": "document", "runId": run_id,
            "pageWidth": PAGE_WIDTH, "pageHeight": PAGE_HEIGHT, "scale": SCALE,
            "pdfPath": pdf_path.name, "pages": pages,
            "paragraphs": [{"id": paragraph["id"], "text": paragraph["text"]} for paragraph in paragraphs],
            "instructions": "Use red or blue pen. Cross out words and write short replacements directly above them. Other marks can be entered during review.",
            "layoutNote": "A reflowed review copy; the original Word layout is preserved in the exported document."}


def _white_balance(aligned: np.ndarray) -> np.ndarray:
    body = aligned[int(TOP * SCALE):int(BOTTOM * SCALE):4,
                   int(LEFT * SCALE):int(RIGHT * SCALE):4]
    white = np.percentile(body, 95, axis=(0, 1))
    return np.clip(aligned.astype(np.float32) * 255 / np.maximum(white, 30),
                   0, 255).astype(np.uint8)


def _color_mask(aligned: np.ndarray) -> np.ndarray:
    balanced = _white_balance(aligned)
    hsv = cv2.cvtColor(balanced, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    chroma = balanced.max(axis=2).astype(np.int16) - balanced.min(axis=2)
    colors = ((hue <= 20) | (hue >= 160) | ((hue >= 88) & (hue <= 145)))
    mask = np.where(colors & (saturation >= 28) & (value >= 12) & (chroma >= 8),
                    255, 0).astype(np.uint8)
    mask[:int((TOP - 25) * SCALE)] = 0
    mask[int((BOTTOM + 10) * SCALE):] = 0
    mask[:, :int((LEFT - 14) * SCALE)] = 0
    mask[:, int((RIGHT + 9) * SCALE):] = 0
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= 8
    keep[0] = False
    return np.where(keep[labels], 255, 0).astype(np.uint8)


def _check_print_alignment(aligned: np.ndarray, page: dict,
                           color_mask: np.ndarray) -> np.ndarray:
    pdf_bytes = BytesIO()
    pdf = canvas.Canvas(pdf_bytes, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    for word in page['words']:
        x, y, _, size = word['rect']
        _write(pdf, word['text'], x, y + size * 0.82, size, bold=size > 11.3,
               gray=0.12 if word.get('editable', False) else 0.46)
    pdf.save()
    with pymupdf.open(stream=pdf_bytes.getvalue(), filetype='pdf') as document:
        pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False)
        reference = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, 3)
        printed = (reference.min(axis=2) < 190).astype(np.uint8)
    gray = cv2.cvtColor(_white_balance(aligned), cv2.COLOR_BGR2GRAY)
    observed = (gray < 190).astype(np.uint8)
    tolerance = np.ones((7, 7), np.uint8)
    eligible = printed.copy()
    eligible[cv2.dilate(color_mask, tolerance) > 0] = 0
    total = int(eligible.sum())
    if total < max(50, int(printed.sum() * 0.35)):
        raise ValueError('Printed text is obscured or the photo has too much color distortion to separate the ink reliably. Retake it in even, neutral light with the original text visible.')
    hits = eligible & cv2.dilate(observed, tolerance)
    matches = float(hits.sum()) / total
    line_matches = []
    for line in {word['line'] for word in page['words']}:
        words = [word for word in page['words'] if word['line'] == line]
        top = max(0, round(min(word['rect'][1] for word in words) * SCALE) - 3)
        bottom = min(eligible.shape[0], round(max(word['rect'][1] + word['rect'][3] for word in words) * SCALE) + 3)
        count = int(eligible[top:bottom].sum())
        if count >= 80:
            line_matches.append(float(hits[top:bottom].sum()) / count)
    if matches < 0.9 or any(score < 0.85 for score in line_matches):
        raise ValueError('The printed text does not line up with this review copy. Use the original PDF, flatten the page, and photograph it again; no corrections were assigned.')
    unexpected = np.where((gray < 210) & (cv2.dilate(printed, tolerance) == 0), 255, 0).astype(np.uint8)
    unexpected[:int((TOP - 12) * SCALE)] = 0
    unexpected[int((BOTTOM + 10) * SCALE):] = 0
    unexpected[:, :int((LEFT - 14) * SCALE)] = 0
    unexpected[:, int((RIGHT + 9) * SCALE):] = 0
    return unexpected


def _struck_groups(mask: np.ndarray, page: dict) -> tuple[list[dict], np.ndarray]:
    _, components, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    strokes = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=20,
                             minLineLength=20, maxLineGap=7)
    horizontal = []
    stroke_components = {}
    if strokes is not None:
        for x1, y1, x2, y2 in strokes[:, 0]:
            if x2 < x1:
                x1, x2, y1, y2 = x2, x1, y2, y1
            if x2 - x1 < 20 or abs(y2 - y1) > (x2 - x1) * 0.15:
                continue
            labels, counts = np.unique(components[max(0, min(y1, y2) - 3):max(y1, y2) + 4,
                                                  max(0, x1):x2 + 1], return_counts=True)
            choices = [(int(count), int(label)) for label, count in zip(labels, counts) if label]
            if not choices:
                continue
            component_id = max(choices)[1]
            component = stats[component_id]
            if component[cv2.CC_STAT_HEIGHT] > max(12, component[cv2.CC_STAT_WIDTH] * 0.15):
                continue
            stroke = (int(x1), int(y1), int(x2), int(y2))
            horizontal.append(stroke)
            stroke_components[stroke] = component_id
    selected: list[tuple[int, dict]] = []
    stroke_mask = np.zeros_like(mask)
    removed_components = np.zeros(len(stats), dtype=bool)
    for index, word in enumerate(page["words"]):
        x, y, width, height = [coordinate * SCALE for coordinate in word["rect"]]
        matched = []
        intervals = []
        for x1, y1, x2, y2 in horizontal:
            mid_y = (y1 + y2) / 2
            overlap = min(x + width, x2) - max(x, x1)
            if y + height * 0.2 <= mid_y <= y + height * 0.75 and overlap >= 8:
                matched.append((x1, y1, x2, y2))
                intervals.append((max(x, x1), min(x + width, x2)))
        covered, end = 0.0, x
        for left, right in sorted(intervals):
            covered += max(0, right - max(left, end))
            end = max(end, right)
        if covered >= width * 0.8:
            selected.append((index, word))
            for x1, y1, x2, y2 in matched:
                cv2.line(stroke_mask, (x1, y1), (x2, y2), 255, 11)
                removed_components[stroke_components[(x1, y1, x2, y2)]] = True
    groups: list[dict] = []
    for index, word in selected:
        if (groups and groups[-1]["lastIndex"] == index - 1
                and groups[-1]["paragraphId"] == word["paragraphId"]
                and groups[-1]["line"] == word["line"]):
            groups[-1]["words"].append(word)
            groups[-1]["end"] = word["end"]
            groups[-1]["lastIndex"] = index
        else:
            groups.append({"paragraphId": word["paragraphId"], "start": word["start"],
                           "end": word["end"], "line": word["line"], "words": [word],
                           "lastIndex": index})
    stroke_mask[removed_components[components]] = 255
    return groups, cv2.bitwise_and(mask, cv2.bitwise_not(stroke_mask))


def _bounds(mask: np.ndarray, minimum_area: int = 15) -> tuple[int, int, int, int] | None:
    points = cv2.findNonZero(mask)
    if points is None or len(points) < minimum_area:
        return None
    x, y, width, height = cv2.boundingRect(points)
    return int(x), int(y), int(width), int(height)


def _ink_regions(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    clustered = cv2.dilate(mask, np.ones((5, 31), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(clustered, connectivity=8)
    regions = []
    for index in range(1, count):
        x, y, width, height, _ = [int(value) for value in stats[index]]
        bounds = _bounds(mask[y:y + height, x:x + width], 25)
        if bounds and bounds[3] >= 7:
            bx, by, bw, bh = bounds
            regions.append((x + bx, y + by, bw, bh))
    return regions


def _replacement_area(regions: list[tuple[int, int, int, int]], group: dict,
                      groups: list[dict], page: dict) -> tuple[tuple[int, int, int, int] | None, str | None]:
    x = min(word["rect"][0] for word in group["words"])
    right = max(word["rect"][0] + word["rect"][2] for word in group["words"])
    top = min(word["rect"][1] for word in group["words"])
    bottom = max(word['rect'][1] + word['rect'][3] for word in group['words'])
    previous = max((word['rect'][1] + word['rect'][3] for word in page['words']
                    if word['rect'][1] + word['rect'][3] < top - 1), default=TOP - 25)
    candidates = []
    for rect in regions:
        rx, ry, rw, rh = [coordinate / SCALE for coordinate in rect]
        if rx + rw < x - 30 or rx > right + 30 or ry + rh < top - 45 or ry > bottom + 30:
            continue
        overlapping = [other for other in groups if
                       rx < max(word['rect'][0] + word['rect'][2] for word in other['words']) and
                       rx + rw > min(word['rect'][0] for word in other['words'])]
        above = [other for other in overlapping if
                 1 <= min(word['rect'][1] for word in other['words']) - ry - rh <= 35]
        owners = above or [other for other in overlapping if other['line'] == group['line']]
        if owners and group not in owners:
            continue
        if len(owners) > 1:
            return rect, 'Replacement ink crosses more than one correction. Enter each intended replacement manually.'
        candidates.append(rect)
    if not candidates:
        return None, None

    def contiguous(first: tuple, second: tuple) -> bool:
        (ax, ay, aw, ah), (bx, by, _, bh) = sorted((first, second))
        gap = bx - ax - aw
        overlap = min(ay + ah, by + bh) - max(ay, by)
        return 0 <= gap <= min(ah, bh) * 1.5 and overlap >= min(ah, bh) * 0.5

    source_regions = set(candidates)
    pending = list(candidates)
    while pending:
        anchor = pending.pop()
        for region in regions:
            if region not in source_regions and contiguous(anchor, region):
                source_regions.add(region)
                pending.append(region)
    left = min(rect[0] for rect in source_regions)
    upper = min(rect[1] for rect in source_regions)
    lower = max(rect[1] + rect[3] for rect in source_regions)
    end = max(rect[0] + rect[2] for rect in source_regions)
    rect = (left, upper, end - left, lower - upper)
    if len(source_regions) > len(candidates):
        return rect, 'Replacement ink may continue beyond this correction. The full source is shown; enter the intended wording manually.'
    ordered = sorted(candidates)
    for first, second in zip(ordered, ordered[1:]):
        if not contiguous(first, second):
            return rect, 'Several ink regions are near this correction. Enter the intended replacement from the full source image.'
    if (upper / SCALE < previous + 1 or lower / SCALE > top - 1
            or left <= (LEFT - 14) * SCALE or end >= (RIGHT + 9) * SCALE
            or upper <= (TOP - 25) * SCALE):
        return rect, 'Nearby ink overlaps printed text or the edge of the review area. Check the full wording and enter it manually.'
    return rect, None


def _ocr(mask: np.ndarray, rect: tuple[int, int, int, int],
         aligned: np.ndarray | None = None) -> tuple[str, list[str], str | None]:
    x, y, width, height = rect
    image = (aligned[y:y + height, x:x + width] if aligned is not None else
             cv2.cvtColor(255 - mask[y:y + height, x:x + width], cv2.COLOR_GRAY2BGR))
    try:
        with OCR_LOCK:
            reading = recognize_handwriting_candidates(image)
            text = reading['text']
            alternatives = [value for value in reading['alternatives']
                            if value != text and _usable_transcription(value, True)][:1]
            if _usable_transcription(text, True):
                return text, alternatives, reading['warning']
            warning = "The primary handwriting reading was empty or unusable. Check any other machine reading against the source, or enter the wording manually."
            if reading['warning']:
                warning += " " + reading['warning']
            return "", alternatives, warning
    except (ImportError, OSError, RuntimeError, ValueError):
        return "", [], "Local handwriting recognition is unavailable. The detected marks are kept; enter their wording during review."


def _normalized(rect: tuple[int, int, int, int], shape: tuple) -> list[float]:
    x, y, width, height = rect
    return [x / shape[1], y / shape[0], width / shape[1], height / shape[0]]


def _crop(aligned: np.ndarray, rect: tuple[int, int, int, int], path: Path) -> None:
    x, y, width, height = rect
    Image.fromarray(cv2.cvtColor(aligned[y:y + height, x:x + width], cv2.COLOR_BGR2RGB)).save(path)


def scan_document_photo(image_bytes: bytes, manifest: dict, output_dir: Path,
                        progress: Callable[[str, int, int], None] | None = None) -> dict:
    if manifest.get("version") != 2 or manifest.get("kind") != "document":
        raise ValueError("This is not a document review print run.")
    notify = progress or (lambda _stage, _done, _total: None)
    notify("Aligning review page", 0, 1)
    warnings: list[str] = []
    original = _read_photo(image_bytes)
    aligned = _align_photo(original, warnings)
    detector = cv2.QRCodeDetector()
    payload, _, _ = detector.detectAndDecode(aligned[44 * SCALE:136 * SCALE, 465 * SCALE:557 * SCALE])
    if not payload:
        payload, _, _ = detector.detectAndDecode(aligned)
    if not payload:
        raise ValueError("The review-page QR could not be read. Photograph the whole flat page in good light.")
    page = next((item for item in manifest["pages"] if item["qrPayload"] == payload), None)
    if page is None:
        raise ValueError("This photo belongs to another document or review copy. Use the PDF from this document.")
    if json.loads(payload) != {"v": 2, "kind": "document", "run": manifest["runId"], "page": page["number"]}:
        raise ValueError("The page identifier does not match this review copy.")
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_id = sha256(image_bytes).hexdigest()[:16]
    original_name = f"original-{scan_id}.png"
    aligned_name = f"aligned-{scan_id}.png"
    Image.fromarray(cv2.cvtColor(original, cv2.COLOR_BGR2RGB)).save(output_dir / original_name)
    Image.fromarray(cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB)).save(output_dir / aligned_name)
    notify("Finding pen marks", 0, 1)
    mask = _color_mask(aligned)
    unexpected = _check_print_alignment(aligned, page, mask)
    groups, writing_mask = _struck_groups(mask, page)
    balanced = _white_balance(aligned)
    writing_regions = _ink_regions(writing_mask)
    unknown_mask = cv2.bitwise_and(unexpected, cv2.bitwise_not(
        cv2.dilate(mask, np.ones((5, 5), np.uint8))))
    unknown_regions = _ink_regions(unknown_mask)
    replacement_areas = [_replacement_area(writing_regions, group, groups, page)
                         for group in groups[:MAX_EDITS]]
    unknown_areas = [_replacement_area(unknown_regions, group, groups, page)
                     for group in groups[:MAX_EDITS]]
    shared_groups = set()
    shared_mask = np.zeros_like(mask)
    for regions, areas in ((writing_regions, replacement_areas),
                           (unknown_regions, unknown_areas)):
        for rx, ry, rw, rh in regions:
            owners = []
            for index, (area, _) in enumerate(areas):
                if area is None:
                    continue
                x, y, width, height = area
                if x <= rx and y <= ry and x + width >= rx + rw and y + height >= ry + rh:
                    owners.append(index)
            if len(owners) > 1:
                shared_groups.update(owners)
                shared_mask[ry:ry + rh, rx:rx + rw] = 255
    manual_mask = np.zeros_like(mask)
    for x, y, width, height in unknown_regions:
        manual_mask[y:y + height, x:x + width] = unknown_mask[y:y + height, x:x + width]
    if len(groups) > MAX_EDITS:
        warnings.append(f"Only the first {MAX_EDITS} crossed-out spans were proposed. Review the full page and add any remaining edits manually.")
    edits = []
    assigned_mask = np.zeros_like(mask)
    for index, group in enumerate(groups[:MAX_EDITS]):
        notify("Reading replacement text", index, min(len(groups), MAX_EDITS))
        words = group["words"]
        x = round((min(word["rect"][0] for word in words) - 6) * SCALE)
        top = round((min(word["rect"][1] for word in words) - 30) * SCALE)
        right = round((max(word["rect"][0] + word["rect"][2] for word in words) + 8) * SCALE)
        bottom = round((max(word["rect"][1] + word["rect"][3] for word in words) + 5) * SCALE)
        replacement_rect, ambiguity = replacement_areas[index]
        unknown_rect, _ = unknown_areas[index]
        if unknown_rect and replacement_rect:
            rx, ry, rw, rh = replacement_rect
            ux, uy, uw, uh = unknown_rect
            if ux >= rx - 3 and uy >= ry - 3 and ux + uw <= rx + rw + 3 and uy + uh <= ry + rh + 3:
                unknown_rect = None
        if unknown_rect:
            ambiguity = 'Additional ink near this correction could not be separated reliably. Enter its intended replacement, or dismiss it and add a manual deletion.'
            if replacement_rect:
                rx, ry, rw, rh = replacement_rect
                ux, uy, uw, uh = unknown_rect
                left, upper = min(rx, ux), min(ry, uy)
                replacement_rect = (left, upper, max(rx + rw, ux + uw) - left,
                                    max(ry + rh, uy + uh) - upper)
            else:
                replacement_rect = unknown_rect
        if index in shared_groups:
            ambiguity = 'The same ink region is near more than one correction. No reading was assigned; resolve the shared mark manually.'
        text = ""
        alternatives = []
        kind = "delete"
        if replacement_rect:
            kind = "replace"
            if ambiguity:
                warning = ambiguity
            else:
                text, alternatives, warning = _ocr(cv2.bitwise_or(writing_mask, unexpected),
                                                   replacement_rect, balanced)
            if warning and warning not in warnings:
                warnings.append(warning)
            rx, ry, rw, rh = replacement_rect
            x, top = min(x, rx - 6), min(top, ry - 6)
            right, bottom = max(right, rx + rw + 6), max(bottom, ry + rh + 6)
            assigned_mask[ry:ry + rh, rx:rx + rw] = 255
        for word in words:
            wx, wy, ww, wh = [round(coordinate * SCALE) for coordinate in word["rect"]]
            assigned_mask[max(0, wy - 3):wy + wh + 3, max(0, wx - 8):wx + ww + 8] = 255
        x, top = max(0, x), max(0, top)
        right, bottom = min(aligned.shape[1], right), min(aligned.shape[0], bottom)
        rect = (x, top, right - x, bottom - top)
        crop_name = f"edit-{scan_id}-{index + 1}.png"
        _crop(aligned, rect, output_dir / crop_name)
        edits.append({"paragraphId": group["paragraphId"], "start": group["start"], "end": group["end"],
                      "replacement": text, "kind": kind, "cropPath": crop_name,
                      "rect": _normalized(rect, aligned.shape), "source": "ocr",
                      "recognitionState": "needs_review", "ocrText": text,
                      "ocrAlternatives": alternatives,
                      "page": page["number"], "protected": not all(word.get("editable", False) for word in words)})
    remaining = cv2.bitwise_and(cv2.bitwise_or(mask, manual_mask),
                               cv2.bitwise_or(cv2.bitwise_not(assigned_mask), shared_mask))
    clustered = cv2.dilate(remaining, np.ones((11, 21), np.uint8), iterations=1)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(clustered, connectivity=8)
    marks = []
    for index in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[index]]
        if area < 100 or cv2.countNonZero(remaining[y:y + height, x:x + width]) < 20:
            continue
        if len(marks) == 30:
            warnings.append("There are more than 30 unassigned ink regions. Inspect the full page or retake it on a plain background.")
            break
        x, y = max(0, x - 8), max(0, y - 8)
        width, height = min(aligned.shape[1] - x, width + 16), min(aligned.shape[0] - y, height + 16)
        name = f"mark-{scan_id}-{len(marks) + 1}.png"
        rect = (x, y, width, height)
        _crop(aligned, rect, output_dir / name)
        marks.append({"id": f"mark-{len(marks) + 1}", "rect": _normalized(rect, aligned.shape),
                      "cropPath": name, "assigned": False,
                      "reason": "Unassigned ink, including marks that cannot be read automatically. Add the intended edit manually or mark it as not an edit."})
    if marks:
        warnings.append(f"{len(marks)} other pen-mark region(s) need page inspection. Add any intended insertion or ambiguous correction manually.")
    if not edits:
        warnings.append("No clear red or blue strike-through was detected. Inspect the page and add edits manually, or retake a sharper photo.")
    warnings.append("Confirm every proposed edit and inspect the whole page for missed marks. Black-pencil edits and freeform instructions are not recognized automatically.")
    gray = cv2.cvtColor(aligned[int(TOP * SCALE):int(BOTTOM * SCALE), int(LEFT * SCALE):int(RIGHT * SCALE)], cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if blur < 45:
        warnings.append("This page looks soft. A sharper photo will make small corrections easier to review.")
    notify("Ready for review", len(edits), len(edits))
    return {"page": page["number"], "alignedPath": aligned_name, "originalPath": original_name,
            "warnings": warnings, "edits": edits, "marks": marks,
            "quality": {"blurScore": round(blur, 2)}, "source": "photo", "recognizer": RECOGNIZER}


def illustrate_document_page(manifest: dict, page_number: int, edits: list[dict],
                             proof_dir: Path, output_path: Path) -> Path:
    page = next(item for item in manifest["pages"] if item["number"] == page_number)
    image = Image.open(proof_dir / page["previewPath"]).convert("RGB")
    draw = ImageDraw.Draw(image)
    candidates = [Path("/System/Library/Fonts/Supplemental/Apple Chancery.ttf"),
                  Path("/System/Library/Fonts/Supplemental/Georgia Italic.ttf"),
                  Path(reportlab.__file__).parent / "fonts" / "VeraIt.ttf"]
    path = next(path for path in candidates if path.exists())
    font = ImageFont.truetype(str(path), 15 * SCALE)
    for edit in edits:
        words = [word for word in page["words"] if word["paragraphId"] == edit["paragraphId"]
                 and word["start"] >= edit["start"] and word["end"] <= edit["end"]]
        for line in sorted({word["line"] for word in words}):
            row = [word for word in words if word["line"] == line]
            x = min(word["rect"][0] for word in row)
            right = max(word["rect"][0] + word["rect"][2] for word in row)
            top = min(word["rect"][1] for word in row)
            mid = top + row[0]["rect"][3] * 0.46
            draw.line(((x - 1) * SCALE, mid * SCALE, (right + 2) * SCALE, (mid - 0.5) * SCALE),
                      fill="#b82e2c", width=2 * SCALE)
        if words and edit.get("replacement"):
            x, top, _, _ = words[0]["rect"]
            draw.text((x * SCALE, (top - 25) * SCALE), edit["replacement"], fill="#b82e2c", font=font)
    note_font = ImageFont.truetype(str(candidates[-1]), 7 * SCALE)
    draw.rectangle((60 * SCALE, 736 * SCALE, 552 * SCALE, 744 * SCALE), fill="white")
    draw.text((64 * SCALE, 736 * SCALE), "Prepared example — illustrative ink, not a physical handwriting test", fill="#56534d", font=note_font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path
