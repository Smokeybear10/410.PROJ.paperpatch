from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4
import json
import logging
import re
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from backend.workbook import inspect_workbook, read_table, patch_workbook
from backend.merge import reconcile, approved_patches
from backend.paper import generate_paper, scan_photo
from backend import review
from backend.verification import verify_export
from backend.documents import router as documents_router

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / '.data' / 'sessions'
DATA.mkdir(parents=True, exist_ok=True)
LOCK = RLock()
WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='paperpatch-capture')
MAX_PENDING_SCANS = 24
ACTIVE_JOBS: set[tuple[str, str]] = set()
FUTURES: dict[tuple[str, str], Future] = {}
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    recover_interrupted_jobs()
    yield


app = FastAPI(title='PaperPatch', docs_url=None, redoc_url=None, lifespan=lifespan)

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sheet: str = Field(min_length=1, max_length=128)
    idColumn: str = Field(min_length=1, max_length=128)
    editableColumns: list[str] = Field(min_length=1, max_length=2)

class Correction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=250)
    revision: int | None = Field(default=None, ge=0)
    candidateId: str | None = Field(default=None, min_length=1, max_length=64)

class ManualCorrection(Correction):
    rowId: str = Field(min_length=1, max_length=500)
    field: str = Field(min_length=1, max_length=128)
    scanId: str | None = Field(default=None, max_length=64)
    boxId: str | None = Field(default=None, max_length=128)

class CandidateSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidateId: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=0)

class PageReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)

class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposalId: str = Field(min_length=1, max_length=64)
    choice: Literal['paper', 'current']
    revision: int = Field(ge=0)

class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currentHash: str = Field(pattern=r'^[a-f0-9]{64}$')
    revision: int = Field(ge=0)
    decisions: dict[str, Literal['paper', 'current']] = Field(default_factory=dict)

@app.exception_handler(ValueError)
def invalid_input(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={'detail': str(exc)})

@app.middleware('http')
async def local_boundary(request: Request, call_next):
    if request.headers.get('host', '').split(':')[0] not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse(status_code=400, content={'detail': 'Use the local PaperPatch address.'})
    origin = request.headers.get('origin')
    if origin and origin not in ('http://localhost:7171', 'http://127.0.0.1:7171', 'http://localhost:5173', 'http://127.0.0.1:5173'):
        return JSONResponse(status_code=403, content={'detail': 'This request did not come from PaperPatch.'})
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
    return response

def session_dir(sid: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', sid):
        raise HTTPException(404, 'Session not found.')
    path = DATA / sid
    if not (path / 'session.json').is_file():
        raise HTTPException(404, 'Session not found. Upload a workbook to start again.')
    return path

def load(sid: str) -> dict:
    session = json.loads((session_dir(sid) / 'session.json').read_text())
    migrated = review.migrate(session)
    migrated = migrate_history(session) or migrated
    if migrated:
        refresh(session)
        save(session)
    return session

def save(session: dict) -> None:
    path = DATA / session['id']
    path.mkdir(exist_ok=True)
    temporary = path / 'session.pending.json'
    temporary.write_text(json.dumps(session, ensure_ascii=False))
    temporary.replace(path / 'session.json')

def refresh(session: dict) -> None:
    review.refresh(session)

def history_entry(record: dict) -> dict:
    entry = {key: record[key] for key in ('id', 'createdAt', 'count', 'filename', 'url', 'auditUrl')}
    for key in ('verification', 'inputWorkbookUrl'):
        if key in record:
            entry[key] = record[key]
    return entry

def migrate_history(session: dict) -> bool:
    migrated = False
    known = {record['id'] for record in session['history']}
    path = DATA / session['id']
    for key, record in session.get('exports', {}).items():
        if not record.get('id'):
            export_path = path / record['filename']
            timestamp = export_path.stat().st_mtime if export_path.exists() else 0
            record.update(id=key[:16], createdAt=datetime.fromtimestamp(timestamp, timezone.utc).isoformat())
            audit_name = f"audit-{record['id']}.json"
            record['auditUrl'] = f"/api/sessions/{session['id']}/files/{audit_name}"
            if not (path / audit_name).exists():
                (path / audit_name).write_text(json.dumps({'version': 1, 'legacy': True, **record}, ensure_ascii=False))
            migrated = True
        if record['id'] not in known:
            session['history'].append(history_entry(record))
            known.add(record['id'])
            migrated = True
        if session.get('export') and session['export'].get('filename') == record['filename']:
            session['export'] = record
    return migrated

def changed(session: dict) -> None:
    session['revision'] += 1
    session['export'] = None
    refresh(session)
    save(session)

def present(session: dict) -> dict:
    public = json.loads(json.dumps({key: value for key, value in session.items()
                                   if key not in ('receipts', 'scanHashes', 'manifestInternal', 'exports',
                                                  'reviewFingerprints', 'schemaVersion')}))
    public['scans'] = [{key: value for key, value in scan.items() if not key.startswith('_')}
                       for scan in public['scans']]
    return public

def upload_bytes(file: UploadFile, limit: int, workbook: bool = False) -> bytes:
    if workbook and not (file.filename or '').lower().endswith('.xlsx'):
        raise ValueError('Choose an ordinary .xlsx workbook. Older or macro-enabled formats are not supported.')
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'File is too large. Maximum size is {limit // (1024 * 1024)} MB.')
    if not data:
        raise ValueError('This file is empty.')
    return data

