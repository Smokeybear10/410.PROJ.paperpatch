from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import BoundedSemaphore, RLock
from time import perf_counter
from typing import Literal
from uuid import uuid4
import json
import re

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.docx_engine import inspect_docx, rebase_edit, export_docx
from backend.document_capture import generate_document_proof, scan_document_photo


DATA = Path(__file__).resolve().parent.parent / '.data' / 'documents'
DATA.mkdir(parents=True, exist_ok=True)
LOCK = RLock()
SCANNERS = BoundedSemaphore(2)
PENDING_CAPTURES: dict[str, int] = {}
router = APIRouter(prefix='/api/documents')


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)


class NewEdit(RevisionBody):
    paragraphId: str = Field(pattern=r'^p\d+$')
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str = Field(max_length=2000)
    reviewer: str = Field(min_length=1, max_length=80)
    scanId: str | None = None
    sourceMarkId: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_span(self) -> 'NewEdit':
        if self.end < self.start or (self.start == self.end and not self.replacement):
            raise ValueError('Select text to change, or enter text to insert.')
        if self.sourceMarkId is not None and not self.scanId:
            raise ValueError('Choose the source page for this pen mark.')
        return self


class EditUpdate(RevisionBody):
    replacement: str | None = Field(default=None, max_length=2000)
    confirmed: bool | None = None
    decision: Literal['paper', 'current'] | None = None


class ExportBody(RevisionBody):
    currentHash: str = Field(pattern=r'^[a-f0-9]{64}$')


