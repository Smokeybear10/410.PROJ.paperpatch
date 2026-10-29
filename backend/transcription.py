from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, RLock
from time import perf_counter
from uuid import uuid4
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile
import json
import logging
import re

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.document_ocr import METADATA, recognize_handwriting_candidates
from backend.paper import MAX_IMAGE_BYTES, OCR_LOCK, _read_photo


DATA = Path(__file__).resolve().parent.parent / '.data' / 'transcriptions'
DATA.mkdir(parents=True, exist_ok=True)
LOCK = RLock()
SLOTS = BoundedSemaphore(2)
WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paperpatch-transcribe')
ACTIVE: set[str] = set()
MAX_LINES = 120
router = APIRouter(prefix='/api/transcriptions')
logger = logging.getLogger(__name__)


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)


class LineUpdate(Revision):
    text: str = Field(max_length=2000)
    confirmed: bool
    dismissed: bool
    paragraphBreak: bool

    @field_validator('text')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if any(not (char in '\t\n\r' or 0x20 <= ord(char) <= 0xD7FF
                    or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
               for char in value):
            raise ValueError('Remove characters that cannot be stored in a Word document.')
        return value.replace('\r\n', '\n').replace('\r', '\n')


class NewLine(Revision):
    afterId: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    text: str = Field(min_length=1, max_length=2000)
    paragraphBreak: bool = True
    _valid_text = field_validator('text')(LineUpdate.valid_text.__func__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_dir(sid: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', sid) or not (DATA / sid / 'session.json').is_file():
        raise HTTPException(404, 'Handwriting page not found. Upload a page to begin.')
    return DATA / sid


def save(session: dict) -> None:
    path = DATA / session['id']
    pending = path / 'session.pending.json'
    pending.write_text(json.dumps(session, ensure_ascii=False))
    pending.replace(path / 'session.json')


def load(sid: str) -> dict:
    session = json.loads((session_dir(sid) / 'session.json').read_text())
    if session['status'] == 'processing' and sid not in ACTIVE:
        session.update(status='failed', error='Reading stopped when PaperPatch restarted. Upload the page again.')
        session['revision'] += 1
        save(session)
    return session


def present(session: dict) -> dict:
    return {key: value for key, value in session.items() if not key.startswith('_')}


def file_url(sid: str, name: str) -> str:
    return f'/api/transcriptions/{sid}/files/{name}'


def require_revision(session: dict, revision: int) -> None:
    if session['status'] != 'ready':
        raise HTTPException(409, 'Wait for the page to finish reading before reviewing or exporting.')
    if session['revision'] != revision:
        raise HTTPException(409, 'This page changed. Reload it before saving your review.')


def changed(session: dict) -> dict:
    session.update(pageReviewed=False, export=None)
    session['revision'] += 1
    save(session)
    return present(session)


def detect_lines(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = []
    keep = np.zeros(count, dtype=bool)
    minimum_area = max(3, round(width * height / 180000))
    edge = max(1, round(width * .025))
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if (area >= minimum_area and w < width * .8 and h < height * .15
                and x > edge and x + w < width - edge):
            components.append((int(x), int(y), int(w), int(h)))
            keep[index] = True
    mask[~keep[labels]] = 0
    heights = [component[3] for component in components if component[3] > 2]
    if not heights:
        return []
    character_height = float(np.median(heights))
    projection = mask.sum(axis=1).astype(float) / 255
    smooth = cv2.GaussianBlur(projection.reshape(-1, 1), (1, 0),
                             max(.8, character_height * .15)).ravel()
    candidates = [row for row in range(1, height - 1)
                  if smooth[row] >= smooth[row - 1] and smooth[row] > smooth[row + 1]
                  and smooth[row] > max(3, width * .015)]
    peaks = []
    for row in sorted(candidates, key=lambda value: smooth[value], reverse=True):
        if all(abs(row - previous) > character_height * 1.15 for previous in peaks):
            peaks.append(row)
    peaks.sort()
    boundaries = [0, *[int((first + second) / 2) for first, second in zip(peaks, peaks[1:])], height]
    rectangles = []
    padding = max(2, round(character_height * .3))
    for top, bottom in zip(boundaries, boundaries[1:]):
        rows, columns = np.where(mask[top:bottom] > 0)
        if len(columns) < minimum_area * 4:
            continue
        left, right = int(columns.min()), int(columns.max()) + 1
        upper, lower = top + int(rows.min()), top + int(rows.max()) + 1
        rectangles.append((max(0, left - padding), max(0, upper - padding),
                           min(width, right + padding), min(height, lower + padding)))
    return rectangles


def read_page(sid: str, image: np.ndarray) -> None:
    started = perf_counter()
    try:
        rectangles = detect_lines(image)
        height, width = image.shape[:2]
        truncated = len(rectangles) > MAX_LINES
        rectangles = rectangles[:MAX_LINES]
        typical_height = float(np.median([bottom - top for _, top, _, bottom in rectangles])) if rectangles else 0
        with LOCK:
            session = load(sid)
            session['progress']['total'] = len(rectangles)
            if truncated:
                session['warning'] = 'Only the first 120 detected lines were read. Add any missing text during review.'
            if not rectangles:
                session['warning'] = 'No handwriting lines were detected. Check the full page and add its text manually.'
            session['revision'] += 1
            save(session)
        previous_bottom = 0
        for index, (left, top, right, bottom) in enumerate(rectangles):
            line_id = uuid4().hex
            crop = image[top:bottom, left:right]
            crop_name = f'line-{line_id}.png'
            if not cv2.imwrite(str(DATA / sid / crop_name), crop):
                raise OSError('Could not save the handwriting crop.')
            try:
                with OCR_LOCK:
                    reading = recognize_handwriting_candidates(crop)
            except (ImportError, OSError, RuntimeError, ValueError):
                reading = dict(text='', alternatives=[], warning='The local reader could not read this line. Enter it from the source page.')
            line = dict(id=line_id, rect=[left / width, top / height, (right - left) / width,
                                         (bottom - top) / height], cropUrl=file_url(sid, crop_name),
                        ocrText=reading['text'], ocrAlternatives=reading['alternatives'],
                        text=reading['text'], confirmed=False, dismissed=False,
                        paragraphBreak=index == 0 or top - previous_bottom > typical_height * .6,
                        source='ocr', warning=reading['warning'])
            previous_bottom = bottom
            with LOCK:
                session = load(sid)
                session['lines'].append(line)
                session['_files'][crop_name] = {'mediaType': 'image/png'}
                session['progress']['completed'] = index + 1
                session['revision'] += 1
                save(session)
        with LOCK:
            session = load(sid)
            session.update(status='ready', captureSeconds=round(perf_counter() - started, 3))
            session['revision'] += 1
            save(session)
    except Exception:
        logger.exception('Could not transcribe page %s', sid)
        with LOCK:
            session = load(sid)
            session.update(status='failed', error='The local reader stopped. Your original page is saved; upload it again to retry.')
            session['revision'] += 1
            save(session)
    finally:
        with LOCK:
            ACTIVE.discard(sid)
        SLOTS.release()


@router.post('')
def upload_page(file: UploadFile) -> dict:
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'Two pages are already being read. Wait for one to finish.')
    submitted = False
    try:
        data = file.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError('Photo exceeds the 20 MB limit.')
        image = _read_photo(data)
        with Image.open(BytesIO(data)) as source:
            formats = {'JPEG': ('jpg', 'image/jpeg'), 'PNG': ('png', 'image/png'), 'WEBP': ('webp', 'image/webp')}
            if source.format not in formats:
                raise ValueError('Upload one JPEG, PNG, or WebP page.')
            extension, content_type = formats[source.format]
        height, width = image.shape[:2]
        if min(height, width) < 24:
            raise ValueError('Upload a larger image with readable handwriting.')
        sid = uuid4().hex
        path = DATA / sid
        path.mkdir(parents=True)
        original_name = f'original.{extension}'
        (path / original_name).write_bytes(data)
        if not cv2.imwrite(str(path / 'page.png'), image):
            raise OSError('Could not save the page for review.')
        filename = (file.filename or 'Handwritten page').replace('\\', '/').rsplit('/', 1)[-1][:180]
        session = dict(id=sid, revision=0, status='processing', filename=filename,
                       sourceSha256=sha256(data).hexdigest(), imageWidth=width, imageHeight=height,
                       previewUrl=file_url(sid, 'page.png'), originalUrl=file_url(sid, original_name),
                       createdAt=now(), lines=[], progress={'completed': 0, 'total': 0},
                       warning=('This image is small. Check every reading and the full page for missing text.'
                                if min(width, height) < 700 else
                                'Check the full page for missed text, especially columns, margins, and angled writing.'),
                       error=None, pageReviewed=False, export=None,
                       recognizer={**METADATA, 'lineDetection': 'Single-column horizontal ink projection with component filtering and midpoint line boundaries.',
                                   'capturePreprocessing': 'EXIF orientation; maximum 3300 pixels per side; source-pixel rectangular line crops. Original upload preserved.'},
                       _files={original_name: {'mediaType': content_type}, 'page.png': {'mediaType': 'image/png'}})
        with LOCK:
            ACTIVE.add(sid)
            save(session)
            try:
                WORKER.submit(read_page, sid, image)
            except RuntimeError:
                ACTIVE.discard(sid)
                raise
            submitted = True
            return present(session)
    finally:
        if not submitted:
            SLOTS.release()


@router.get('/{sid}')
def get_page(sid: str) -> dict:
    with LOCK:
        return present(load(sid))


@router.patch('/{sid}/lines/{line_id}')
def update_line(sid: str, line_id: str, body: LineUpdate) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        line = next((item for item in session['lines'] if item['id'] == line_id), None)
        if line is None:
            raise HTTPException(404, 'Handwriting line not found.')
        if body.confirmed and not body.dismissed and not body.text.strip():
            raise ValueError('Enter this line or dismiss it before confirming.')
        line.update(text=body.text, confirmed=body.confirmed, dismissed=body.dismissed,
                    paragraphBreak=body.paragraphBreak)
        return changed(session)


@router.post('/{sid}/lines')
def add_line(sid: str, body: NewLine) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        if len(session['lines']) >= MAX_LINES * 2:
            raise ValueError('This page has reached the 240-line review limit.')
        if not body.text.strip():
            raise ValueError('Enter the missing handwriting before adding a line.')
        index = 0
        if body.afterId is not None:
            index = next((i + 1 for i, line in enumerate(session['lines']) if line['id'] == body.afterId), -1)
            if index < 0:
                raise HTTPException(404, 'The preceding line was not found.')
        session['lines'].insert(index, dict(id=uuid4().hex, rect=None, cropUrl=None, ocrText='',
                                           ocrAlternatives=[], text=body.text, confirmed=False,
                                           dismissed=False, paragraphBreak=body.paragraphBreak,
                                           source='manual', warning=None))
        return changed(session)


def require_readings(session: dict) -> None:
    if any(not line['dismissed'] and (not line['confirmed'] or not line['text'].strip())
           for line in session['lines']):
        raise HTTPException(409, 'Confirm every reading or dismiss it before finishing the page review.')
    if not any(not line['dismissed'] and line['text'].strip() for line in session['lines']):
        raise HTTPException(409, 'Add at least one nonempty line before finishing this page.')


@router.post('/{sid}/confirm')
def confirm_readings(sid: str, body: Revision) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        if any(not line['dismissed'] and not line['text'].strip() for line in session['lines']):
            raise HTTPException(409, 'Enter or dismiss the empty readings before confirming all lines.')
        for line in session['lines']:
            if not line['dismissed']:
                line['confirmed'] = True
        return changed(session)


@router.post('/{sid}/review')
def review_page(sid: str, body: Revision) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        require_readings(session)
        session.update(pageReviewed=True, export=None)
        session['revision'] += 1
        save(session)
        return present(session)


def document_bytes(lines: list[dict]) -> bytes:
    paragraphs: list[list[str]] = []
    for line in lines:
        if line['dismissed']:
            continue
        if line['paragraphBreak'] or not paragraphs:
            paragraphs.append([])
        paragraphs[-1].append(line['text'])
    body = []
    for paragraph in paragraphs:
        runs = []
        for line in paragraph:
            if runs:
                runs.append('<w:r><w:br/></w:r>')
            for index, part in enumerate(line.split('\n')):
                if index:
                    runs.append('<w:r><w:br/></w:r>')
                runs.append(f'<w:r><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
        body.append('<w:p><w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr>' + ''.join(runs) + '</w:p>')
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + ''.join(body) + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
           '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/></w:sectPr></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
    relationships = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
              '<w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
    document_relationships = ('<?xml version="1.0" encoding="UTF-8"?>'
                              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as package:
        for name, data in {'[Content_Types].xml': content_types, '_rels/.rels': relationships,
                           'word/document.xml': xml, 'word/styles.xml': styles,
                           'word/_rels/document.xml.rels': document_relationships}.items():
            package.writestr(name, data.encode('utf-8'))
    return output.getvalue()


@router.post('/{sid}/export')
def export_page(sid: str, body: Revision) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        require_readings(session)
        if not session['pageReviewed']:
            raise HTTPException(409, 'Check the full page for missed text before exporting.')
        export_id = uuid4().hex
        name, audit_name = f'export-{export_id}.docx', f'audit-{export_id}.json'
        filename = re.sub(r'[^\w .()-]', '-', Path(session['filename']).stem).strip(' .')[:100] or 'Handwritten page'
        data = document_bytes(session['lines'])
        path = session_dir(sid)
        (path / name).write_bytes(data)
        audit = {'version': 1, 'exportedAt': now(), 'sourceSha256': session['sourceSha256'],
                 'sourceFilename': session['filename'], 'sourceUrl': session['originalUrl'],
                 'reviewedRevision': session['revision'], 'pageReviewed': True,
                 'recognizer': session['recognizer'], 'lines': session['lines'],
                 'exportSha256': sha256(data).hexdigest(),
                 'format': 'Editable Word text. Source line breaks preserved; paragraph breaks reviewed by user. No spelling correction or semantic rewriting.'}
        (path / audit_name).write_text(json.dumps(audit, ensure_ascii=False, indent=2))
        session['_files'][name] = {'mediaType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                   'filename': f'{filename}.transcribed.docx'}
        session['_files'][audit_name] = {'mediaType': 'application/json', 'filename': f'{filename}.review.json'}
        session['export'] = {'url': file_url(sid, name), 'auditUrl': file_url(sid, audit_name),
                              'filename': f'{filename}.transcribed.docx'}
        session['revision'] += 1
        save(session)
        return present(session)


@router.get('/{sid}/files/{name}')
def get_file(sid: str, name: str) -> FileResponse:
    with LOCK:
        session = load(sid)
        metadata = session['_files'].get(name)
        if metadata is None or '/' in name or '\\' in name:
            raise HTTPException(404, 'File not found.')
        path = session_dir(sid) / name
        if not path.is_file():
            raise HTTPException(404, 'File not found.')
        return FileResponse(path, media_type=metadata['mediaType'], filename=metadata.get('filename'))