def filename_of(file: UploadFile) -> str:
    return Path((file.filename or 'workbook.xlsx').replace('\\', '/')).name[:180]

def image_extension(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError('Photo exceeds the 25 megapixel limit.')
            extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(image.format)
            if not extension:
                raise ValueError('Choose a JPEG, PNG, or WebP photograph.')
            image.verify()
            return extension
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Could not read this photo. Upload a JPEG, PNG, or WebP under 25 megapixels.') from exc

def new_session(data: bytes, filename: str, demo: bool = False) -> dict:
    inspection = inspect_workbook(data)
    sid = uuid4().hex
    path = DATA / sid
    path.mkdir()
    (path / 'baseline.xlsx').write_bytes(data)
    (path / 'current.xlsx').write_bytes(data)
    session = dict(id=sid, filename=filename, currentFilename=filename, inspection=inspection,
                   config=None, base=None, current=None, manifest=None, manifestInternal=None,
                   proposals=[], items=[], warnings=[], receipts=[], scanHashes={}, exports={},
                   revision=0, demo=demo, export=None, createdAt=review.now())
    review.migrate(session)
    save(session)
    return session

def configure(session: dict, config: Config) -> None:
    if session['manifest']:
        raise ValueError('This print run is fixed. Start a new workbook to change its fields.')
    path = session_dir(session['id'])
    table = read_table((path / 'baseline.xlsx').read_bytes(), config.sheet, config.idColumn, config.editableColumns)
    if not table['rows']:
        raise ValueError('The selected table has no data rows.')
    if not any(not row['cells'][field]['protected'] for row in table['rows'] for field in config.editableColumns):
        raise ValueError('The selected fields contain only protected cells. Choose an editable number or plain-text field.')
    manifest = generate_paper(table, session['id'], path / 'paper')
    prefix = f"/api/sessions/{session['id']}/files/paper/"
    public = json.loads(json.dumps(manifest))
    public['pdfUrl'] = prefix + 'paper.pdf'
    for page in public['pages']:
        preview = page.get('previewUrl') or page.get('previewPath') or f"page-{page['number']}.png"
        page['previewUrl'] = prefix + Path(preview).name
    session.update(config=config.model_dump(), base=table, current=table, manifestInternal=manifest, manifest=public)
    changed(session)

def check_revision(session: dict, revision: int) -> None:
    if revision != session['revision']:
        raise HTTPException(409, 'The workbook or paper readings changed. Refresh the review and try again.')

def scan_record(session: dict, scan_id: str) -> dict:
    scan = next((scan for scan in session['scans'] if scan['id'] == scan_id), None)
    if scan is None:
        raise HTTPException(404, 'Paper source not found.')
    return scan

def recover_interrupted_jobs() -> None:
    with LOCK:
        for session_path in DATA.glob('*/session.json'):
            try:
                session = load(session_path.parent.name)
            except (ValueError, OSError):
                logger.exception('Could not restore a stored session')
                continue
            interrupted = False
            for scan in session['scans']:
                if scan['status'] not in ('queued', 'processing') or (session['id'], scan['id']) in ACTIVE_JOBS:
                    continue
                scan.update(status='failed', stage='Interrupted', finishedAt=review.now(),
                            error='PaperPatch restarted before this page finished. Retry this source to read the saved original.')
                interrupted = True
            if interrupted:
                changed(session)

def process_scan(sid: str, scan_id: str) -> None:
    try:
        with LOCK:
            session = load(sid)
            scan = scan_record(session, scan_id)
            scan.update(status='processing', stage='Aligning page')
            save(session)
            original_path = session_dir(sid) / scan['_originalName']
            attempt = scan['_attempt']
            manifest = session['manifestInternal']
        output_path = session_dir(sid) / 'scans' / scan_id / attempt

        def progress(stage: str, done: int, total: int) -> None:
            with LOCK:
                latest = load(sid)
                current_scan = scan_record(latest, scan_id)
                current_scan.update(stage=stage, progress={'done': max(0, done), 'total': max(0, total)})
                save(latest)

        result = scan_photo(original_path.read_bytes(), manifest, output_path, sid, progress=progress)
        with LOCK:
            session = load(sid)
            scan = scan_record(session, scan_id)
            prefix = f'/api/sessions/{sid}/files/scans/{scan_id}/{attempt}/'
            boxes = []
            for box in result.get('boxes', []):
                public_box = {key: value for key, value in box.items() if key != 'cropPath'}
                public_box['cropUrl'] = prefix + Path(box['cropPath']).name if box.get('cropPath') else None
                boxes.append(public_box)
            scan.update(status='ready', stage='Ready to review', finishedAt=review.now(), page=result['page'],
                        alignedUrl=prefix + Path(result['alignedPath']).name,
                        readingCount=len(result['proposals']), warnings=result.get('warnings', []), error=None,
                        quality=result.get('quality', {}), boxes=boxes)
            if not result['proposals']:
                scan['warnings'] = [*scan['warnings'], 'No writing was detected. Inspect the page for any missed marks.']
            for proposal in result['proposals']:
                candidate = dict(id=uuid4().hex, scanId=scan_id, boxId=proposal.get('boxId'),
                                 text=proposal['text'], ocrText=proposal.get('ocrText', proposal['text']),
                                 source='ocr', transcriptionState='unverified',
                                 cropUrl=prefix + Path(proposal['cropUrl']).name if proposal.get('cropUrl') else None,
                                 createdAt=review.now(), label=scan['filename'], page=result['page'],
                                 recognitionState=proposal.get('recognitionState'))
                review.add_candidate(session, proposal['rowId'], proposal['field'], candidate)
            session['warnings'] = scan['warnings']
            session['lastScanUrl'] = scan['alignedUrl']
            changed(session)
    except Exception as exc:
        logger.exception('Paper source processing failed')
        with LOCK:
            session = load(sid)
            scan = scan_record(session, scan_id)
            message = str(exc) if isinstance(exc, ValueError) else 'This page could not be processed. Retry the saved original or upload another photograph.'
            scan.update(status='failed', stage='Needs attention', error=message, finishedAt=review.now())
            changed(session)
    finally:
        with LOCK:
            ACTIVE_JOBS.discard((sid, scan_id))
            FUTURES.pop((sid, scan_id), None)

def submit_job(sid: str, scan_id: str) -> Future:
    ACTIVE_JOBS.add((sid, scan_id))
    future = WORKER.submit(process_scan, sid, scan_id)
    FUTURES[(sid, scan_id)] = future
    return future

def queue_scans(sid: str, files: list[UploadFile]) -> tuple[dict, list[Future], list[str]]:
    if not 1 <= len(files) <= 12:
        raise ValueError('Choose between 1 and 12 page photographs per upload.')
    incoming = []
    for file in files:
        data = upload_bytes(file, 20 * 1024 * 1024)
        incoming.append((data, filename_of(file), image_extension(data), sha256(data).hexdigest()))
    with LOCK:
        session = load(sid)
        if not session['manifestInternal']:
            raise ValueError('Generate the paper sheet before uploading its photograph.')
        unique = {digest for _, _, _, digest in incoming if digest not in session['scanHashes']}
        if len(ACTIVE_JOBS) + len(unique) > MAX_PENDING_SCANS:
            raise HTTPException(429, 'The reading queue is full. Wait for a page to finish before adding more.')
        added = []
        requested = []
        futures = []
        duplicates = False
        for data, filename, extension, digest in incoming:
            if digest in session['scanHashes']:
                duplicates = True
                existing_id = session['scanHashes'][digest]
                requested.append(existing_id)
                if (sid, existing_id) in FUTURES:
                    futures.append(FUTURES[(sid, existing_id)])
                continue
            scan_id = uuid4().hex
            original_name = f'scans/{scan_id}/original.{extension}'
            original_path = session_dir(sid) / original_name
            original_path.parent.mkdir(parents=True)
            original_path.write_bytes(data)
            scan = dict(id=scan_id, filename=filename, status='queued', stage='Waiting to read',
                        createdAt=review.now(), finishedAt=None, page=None,
                        originalUrl=f'/api/sessions/{sid}/files/{original_name}', alignedUrl=None,
                        readingCount=0, progress={'done': 0, 'total': 0}, warnings=[], error=None, boxes=[],
                        reviewedAt=None, _originalName=original_name, _attempt=uuid4().hex)
            session['scans'].append(scan)
            session['scanHashes'][digest] = scan_id
            added.append(scan_id)
            requested.append(scan_id)
        session['warnings'] = ['Duplicate photographs were kept as their existing sources. Retry a failed source from the inbox.'] if duplicates else []
        if added:
            changed(session)
        else:
            save(session)
        for scan_id in added:
            futures.append(submit_job(sid, scan_id))
        return present(session), futures, requested

@app.get('/api/health')
def health() -> dict:
    return {'status': 'ok', 'ocr': 'Local PP-OCRv6 (Word)', 'storage': 'local'}

@app.post('/api/sessions')
def create_session(file: UploadFile) -> dict:
    with LOCK:
        return present(new_session(upload_bytes(file, 10 * 1024 * 1024, True), filename_of(file)))

@app.get('/api/sessions/{sid}')
def get_session(sid: str) -> dict:
    with LOCK:
        session = load(sid)
        decisions = session['reviewDecisions'].copy()
        refresh(session)
        if decisions != session['reviewDecisions']:
            save(session)
        return present(session)

@app.post('/api/sessions/{sid}/config')
def config_session(sid: str, config: Config) -> dict:
    with LOCK:
        session = load(sid)
        configure(session, config)
        return present(session)

@app.post('/api/sessions/{sid}/current')
def upload_current(sid: str, file: UploadFile) -> dict:
    data = upload_bytes(file, 10 * 1024 * 1024, True)
    with LOCK:
        session = load(sid)
        config = session['config']
        if not config:
            raise ValueError('Choose your table and create the print run first.')
        table = read_table(data, config['sheet'], config['idColumn'], config['editableColumns'])
        reconcile(session['base'], table, session['proposals'], session['receipts'])
        (session_dir(sid) / 'current.xlsx').write_bytes(data)
        session.update(current=table, currentFilename=filename_of(file))
        changed(session)
        return present(session)

@app.post('/api/sessions/{sid}/scans')
def upload_scans(sid: str, files: list[UploadFile] | None = File(default=None),
                 array_files: list[UploadFile] | None = File(default=None, alias='files[]')) -> dict:
    session, _, _ = queue_scans(sid, [*(files or []), *(array_files or [])])
    return session

@app.post('/api/sessions/{sid}/scan')
def upload_scan(sid: str, file: UploadFile) -> dict:
    _, futures, requested = queue_scans(sid, [file])
    for future in futures:
        future.result()
    with LOCK:
        session = load(sid)
        failed = next((scan for scan in session['scans']
                       if scan['id'] in requested and scan['status'] == 'failed'), None)
        if failed:
            raise ValueError(failed['error'])
        return present(session)

@app.post('/api/sessions/{sid}/scans/{scan_id}/retry')
def retry_scan(sid: str, scan_id: str) -> dict:
    with LOCK:
        session = load(sid)
        scan = scan_record(session, scan_id)
        if scan['status'] in ('queued', 'processing'):
            return present(session)
        if not scan.get('_originalName'):
            raise ValueError('This illustrative source has no original photograph to reprocess.')
        if len(ACTIVE_JOBS) >= MAX_PENDING_SCANS:
            raise HTTPException(429, 'The reading queue is full. Wait for a page to finish before retrying.')
        scan.update(status='queued', stage='Waiting to read', finishedAt=None, error=None,
                    progress={'done': 0, 'total': 0}, _attempt=uuid4().hex,
                    reviewedAt=None, _reviewedAttempt=None)
        changed(session)
        submit_job(sid, scan_id)
        return present(session)

@app.post('/api/sessions/{sid}/scans/{scan_id}/review')
def review_page(sid: str, scan_id: str, body: PageReview) -> dict:
    with LOCK:
        session = load(sid)
        check_revision(session, body.revision)
        scan = scan_record(session, scan_id)
        if scan['status'] != 'ready':
            raise ValueError('Wait for this page to finish processing before checking it for missed writing.')
        if scan['reviewedAt'] and scan.get('_reviewedAttempt') == scan['_attempt']:
            return present(session)
        scan.update(reviewedAt=review.now(), _reviewedAttempt=scan['_attempt'])
        changed(session)
        return present(session)

@app.patch('/api/sessions/{sid}/proposals/{proposal_id}')
def edit_proposal(sid: str, proposal_id: str, body: Correction) -> dict:
    with LOCK:
        session = load(sid)
        if body.revision is not None:
            check_revision(session, body.revision)
        proposal = next((p for p in session['proposals'] if p['id'] == proposal_id), None)
        if proposal is None:
            raise HTTPException(404, 'Correction not found.')
        source_id = body.candidateId or proposal['selectedCandidateId']
        original = next((candidate for candidate in proposal['candidates'] if candidate['id'] == source_id), None)
        if body.candidateId and original is None:
            raise ValueError('This source does not belong to the selected correction.')
        original = original or proposal['candidates'][-1]
        candidate = {**original, 'id': uuid4().hex, 'text': body.text, 'source': 'manual',
                     'transcriptionState': 'corrected', 'createdAt': review.now()}
        review.add_candidate(session, proposal['rowId'], proposal['field'], candidate, select=True)
        changed(session)
        return present(session)

@app.post('/api/sessions/{sid}/proposals')
def add_proposal(sid: str, body: ManualCorrection) -> dict:
    with LOCK:
        session = load(sid)
        if body.revision is not None:
            check_revision(session, body.revision)
        if not session['base']:
            raise ValueError('Choose a table first.')
        if body.field not in session['config']['editableColumns'] or body.rowId not in {r['id'] for r in session['base']['rows']}:
            raise ValueError('Choose a row and editable field from the printed table.')
        candidate = dict(id=uuid4().hex, scanId=None, boxId=None, text=body.text, ocrText=None,
                         source='manual', transcriptionState='corrected', cropUrl=None,
                         createdAt=review.now(), label='Manual entry', page=None)
        if body.scanId or body.boxId:
            if not body.scanId or not body.boxId:
                raise ValueError('Choose both the paper source and its correction box.')
            scan = scan_record(session, body.scanId)
            box = next((box for box in scan.get('boxes', []) if box['boxId'] == body.boxId), None)
            if not box or box['rowId'] != body.rowId or box['field'] != body.field:
                raise ValueError('This box does not belong to the selected row and field.')
            source = next((source for proposal in session['proposals'] for source in proposal['candidates']
                           if source['scanId'] == scan['id'] and source.get('boxId') == box['boxId']
                           and source.get('cropUrl') == box.get('cropUrl')), None)
            candidate.update(scanId=scan['id'], boxId=box['boxId'], cropUrl=box.get('cropUrl'),
                             ocrText=source.get('ocrText') if source else box.get('ocrText'),
                             label=scan['filename'], page=scan['page'])
        review.add_candidate(session, body.rowId, body.field, candidate, select=True)
        changed(session)
        return present(session)

@app.post('/api/sessions/{sid}/proposals/{proposal_id}/candidate')
def choose_candidate(sid: str, proposal_id: str, body: CandidateSelection) -> dict:
    with LOCK:
        session = load(sid)
        check_revision(session, body.revision)
        proposal = next((proposal for proposal in session['proposals'] if proposal['id'] == proposal_id), None)
        if proposal is None:
            raise HTTPException(404, 'Correction not found.')
        candidate = next((candidate for candidate in proposal['candidates'] if candidate['id'] == body.candidateId), None)
        if candidate is None:
            raise ValueError('This reading does not belong to the selected correction.')
        review.select_candidate(proposal, candidate)
        review.invalidate(session, proposal_id)
        changed(session)
        return present(session)

@app.post('/api/sessions/{sid}/decisions')
def decide_proposal(sid: str, body: DecisionRequest) -> dict:
    with LOCK:
        session = load(sid)
        check_revision(session, body.revision)
        refresh(session)
        review.record_decision(session, body.proposalId, body.choice)
        changed(session)
        return present(session)

@app.post('/api/sessions/{sid}/proposals/{proposal_id}/verify')
def verify_proposal(sid: str, proposal_id: str, body: CandidateSelection) -> dict:
    with LOCK:
        session = load(sid)
        check_revision(session, body.revision)
        refresh(session)
        review.verify_reading(session, proposal_id, body.candidateId)
        changed(session)
        return present(session)

@app.get('/api/sessions/{sid}/preview')
def export_preview(sid: str) -> dict:
    with LOCK:
        session = load(sid)
        if not session['current']:
            raise ValueError('Configure the workbook first.')
        return review.preview(session)

@app.post('/api/sessions/{sid}/export')
def export_session(sid: str, body: ExportRequest) -> dict:
    with LOCK:
        session = load(sid)
        if not session['current']:
            raise ValueError('Configure the workbook first.')
        fingerprint = sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
        if fingerprint in session['exports']:
            session['export'] = session['exports'][fingerprint]
            return present(session)
        if body.revision != session['revision'] or body.currentHash != session['current']['sha256']:
            raise HTTPException(409, 'The workbook or corrections changed. Refresh the review before exporting.')
        if any(scan['status'] in ('queued', 'processing') for scan in session['scans']):
            raise HTTPException(409, 'Wait for all paper sources to finish, then review their readings before exporting.')
        refresh(session)
        for proposal_id, choice in body.decisions.items():
            review.record_decision(session, proposal_id, choice)
        preview = review.preview(session)
        if preview['pendingCount']:
            raise ValueError(f"Review {preview['pendingCount']} remaining corrections before export.")
        if preview['uncheckedPageCount']:
            raise ValueError(f"Check {preview['uncheckedPageCount']} paper pages for missed writing before export.")
        patches = approved_patches(session['items'], session['reviewDecisions'])
        if not patches:
            raise ValueError('No new paper corrections are approved. The current workbook is unchanged.')
        config = session['config']
        path = session_dir(sid)
        data = (path / 'current.xlsx').read_bytes()
        if sha256(data).hexdigest() != body.currentHash:
            raise HTTPException(409, 'The current workbook changed. Review it again.')
        output = patch_workbook(data, config['sheet'], config['idColumn'], config['editableColumns'], patches)
        try:
            verification = verify_export(data, output, config['sheet'], config['idColumn'], config['editableColumns'], patches)
        except ValueError as exc:
            raise ValueError('Export stopped because the file did not pass preservation checks. Your current workbook is unchanged.') from exc
        export_id = fingerprint[:16]
        filename = f'paperpatch-{export_id}.xlsx'
        input_name = f'input-{export_id}.xlsx'
        audit_name = f'audit-{export_id}.json'
        (path / input_name).write_bytes(data)
        (path / filename).write_bytes(output)
        record = dict(id=export_id, createdAt=review.now(), url=f'/api/sessions/{sid}/files/{filename}',
                      auditUrl=f'/api/sessions/{sid}/files/{audit_name}', filename=filename, patches=patches,
                      count=len(patches), currentHash=body.currentHash, outputHash=sha256(output).hexdigest(),
                      verification=verification, inputWorkbookUrl=f'/api/sessions/{sid}/files/{input_name}')
        record['reviewedItems'] = json.loads(json.dumps(session['items']))
        record['decisions'] = session['reviewDecisions'].copy()
        audit = dict(version=4, id=export_id, createdAt=record['createdAt'], sessionId=sid,
                     baselineHash=session['base']['sha256'], currentHash=body.currentHash,
                     outputHash=record['outputHash'], filename=filename, revision=body.revision,
                     decisions=record['decisions'], paperChanges=preview['paperChanges'],
                     digitalChanges=preview['digitalChanges'], preservedDigitalCount=preview['preservedDigitalCount'],
                     formulaChanges=preview['formulaChanges'], retainedFormulaChangeCount=preview['retainedFormulaChangeCount'],
                     verification=verification, inputWorkbookUrl=record['inputWorkbookUrl'],
                     uncheckedPageCount=preview['uncheckedPageCount'],
                     reviewedItems=record['reviewedItems'], sources=present(session)['scans'], warnings=session['warnings'])
        (path / audit_name).write_text(json.dumps(audit, ensure_ascii=False, indent=2))
        session['receipts'].extend(patches)
        current_path = path / 'current.pending.xlsx'
        current_path.write_bytes(output)
        current_path.replace(path / 'current.xlsx')
        session['current'] = read_table(output, config['sheet'], config['idColumn'], config['editableColumns'])
        session['currentFilename'] = filename
        session['revision'] += 1
        refresh(session)
        session['exports'][fingerprint] = record
        session['history'].append(history_entry(record))
        session['export'] = record
        save(session)
        return present(session)

@app.get('/api/sessions/{sid}/exports/{export_id}/receipt')
def export_receipt(sid: str, export_id: str) -> dict:
    with LOCK:
        session = load(sid)
        if not re.fullmatch(r'[a-f0-9]{12,64}', export_id) or not any(record['id'] == export_id for record in session['history']):
            raise HTTPException(404, 'Export receipt not found.')
        path = session_dir(sid) / f'audit-{export_id}.json'
        if not path.is_file():
            raise HTTPException(404, 'This earlier export has no saved receipt.')
        return json.loads(path.read_text())

@app.get('/api/sessions/{sid}/files/{filename:path}')
def session_file(sid: str, filename: str) -> FileResponse:
    root = session_dir(sid)
    path = (root / filename).resolve()
    audit = path.parent == root.resolve() and bool(re.fullmatch(r'audit-[a-f0-9]{12,64}\.json', path.name))
    if not path.is_relative_to(root.resolve()) or not path.is_file() or (path.suffix not in ('.xlsx', '.png', '.jpg', '.jpeg', '.webp', '.pdf') and not audit):
        raise HTTPException(404, 'File not found.')
    return FileResponse(path, filename=path.name if path.suffix in ('.xlsx', '.pdf', '.json') else None)

@app.post('/api/paper-trial')
def paper_trial() -> dict:
    from backend.demo import make_demo_workbooks
    with LOCK:
        baseline, latest = make_demo_workbooks()
        session = new_session(baseline, 'Field-equipment.xlsx')
        configure(session, Config(sheet='Equipment', idColumn='Asset ID', editableColumns=['Quantity', 'Location']))
        (session_dir(session['id']) / 'practice-latest.xlsx').write_bytes(latest)
        prefix = f"/api/sessions/{session['id']}/files/"
        session.update(practice=True, practiceResources={'baselineUrl': prefix + 'baseline.xlsx',
                                                        'latestUrl': prefix + 'practice-latest.xlsx'})
        save(session)
        return present(session)

@app.post('/api/demo')
def demo_session() -> dict:
    from backend.demo import make_demo_workbooks, demo_crops
    with LOCK:
        baseline, current = make_demo_workbooks()
        session = new_session(baseline, 'Field-equipment.xlsx', demo=True)
        configure(session, Config(sheet='Equipment', idColumn='Asset ID', editableColumns=['Quantity', 'Location']))
        path = session_dir(session['id'])
        (path / 'current.xlsx').write_bytes(current)
        session['current'] = read_table(current, 'Equipment', 'Asset ID', ['Quantity', 'Location'])
        session['currentFilename'] = 'Field-equipment-latest.xlsx'
        candidates = [('EQ-104', 'Quantity', '9'), ('EQ-205', 'Quantity', '22'),
                      ('EQ-407', 'Quantity', '5'), ('EQ-508', 'Quantity', '4'),
                      ('EQ-104', 'Quantity', '8')]
        crops = demo_crops(path / 'demo', candidates)
        manifest = session['manifestInternal']
        page = manifest['pages'][0]
        prefix = f"/api/sessions/{session['id']}/files/demo/"
        font_paths = [Path('/System/Library/Fonts/Supplemental/Georgia Italic.ttf'),
                      Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf')]
        font_path = next((font for font in font_paths if font.exists()), None)
        font = ImageFont.truetype(str(font_path), 52) if font_path else ImageFont.load_default(size=52)
        for label, indices in [('Crew A', range(4)), ('Crew B', range(4, 5))]:
            scan_id = uuid4().hex
            with Image.open(path / 'paper' / page['previewUrl']) as printed:
                image = printed.convert('RGB')
            draw = ImageDraw.Draw(image)
            draw.text((190, 2320), f'{label} — illustrative example; prepared values, not OCR',
                      fill='#536170', font=ImageFont.load_default(size=24))
            values = {('text:' + candidates[i][0], candidates[i][1]): candidates[i][2] for i in indices}
            boxes = []
            for box in page['boxes']:
                x, y, width, height = box['rect']
                pixels = [round(value * manifest['scale']) for value in (x, y, width, height)]
                text_value = values.get((box['rowId'], box['field']), '')
                if text_value:
                    draw.text((pixels[0] + 12, pixels[1] - 3), text_value, fill='#254e71', font=font)
                crop_name = f"{scan_id}-{box['id']}.png"
                image.crop((pixels[0], pixels[1], pixels[0] + pixels[2], pixels[1] + pixels[3])).save(path / 'demo' / crop_name)
                boxes.append(dict(boxId=box['id'], rowId=box['rowId'], field=box['field'],
                                  rect=[x / manifest['pageWidth'], y / manifest['pageHeight'],
                                        width / manifest['pageWidth'], height / manifest['pageHeight']],
                                  cropUrl=prefix + crop_name, inkStatus='written' if text_value else 'blank',
                                  recognitionState='illustrative' if text_value else 'blank', text=text_value))
            page_name = f'{scan_id}-page.png'
            image.save(path / 'demo' / page_name)
            session['scans'].append(dict(id=scan_id, filename=label, status='ready', stage='Illustrative example',
                                         createdAt=review.now(), finishedAt=review.now(), page=1,
                                         originalUrl=prefix + page_name, alignedUrl=prefix + page_name,
                                         readingCount=len(indices), progress={'done': len(indices), 'total': len(indices)},
                                         warnings=['Prepared example values. These sources were not processed by OCR.'],
                                         error=None, boxes=boxes, reviewedAt=None, _attempt=uuid4().hex))
            for i in indices:
                row, field, text_value = candidates[i]
                box = next((box for box in boxes if box['rowId'] == 'text:' + row and box['field'] == field), None)
                candidate = dict(id=uuid4().hex, scanId=scan_id, boxId=box['boxId'] if box else None,
                                 text=text_value, ocrText=None, source='demo', transcriptionState='confirmed',
                                 cropUrl=box['cropUrl'] if box else prefix + crops[i],
                                 createdAt=review.now(), label=label, page=1)
                review.add_candidate(session, 'text:' + row, field, candidate)
        session['warnings'] = []
        changed(session)
        return present(session)

app.include_router(documents_router)

if (ROOT / 'dist').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