class MarkUpdate(RevisionBody):
    resolution: Literal['ignored', 'correction'] | None
    editId: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def valid_link(self) -> 'MarkUpdate':
        if (self.resolution == 'correction') != (self.editId is not None):
            raise ValueError('Choose an existing correction when linking a pen mark.')
        return self


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def document_dir(sid: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', sid) or not (DATA / sid / 'session.json').is_file():
        raise HTTPException(404, 'Document review not found. Open a Word draft to begin.')
    return DATA / sid


def load(sid: str) -> dict:
    return json.loads((document_dir(sid) / 'session.json').read_text())


def save(session: dict) -> None:
    path = DATA / session['id']
    path.mkdir(exist_ok=True)
    pending = path / 'session.pending.json'
    pending.write_text(json.dumps(session, ensure_ascii=False))
    pending.replace(path / 'session.json')


def present(session: dict) -> dict:
    result = {key: value for key, value in session.items() if not key.startswith('_')}
    result['pendingCaptures'] = PENDING_CAPTURES.get(session['id'], 0)
    if result['pendingCaptures']:
        result['export'] = None
    pages = {page['number']: page for page in session['_manifest']['pages']}
    result['pages'] = [{**page, 'textRanges': page_text_ranges(pages[page['number']])}
                       for page in session['pages']]
    result['scans'] = [
        {**{key: value for key, value in scan.items() if not key.startswith('_')},
         'marks': [{**mark, 'resolution': mark.get('resolution'), 'editId': mark.get('editId'),
                    'suggestedParagraphId': nearest_mark_paragraph(pages[scan['page']], mark['rect'], session['_manifest'])}
                   for mark in scan.get('marks', [])]}
        for scan in session['scans']
    ]
    result['edits'] = [{**edit, 'sourceMarkId': edit.get('sourceMarkId')} for edit in session['edits']]
    return result


def nearest_mark_paragraph(page: dict, rect: list[float], manifest: dict) -> str | None:
    words = [word for word in page['words'] if word.get('editable')]
    if not words:
        return None
    center_x = (rect[0] + rect[2] / 2) * manifest['pageWidth']
    center_y = (rect[1] + rect[3] / 2) * manifest['pageHeight']
    def distance(word: dict) -> float:
        x, y, width, height = word['rect']
        return max(y - center_y, center_y - y - height, 0) * 4 + max(x - center_x, center_x - x - width, 0)
    return min(words, key=distance)['paragraphId']


def page_text_ranges(page: dict) -> list[dict]:
    ranges = {}
    for word in page['words']:
        pid = word['paragraphId']
        if pid not in ranges:
            ranges[pid] = dict(paragraphId=pid, start=word['start'], end=word['end'])
        else:
            ranges[pid]['start'] = min(ranges[pid]['start'], word['start'])
            ranges[pid]['end'] = max(ranges[pid]['end'], word['end'])
    return list(ranges.values())


def require_resolved_marks(session: dict, scan: dict) -> None:
    for mark in scan.get('marks', []):
        if mark.get('resolution') == 'ignored':
            continue
        if mark.get('resolution') == 'correction' and any(
            edit['id'] == mark.get('editId') and edit.get('scanId') == scan['id']
            and edit.get('page') == scan['page'] for edit in session['edits']
        ):
            continue
        raise ValueError('Resolve every pen mark on this source page: add its correction or mark it as not a correction.')


def require_revision(session: dict, revision: int) -> None:
    if session['revision'] != revision:
        raise HTTPException(409, 'This review changed in another window. Reload before continuing.')


def upload_bytes(file: UploadFile, document: bool = False) -> bytes:
    if document and not (file.filename or '').lower().endswith('.docx'):
        raise ValueError('Choose an ordinary .docx file. Macro-enabled and older Word formats are not supported.')
    limit = (10 if document else 20) * 1024 * 1024
    data = file.file.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError(f'Choose a nonempty file smaller than {limit // 1024 // 1024} MB.')
    return data


def file_url(sid: str, name: str) -> str:
    return f'/api/documents/{sid}/files/{name}'


def safe_name(file: UploadFile) -> str:
    return Path((file.filename or 'draft.docx').replace('\\', '/')).name[:160]


def overlaps(left: dict, right: dict) -> bool:
    if not left.get('currentParagraphId') or left['currentParagraphId'] != right.get('currentParagraphId'):
        return False
    a, b, c, d = (left.get('currentStart'), left.get('currentEnd'), right.get('currentStart'), right.get('currentEnd'))
    if None in (a, b, c, d):
        return False
    if a == b or c == d:
        return max(a, c) <= min(b, d)
    return max(a, c) < min(b, d)


def refresh(session: dict, current: bytes | None = None) -> None:
    path = DATA / session['id']
    baseline = (path / 'baseline.docx').read_bytes()
    current = current if current is not None else (path / 'current.docx').read_bytes()
    for edit in session['edits']:
        target_keys = ('currentParagraphId', 'currentStart', 'currentEnd', 'currentText')
        previous_target = tuple(edit.get(key) for key in target_keys)
        previous_status = edit.get('status')
        result = rebase_edit(baseline, current, edit)
        edit.update(currentParagraphId=None, currentStart=None, currentEnd=None,
                    currentText='', paragraphText='', reason='')
        edit.update(result)
        if edit['decision'] == 'paper' and (
            edit['status'] in ('missing', 'protected')
            or tuple(edit.get(key) for key in target_keys) != previous_target
            or edit['status'] == 'conflict' and previous_status not in ('conflict', 'overlap')
        ):
            edit['decision'] = None
    active = [edit for edit in session['edits'] if edit['decision'] != 'current'
              and edit['status'] in ('ready', 'conflict')]
    competing = set()
    for index, edit in enumerate(active):
        for other in active[index + 1:]:
            if overlaps(edit, other):
                competing.update((edit['id'], other['id']))
    for edit in active:
        if edit['id'] in competing:
            edit['status'] = 'overlap'
            edit['reason'] = 'Two paper corrections affect the same text. Keep one and dismiss the other before export.'
    if session.get('export') and any(
        edit['decision'] is None or edit['decision'] == 'paper' and (
            not edit['confirmed'] or edit['status'] in ('missing', 'protected', 'overlap')
            or any(edit.get(key) is None for key in ('currentParagraphId', 'currentStart', 'currentEnd'))
        ) for edit in session['edits']
    ):
        session['export'] = None


def changed(session: dict) -> None:
    session['revision'] += 1
    session['export'] = None
    refresh(session)
    save(session)


def new_document(data: bytes, filename: str) -> dict:
    inspection = inspect_docx(data)
    if not any(paragraph['editable'] and paragraph['text'].strip() for paragraph in inspection['paragraphs']):
        raise ValueError('This draft has no editable text paragraphs. Use a short document with ordinary paragraphs.')
    sid = uuid4().hex
    path = DATA / sid
    path.mkdir()
    manifest = generate_document_proof(inspection['paragraphs'], sid, path / 'paper')
    (path / 'baseline.docx').write_bytes(data)
    (path / 'current.docx').write_bytes(data)
    warnings = inspection.get('warnings', []) + [
        'The review PDF reflows body text. Headers, footers, images and original page layout stay in the Word file and are not shown in this review copy.'
    ]
    if any(not paragraph['editable'] and paragraph['text'] for paragraph in inspection['paragraphs']):
        warnings.append('Some passages contain tables, fields or other Word structures. They are shown for context and protected from changes.')
    session = dict(id=sid, title=inspection.get('title') or Path(filename).stem,
                   filename=filename, currentFilename=filename, currentHash=sha256(data).hexdigest(),
                   revision=0, demo=False, createdAt=now(), paragraphs=inspection['paragraphs'],
                   currentParagraphs=inspection['paragraphs'],
                   pages=[dict(number=page['number'], previewUrl=file_url(sid, 'paper/' + page['previewPath']),
                               width=manifest['pageWidth'], height=manifest['pageHeight'],
                               textRanges=page_text_ranges(page)) for page in manifest['pages']],
                   printUrl=file_url(sid, 'paper/' + manifest['pdfPath']),
                   baselineUrl=file_url(sid, 'baseline.docx'), currentUrl=file_url(sid, 'current.docx'),
                   scans=[], edits=[], warnings=warnings,
                   export=None, _manifest=manifest, _history=[])
    save(session)
    return session


def validate_replacement(text: str) -> None:
    if len(text) > 2000 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF
                             or ord(char) in (0xFFFE, 0xFFFF) for char in text):
        raise ValueError('Use a short replacement within this paragraph, without line breaks or tabs.')


def create_edit(session: dict, paragraph_id: str, start: int, end: int, replacement: str,
                reviewer: str, source: str, scan_id: str | None = None) -> dict:
    paragraph = next((item for item in session['paragraphs'] if item['id'] == paragraph_id), None)
    if not paragraph or (not paragraph['editable'] and source != 'ocr'):
        raise ValueError('This paragraph contains protected Word structure and cannot be changed here.')
    if start < 0 or end < start or end > len(paragraph['text']) or (start == end and not replacement):
        raise ValueError('Choose a valid text selection in the printed draft.')
    if len(session['edits']) >= 200:
        raise ValueError('This review already has 200 corrections. Start a smaller review.')
    validate_replacement(replacement)
    if not reviewer.strip():
        raise ValueError('Enter a reviewer name.')
    page = next((page['number'] for page in session['_manifest']['pages']
                 if any(word['paragraphId'] == paragraph_id and word['start'] <= start <= word['end']
                        for word in page['words'])), None)
    edit = dict(id=uuid4().hex, paragraphId=paragraph_id, start=start, end=end,
                replacement=replacement, reviewer=reviewer.strip()[:80], scanId=scan_id,
                sourceMarkId=None,
                cropUrl=None, rect=None, page=page, source=source, confirmed=source != 'ocr',
                decision=None, baseText=paragraph['text'][start:end], currentText='',
                status='ready', reason='', currentParagraphId=None, currentStart=None,
                currentEnd=None, paragraphText='')
    session['edits'].append(edit)
    return edit


@router.post('')
def upload_document(file: UploadFile) -> dict:
    with LOCK:
        return present(new_document(upload_bytes(file, True), safe_name(file)))


@router.post('/example')
def example_document() -> dict:
    from backend.document_example import create_example
    return create_example()


@router.post('/trial')
def capture_trial() -> dict:
    from backend.document_example import example_docx

    paragraphs = [
        ('Title', 'A real-pen capture check'),
        ('Normal', 'The doors open at seven for evening visitors.'),
        ('Normal', 'The guided tour returns on Friday at noon.'),
        ('Normal', 'Each group can include 24 visitors inside.'),
        ('Normal', 'Please bring a printed ticket to the entrance.'),
        ('Normal', 'Meet the team at the north entrance before dark.'),
        ('Normal', 'The cafe serves hot drinks during the interval.'),
        ('Normal', 'Leave this sentence unchanged so extra detections can be counted.'),
    ]
    changes = [('seven', 'eight'), ('Friday', 'Monday'), ('24', '18'),
               ('printed', 'digital'), ('north', 'south'), ('hot', '')]
    with LOCK:
        session = new_document(example_docx(paragraphs), 'real-pen-capture-check.docx')
        instructions, expected = [], []
        for index, (before, after) in enumerate(changes, 1):
            paragraph = session['paragraphs'][index]
            start = paragraph['text'].index(before)
            kind = 'replace' if after else 'delete'
            instructions.append(dict(paragraphId=paragraph['id'], before=before, after=after, kind=kind))
            expected.append(dict(paragraphId=paragraph['id'], start=start, end=start + len(before),
                                 before=before, replacement=after, kind=kind))
        session['trial'] = dict(kind='physical_capture', instructions=instructions, expectedCount=len(expected))
        session['_trialExpected'] = expected
        save(session)
        return present(session)


def capture_report(expected: list[dict], proposals: list[dict], capture_kind: str) -> dict:
    items = []
    matched_proposals = set()
    for target in expected:
        matches = [(index, proposal) for index, proposal in enumerate(proposals)
                   if all(proposal[key] == target[key] for key in ('paragraphId', 'start', 'end'))]
        found = len(matches) == 1
        proposal = matches[0][1] if found else None
        if found:
            matched_proposals.add(matches[0][0])
        observed = proposal.get('ocrText', proposal['replacement']) if proposal else None
        exact = bool(found and proposal['kind'] == target['kind'] and observed == target['replacement'])
        items.append(dict(paragraphId=target['paragraphId'], before=target['before'],
                          expected=target['replacement'], observed=observed,
                          observedKind=proposal['kind'] if proposal else None, targetFound=found, exact=exact))
    return dict(expectedCount=len(expected), targetMatches=sum(item['targetFound'] for item in items),
                exactReadings=sum(item['exact'] for item in items),
                missedTargets=sum(not item['targetFound'] for item in items),
                unexpectedTargets=len(proposals) - len(matched_proposals),
                source='physical_reported' if capture_kind == 'physical' else capture_kind,
                items=items,
                scope='One capture attempt. Exact spans and case-sensitive initial readings, including the deletion. Manual repairs do not change these counts. Physical capture is reported by the uploader, not independently verified.')


@router.get('/{sid}')
def get_document(sid: str) -> dict:
    with LOCK:
        session = load(sid)
        refresh(session)
        return present(session)


@router.post('/{sid}/current')
def update_current(sid: str, file: UploadFile, revision: int = Form(...)) -> dict:
    data = upload_bytes(file, True)
    inspection = inspect_docx(data)
    with LOCK:
        session = load(sid)
        require_revision(session, revision)
        path = document_dir(sid)
        temporary = path / 'current.pending.docx'
        temporary.write_bytes(data)
        temporary.replace(path / 'current.docx')
        session.update(currentFilename=safe_name(file), currentHash=sha256(data).hexdigest(),
                       currentParagraphs=inspection['paragraphs'])
        session['warnings'] = list(dict.fromkeys(session['warnings'] + inspection.get('warnings', [])))
        for edit in session['edits']:
            edit['decision'] = None
        changed(session)
        return present(session)


@router.post('/{sid}/scans')
def add_scan(sid: str, file: UploadFile, reviewer: str = Form(...),
             captureKind: Literal['physical', 'unspecified', 'synthetic'] = Form('unspecified')) -> dict:
    if not reviewer.strip() or len(reviewer) > 80:
        raise ValueError('Enter a reviewer name of up to 80 characters.')
    if not SCANNERS.acquire(blocking=False):
        raise HTTPException(429, 'Two pages are already being read. Try again when one finishes.')
    registered = False
    try:
        data = upload_bytes(file)
        digest = sha256(data).hexdigest()
        with LOCK:
            session = load(sid)
            if session.get('trial') and session['scans']:
                raise ValueError('This capture check already has a result. Start a new real-pen check for a separate attempt.')
            if len(session['scans']) >= 24:
                raise ValueError('This review supports up to 24 photographs.')
            if any(scan.get('_hash') == digest for scan in session['scans']):
                raise ValueError('This photograph is already in the review.')
            manifest = session['_manifest']
            scan_id = uuid4().hex
            output = document_dir(sid) / 'scans' / scan_id
            PENDING_CAPTURES[sid] = PENDING_CAPTURES.get(sid, 0) + 1
            registered = True
        output.mkdir(parents=True, exist_ok=True)
        suffix = '.jpg' if data.startswith(b'\xff\xd8\xff') else '.png' if data.startswith(b'\x89PNG') else '.webp' if data[:4] == b'RIFF' and data[8:12] == b'WEBP' else '.bin'
        upload_name = 'source-upload' + suffix
        (output / upload_name).write_bytes(data)
        started = perf_counter()
        try:
            result = scan_document_photo(data, manifest, output)
        except ValueError as error:
            with LOCK:
                session = load(sid)
                if session.get('trial'):
                    session.setdefault('_captureFailures', []).append(dict(
                        filename=safe_name(file), sha256=digest, createdAt=now(),
                        uploadUrl=file_url(sid, f'scans/{scan_id}/{upload_name}'),
                        source='physical_reported' if captureKind == 'physical' else captureKind,
                        error=str(error)))
                    save(session)
            raise
        elapsed = round(perf_counter() - started, 2)
        with LOCK:
            session = load(sid)
            if session.get('trial') and session['scans']:
                raise HTTPException(409, 'Another capture finished first. Start a new real-pen check for this attempt.')
            if len(session['scans']) >= 24 or any(scan.get('_hash') == digest for scan in session['scans']):
                raise HTTPException(409, 'The review changed while this photograph was being read. Reload to continue.')
            if len(session['edits']) + len(result['edits']) > 200:
                raise ValueError('This page would exceed the limit of 200 corrections per review.')
            prefix = f'scans/{scan_id}/'
            scan = dict(id=scan_id, filename=safe_name(file), reviewer=reviewer.strip(), page=result['page'], createdAt=now(),
                        alignedUrl=file_url(sid, prefix + result['alignedPath']),
                        originalUrl=file_url(sid, prefix + result['originalPath']), reviewed=False,
                        uploadUrl=file_url(sid, prefix + upload_name), captureSeconds=elapsed,
                        recognizer=result['recognizer'],
                        warnings=result['warnings'], source='photo', markCount=len(result.get('marks', [])),
                        marks=[{**mark, 'cropUrl': file_url(sid, prefix + mark['cropPath']),
                                'resolution': None, 'editId': None}
                               for mark in result.get('marks', [])], _hash=digest)
            if session.get('trial'):
                scan['captureReport'] = capture_report(session['_trialExpected'], result['edits'], captureKind)
                scan['captureReport']['failedAttemptsBeforeThis'] = len(session.get('_captureFailures', []))
                scan['captureReportUrl'] = file_url(sid, prefix + 'capture-report.json')
                record = dict(version=1, documentId=sid, scanId=scan_id, createdAt=scan['createdAt'],
                              uploadSha256=digest, uploadUrl=scan['uploadUrl'], captureSeconds=elapsed,
                              recognizer=result['recognizer'], report=scan['captureReport'],
                              proposals=result['edits'], warnings=result['warnings'],
                              failedCaptures=session.get('_captureFailures', []))
                (output / 'capture-report.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
            for proposal in result['edits']:
                edit = create_edit(session, proposal['paragraphId'], proposal['start'], proposal['end'],
                                   proposal['replacement'], reviewer, 'ocr', scan_id)
                edit.update(cropUrl=file_url(sid, prefix + proposal['cropPath']), rect=proposal['rect'],
                            page=result['page'], kind=proposal['kind'], ocrText=proposal.get('ocrText', ''),
                            ocrAlternatives=proposal.get('ocrAlternatives', []))
            session['scans'].append(scan)
            changed(session)
            PENDING_CAPTURES[sid] -= 1
            if not PENDING_CAPTURES[sid]:
                del PENDING_CAPTURES[sid]
            registered = False
            return present(session)
    finally:
        if registered:
            with LOCK:
                PENDING_CAPTURES[sid] -= 1
                if not PENDING_CAPTURES[sid]:
                    del PENDING_CAPTURES[sid]
        SCANNERS.release()


@router.post('/{sid}/edits')
def add_edit(sid: str, body: NewEdit) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        scan = next((scan for scan in session['scans'] if scan['id'] == body.scanId), None)
        if body.scanId and not scan:
            raise ValueError('The source page is no longer part of this review.')
        mark = None
        if scan:
            page = next((page for page in session['_manifest']['pages'] if page['number'] == scan['page']), None)
            if page is None or not any(
                span['paragraphId'] == body.paragraphId and span['start'] <= body.start <= body.end <= span['end']
                for span in page_text_ranges(page)
            ):
                raise ValueError('The selected text is not on that source page. Choose text shown on the matching page or add a manual instruction without a photo.')
            if body.sourceMarkId is not None:
                mark = next((mark for mark in scan.get('marks', []) if mark['id'] == body.sourceMarkId), None)
                if mark is None:
                    raise ValueError('This pen mark does not belong to the selected source page.')
                if mark.get('resolution') is not None or mark.get('editId') is not None:
                    raise ValueError('This pen mark is already resolved. Reopen an ignored mark before adding its correction.')
        edit = create_edit(session, body.paragraphId, body.start, body.end, body.replacement,
                           body.reviewer, 'manual', body.scanId)
        if scan:
            edit['page'] = scan['page']
            scan['reviewed'] = False
        if mark is not None:
            edit.update(sourceMarkId=mark['id'], cropUrl=mark['cropUrl'], rect=mark['rect'], page=scan['page'])
            mark.update(resolution='correction', editId=edit['id'])
        changed(session)
        return present(session)


@router.patch('/{sid}/scans/{scan_id}/marks/{mark_id}')
def update_mark(sid: str, scan_id: str, mark_id: str, body: MarkUpdate) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        scan = next((scan for scan in session['scans'] if scan['id'] == scan_id), None)
        if scan is None:
            raise HTTPException(404, 'Source page not found.')
        mark = next((mark for mark in scan.get('marks', []) if mark['id'] == mark_id), None)
        if mark is None:
            raise HTTPException(404, 'Pen mark not found on this source page.')
        if mark.get('resolution') == body.resolution and mark.get('editId') == body.editId:
            return present(session)
        origin = next((edit for edit in session['edits'] if edit.get('scanId') == scan_id
                       and edit.get('sourceMarkId') == mark_id), None)
        if origin is not None:
            raise ValueError('This mark created a correction. Review that correction to choose which text to keep.')
        if body.resolution == 'correction':
            if mark.get('resolution') is not None:
                raise ValueError('Reopen this pen mark before linking it to another correction.')
            edit = next((edit for edit in session['edits'] if edit['id'] == body.editId
                         and edit.get('scanId') == scan_id and edit.get('page') == scan['page']), None)
            if edit is None:
                raise ValueError('Choose a correction from this same source page.')
        mark.update(resolution=body.resolution, editId=body.editId)
        scan['reviewed'] = False
        changed(session)
        return present(session)


@router.patch('/{sid}/edits/{eid}')
def update_edit(sid: str, eid: str, body: EditUpdate) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        edit = next((edit for edit in session['edits'] if edit['id'] == eid), None)
        if not edit:
            raise HTTPException(404, 'Correction not found.')
        if body.replacement is not None and body.replacement != edit['replacement']:
            validate_replacement(body.replacement)
            if edit['start'] == edit['end'] and not body.replacement:
                raise ValueError('An insertion needs text. Dismiss this correction to remove it.')
            edit.update(replacement=body.replacement, confirmed=False, decision=None)
        if body.confirmed is not None:
            if body.confirmed and edit.get('kind') == 'replace' and not edit['replacement']:
                raise ValueError('The scanner found replacement ink but could not read it. Enter the replacement, or dismiss this correction and add a manual deletion.')
            edit['confirmed'] = body.confirmed
            if not body.confirmed:
                edit['decision'] = None
        refresh(session)
        if 'decision' in body.model_fields_set:
            if body.decision == 'paper':
                if not edit['confirmed']:
                    raise ValueError('Check the handwriting and confirm the reading before applying it.')
                if edit['status'] in ('missing', 'protected') or any(edit[key] is None for key in
                        ('currentParagraphId', 'currentStart', 'currentEnd')):
                    raise ValueError('This correction cannot be safely located in the current draft.')
                if any(other['id'] != eid and other['decision'] == 'paper' and overlaps(edit, other)
                       for other in session['edits']):
                    raise ValueError('Another accepted correction affects this text. Dismiss it before choosing this version.')
            edit['decision'] = body.decision
        changed(session)
        return present(session)


@router.post('/{sid}/scans/{scan_id}/review')
def review_scan(sid: str, scan_id: str, body: RevisionBody) -> dict:
    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        scan = next((scan for scan in session['scans'] if scan['id'] == scan_id), None)
        if not scan:
            raise HTTPException(404, 'Source page not found.')
        require_resolved_marks(session, scan)
        scan['reviewed'] = True
        changed(session)
        return present(session)


@router.post('/{sid}/export')
def export_document(sid: str, body: ExportBody) -> dict:
    from backend.docx_engine import accept_export_revisions

    with LOCK:
        session = load(sid)
        require_revision(session, body.revision)
        if PENDING_CAPTURES.get(sid, 0):
            raise HTTPException(409, 'A source page is still being read. Wait for capture to finish before exporting.')
        if session['currentHash'] != body.currentHash:
            raise HTTPException(409, 'The current draft changed. Review the new text before exporting.')
        path = document_dir(sid)
        current = (path / 'current.docx').read_bytes()
        if sha256(current).hexdigest() != body.currentHash:
            raise HTTPException(409, 'The current file changed outside this review. Upload it again.')
        refresh(session, current)
        for scan in session['scans']:
            require_resolved_marks(session, scan)
        if any(not scan['reviewed'] for scan in session['scans']):
            raise ValueError('Inspect every source page for missed marks before exporting.')
        if any(edit['decision'] is None for edit in session['edits']):
            raise ValueError('Choose which version to keep for every correction before exporting.')
        selected = [edit for edit in session['edits']
                    if edit['decision'] == 'paper' and edit['status'] != 'already_applied']
        if not selected:
            raise ValueError('Choose at least one paper correction that changes the current draft.')
        if any(not edit['confirmed'] or edit['status'] in ('missing', 'protected', 'overlap')
               for edit in selected):
            raise ValueError('Resolve the blocked corrections and verify their readings before exporting.')
        if session['export']:
            return present(session)
        operations = [dict(paragraphId=edit['currentParagraphId'], start=edit['currentStart'],
                           end=edit['currentEnd'], replacement=edit['replacement'],
                           author=f"PaperPatch / {edit['reviewer']}") for edit in selected]
        output, verification = export_docx(current, operations)
        clean = accept_export_revisions(output, verification['revisionIds'])
        verification.update(baselineHash=sha256((path / 'baseline.docx').read_bytes()).hexdigest(),
                            cleanHash=sha256(clean).hexdigest())
        clean_inspection = inspect_docx(clean)
        expected = {paragraph['id']: paragraph['text'] for paragraph in session['currentParagraphs']}
        for operation in sorted(operations, key=lambda item: (item['paragraphId'], -item['start'])):
            text = expected[operation['paragraphId']]
            expected[operation['paragraphId']] = text[:operation['start']] + operation['replacement'] + text[operation['end']:]
        if {paragraph['id']: paragraph['text'] for paragraph in clean_inspection['paragraphs']} != expected:
            raise ValueError('The clean copy failed its text-preservation check. No export was saved.')
        export_id = uuid4().hex
        prefix = f'exports/{export_id}'
        folder = path / prefix
        folder.mkdir(parents=True)
        stem = re.sub(r'[^\w .-]', '', Path(session['currentFilename']).stem)[:90] or 'draft'
        filename = stem + ' — tracked.docx'
        (folder / 'tracked.docx').write_bytes(output)
        (folder / 'clean.docx').write_bytes(clean)
        (folder / 'input.docx').write_bytes(current)
        record = dict(id=export_id, createdAt=now(), url=file_url(sid, prefix + '/tracked.docx'),
                      cleanUrl=file_url(sid, prefix + '/clean.docx'), auditUrl=file_url(sid, prefix + '/audit.json'),
                      inputUrl=file_url(sid, prefix + '/input.docx'), filename=filename,
                      changeCount=len(selected), verification=verification)
        audit = dict(version=1, documentId=sid, **record, sourceFilename=session['filename'],
                     baselineUrl=session['baselineUrl'], currentFilename=session['currentFilename'], edits=session['edits'],
                     scans=[{key: value for key, value in scan.items() if not key.startswith('_')}
                            | ({'sha256': scan['_hash']} if scan.get('_hash') else {})
                            for scan in session['scans']],
                     verificationScope='Document structure, intended text changes and unchanged package parts. This does not verify handwriting accuracy or editorial judgment.')
        if session.get('trial'):
            audit['captureCheck'] = session['trial']
            audit['failedCaptures'] = session.get('_captureFailures', [])
        (folder / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
        session['export'] = record
        session['_history'].append(record)
        save(session)
        return present(session)


@router.get('/{sid}/files/{filename:path}')
def document_file(sid: str, filename: str) -> FileResponse:
    root = document_dir(sid).resolve()
    path = (root / filename).resolve()
    if (not path.is_relative_to(root) or not path.is_file() or path.name.startswith('session.')
            or path.suffix.lower() not in ('.docx', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.json', '.bin')):
        raise HTTPException(404, 'File not found.')
    download = path.name if path.suffix in ('.docx', '.json', '.bin') or path.name.startswith('source-upload.') else None
    return FileResponse(path, filename=download)
