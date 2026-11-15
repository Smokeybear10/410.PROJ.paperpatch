from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from backend.merge import _parse_finite_decimal, _proposal_to_typed, approved_patches, reconcile


NEEDS_REVIEW = {'ready', 'conflict', 'invalid', 'paper_conflict'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def checked_readings(session: dict) -> dict:
    proofs = {}
    records = list(session.get('exports', {}).values())
    current_decisions = {
        item['id']: session['reviewDecisions'][item['id']]
        for item in session.get('items', [])
        if item['id'] in session['reviewDecisions'] and 'selectedCandidateId' in item
        and session['reviewFingerprints'].get(item['id']) == fingerprint(item)
    }
    records.append({'reviewedItems': session.get('items', []), 'decisions': current_decisions})
    for record in records:
        for item in record.get('reviewedItems', []):
            choice = record.get('decisions', {}).get(item['id'])
            if choice not in ('paper', 'current'):
                continue
            selected_id = item.get('selectedCandidateId', item['id'])
            for candidate in item.get('candidates') or [item]:
                if choice == 'paper' and candidate['id'] != selected_id:
                    continue
                key = (candidate['id'], candidate.get('text', ''), candidate.get('cropUrl'))
                proofs[key] = 'confirmed' if choice == 'paper' else 'dismissed'
    return proofs


def migrate(session: dict) -> bool:
    changed = False
    for key, default in (('scans', []), ('reviewDecisions', {}), ('reviewFingerprints', {}), ('history', [])):
        if key not in session:
            session[key] = default
            changed = True
    proofs = None
    for proposal in session.get('proposals', []):
        if not proposal.get('candidates'):
            source = proposal.get('source', 'manual')
            candidate = {
                'id': proposal['id'], 'scanId': None, 'boxId': None,
                'text': proposal.get('text', ''),
                'ocrText': proposal.get('text', '') if source == 'ocr' else None,
                'source': source, 'cropUrl': proposal.get('cropUrl'),
                'createdAt': now(), 'label': 'Earlier paper reading' if source == 'ocr' else
                'Illustrative example' if source == 'demo' else 'Manual entry', 'page': None,
            }
            proposal.update(candidates=[candidate], selectedCandidateId=candidate['id'], paperConflict=False)
            changed = True
        for candidate in proposal['candidates']:
            if (candidate.get('source') == 'manual' and not candidate.get('text', '').strip()
                    and candidate.get('transcriptionState') in (None, 'unverified')
                    and (candidate.get('scanId') or candidate.get('ocrText') is not None)):
                candidate['source'] = 'ocr'
                if proposal['selectedCandidateId'] == candidate['id']:
                    proposal['source'] = 'ocr'
                changed = True
            if 'transcriptionState' in candidate:
                continue
            source = candidate.get('source', 'manual')
            if source == 'demo':
                state = 'confirmed'
            elif source == 'manual':
                state = 'corrected' if candidate.get('text', '').strip() else 'unverified'
            else:
                if proofs is None:
                    proofs = checked_readings(session)
                key = (candidate['id'], candidate.get('text', ''), candidate.get('cropUrl'))
                state = proofs.get(key, 'unverified')
            candidate['transcriptionState'] = state
            changed = True
    for scan in session['scans']:
        if '_attempt' not in scan:
            scan['_attempt'] = uuid4().hex
            changed = True
        if 'reviewedAt' not in scan:
            scan['reviewedAt'] = None
            changed = True
        if scan['reviewedAt'] and scan.get('_reviewedAttempt') != scan['_attempt']:
            scan['reviewedAt'] = None
            changed = True
    if session.get('schemaVersion') != 3:
        session['schemaVersion'] = 3
        changed = True
    return changed


def value_key(cell: dict | None) -> tuple:
    if not cell:
        return ('blank', '')
    kind, value = cell.get('kind', 'blank'), cell.get('value', '')
    if kind == 'blank':
        return ('blank', '')
    if kind == 'number':
        number = _parse_finite_decimal(str(value))
        if number is not None:
            value = format(number, 'f')
            if '.' in value:
                value = value.rstrip('0').rstrip('.')
            if number == 0:
                value = '0'
    return kind, value


def reading_key(session: dict, proposal: dict, text: str) -> tuple:
    row = next((row for row in session['base']['rows'] if row['id'] == proposal['rowId']), None)
    baseline = row['cells'].get(proposal['field'], {}) if row else {}
    return value_key(_proposal_to_typed(text, baseline.get('kind', 'text')))


def invalidate(session: dict, proposal_id: str) -> None:
    session['reviewDecisions'].pop(proposal_id, None)
    session['reviewFingerprints'].pop(proposal_id, None)


def select_candidate(proposal: dict, candidate: dict) -> None:
    proposal.update(text=candidate['text'], cropUrl=candidate['cropUrl'], source=candidate['source'],
                    selectedCandidateId=candidate['id'], paperConflict=False)


def add_candidate(session: dict, row_id: str, field: str, candidate: dict, select: bool = False) -> dict:
    proposal = next((item for item in session['proposals']
                     if item['rowId'] == row_id and item['field'] == field), None)
    if proposal is None:
        proposal = dict(id=uuid4().hex, rowId=row_id, field=field, text=candidate['text'],
                        source=candidate['source'], cropUrl=candidate['cropUrl'], candidates=[candidate],
                        selectedCandidateId=candidate['id'], paperConflict=False)
        session['proposals'].append(proposal)
        return proposal
    proposal['candidates'].append(candidate)
    if select:
        select_candidate(proposal, candidate)
        invalidate(session, proposal['id'])
        return proposal
    if reading_key(session, proposal, candidate['text']) != reading_key(session, proposal, proposal['text']):
        proposal.update(text=candidate['text'], cropUrl=candidate['cropUrl'], source=candidate['source'],
                        selectedCandidateId=None, paperConflict=True)
        invalidate(session, proposal['id'])
    return proposal


def fingerprint(item: dict) -> str:
    def cell_state(cell: dict) -> dict:
        return {'value': value_key(cell), 'protected': bool(cell.get('protected'))}

    state = {
        'baseline': cell_state(item['baseline']), 'current': cell_state(item['current']),
        'selectedCandidateId': item['selectedCandidateId'], 'proposed': value_key(item['proposed']),
        'text': item['text'], 'paperConflict': item['paperConflict'], 'status': item['status'],
    }
    return sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refresh(session: dict) -> None:
    migrate(session)
    if session['base'] is None:
        session['items'] = []
        session['reviewDecisions'] = {}
        session['reviewFingerprints'] = {}
        return
    items = reconcile(session['base'], session['current'], session['proposals'], session['receipts'])
    proposals = {proposal['id']: proposal for proposal in session['proposals']}
    for item in items:
        proposal = proposals[item['id']]
        item.update(candidates=proposal['candidates'], selectedCandidateId=proposal['selectedCandidateId'],
                    paperConflict=proposal['paperConflict'])
        if item['paperConflict'] and item['status'] != 'protected':
            item['status'] = 'paper_conflict'
            item['reason'] = 'Paper sources disagree. Select a reading or transcribe the value before using paper, or keep Excel.'
        elif item.get('cropUrl') and not item['text'].strip() and item['status'] == 'unchanged':
            item['status'] = 'invalid'
            item['reason'] = 'Writing was detected but has no transcription. Enter its value or explicitly keep Excel.'
        selected = next((candidate for candidate in item['candidates']
                         if candidate['id'] == item['selectedCandidateId']), None)
        item['requiresReadingCheck'] = bool(selected and selected['source'] == 'ocr'
                                           and selected['transcriptionState'] == 'unverified'
                                           and item['status'] != 'protected')
    session['items'] = items
    index = {item['id']: item for item in items}
    for proposal_id in list(session['reviewDecisions']):
        item = index.get(proposal_id)
        choice = session['reviewDecisions'][proposal_id]
        if (item is None or session['reviewFingerprints'].get(proposal_id) != fingerprint(item)
                or choice == 'paper' and item['status'] not in ('ready', 'conflict')):
            invalidate(session, proposal_id)


def record_decision(session: dict, proposal_id: str, choice: str) -> None:
    item = next((item for item in session['items'] if item['id'] == proposal_id), None)
    if item is None:
        raise ValueError('This correction is no longer available. Refresh the review.')
    if choice not in ('paper', 'current'):
        raise ValueError('Choose paper or the current Excel value.')
    if choice == 'paper' and item['status'] not in ('ready', 'conflict'):
        if item['status'] == 'paper_conflict':
            raise ValueError('Select one of the competing paper readings before approving it.')
        raise ValueError('This paper reading cannot be applied. Check its transcription and protection status.')
    for candidate in item['candidates']:
        if choice == 'paper' and candidate['id'] == item['selectedCandidateId']:
            if candidate['transcriptionState'] in ('unverified', 'dismissed'):
                candidate['transcriptionState'] = 'confirmed'
        elif choice == 'current' and candidate['transcriptionState'] == 'unverified':
            candidate['transcriptionState'] = 'dismissed'
    item['requiresReadingCheck'] = False
    session['reviewDecisions'][proposal_id] = choice
    session['reviewFingerprints'][proposal_id] = fingerprint(item)


def verify_reading(session: dict, proposal_id: str, candidate_id: str) -> None:
    item = next((item for item in session['items'] if item['id'] == proposal_id), None)
    if item is None:
        raise ValueError('This correction is no longer available. Refresh the review.')
    if item['paperConflict'] or item['selectedCandidateId'] != candidate_id:
        raise ValueError('Select this paper reading before confirming its transcription.')
    if item['status'] in ('invalid', 'protected') or not item['text'].strip() or item['proposed'] is None:
        raise ValueError('Enter a usable transcription before confirming what the paper says.')
    candidate = next(candidate for candidate in item['candidates'] if candidate['id'] == candidate_id)
    if candidate['transcriptionState'] in ('unverified', 'dismissed'):
        candidate['transcriptionState'] = 'confirmed'
    item['requiresReadingCheck'] = False


def preview(session: dict) -> dict:
    refresh(session)
    decisions = session['reviewDecisions']
    patches = approved_patches(session['items'], decisions)
    targets = {(patch['rowId'], patch['field']) for patch in patches}
    paper_changes = []
    for item in session['items']:
        if decisions.get(item['id']) != 'paper' or item['status'] not in ('ready', 'conflict'):
            continue
        candidate = next((candidate for candidate in item['candidates']
                          if candidate['id'] == item['selectedCandidateId']), None)
        paper_changes.append({
            'proposalId': item['id'], 'rowId': item['rowId'], 'rowLabel': item['rowLabel'],
            'field': item['field'], 'address': item['current']['address'], 'before': item['current'],
            'after': item['proposed'], 'source': candidate['label'] if candidate else item['source'],
        })
    digital_changes = []
    digital_count = 0
    formula_changes = []
    formula_count = 0
    baseline_rows = {row['id']: row for row in session['base']['rows']} if session['base'] else {}
    for row in session['current']['rows'] if session['current'] else []:
        before_row = baseline_rows.get(row['id'], {'cells': {}})
        for field in dict.fromkeys([*before_row['cells'], *row['cells']]):
            if (row['id'], field) in targets:
                continue
            before = before_row['cells'].get(field)
            after = row['cells'].get(field)
            if value_key(before) == value_key(after):
                continue
            formula_changed = (before or {}).get('kind') == 'formula' or (after or {}).get('kind') == 'formula'
            if formula_changed:
                formula_count += 1
                changes = formula_changes
            else:
                digital_count += 1
                changes = digital_changes
            if len(changes) >= 100:
                continue
            blank = {'kind': 'blank', 'value': '', 'address': '', 'protected': False}
            changes.append({
                'rowId': row['id'], 'rowLabel': row['label'], 'field': field,
                'address': (after or before or blank)['address'],
                'before': before or blank, 'after': after or blank,
            })
    sources = {scan['id'] for scan in session['scans'] if scan['status'] == 'ready'}
    sources.update(candidate['scanId'] or candidate['id']
                   for proposal in session['proposals'] for candidate in proposal['candidates'])
    pending = sum(item['requiresReadingCheck'] or item['status'] in NEEDS_REVIEW and item['id'] not in decisions
                  for item in session['items'])
    processing = any(scan['status'] in ('queued', 'processing') for scan in session['scans'])
    unchecked = sum(scan['status'] == 'ready' and not scan['reviewedAt'] for scan in session['scans'])
    return {
        'revision': session['revision'], 'currentHash': session['current']['sha256'] if session['current'] else None,
        'canExport': bool(paper_changes) and pending == 0 and not processing and unchecked == 0,
        'pendingCount': pending, 'sourceCount': len(sources),
        'uncheckedPageCount': unchecked,
        'failedSourceCount': sum(scan['status'] == 'failed' for scan in session['scans']),
        'protectedCount': sum(item['status'] == 'protected' for item in session['items']),
        'paperChanges': paper_changes, 'digitalChanges': digital_changes, 'preservedDigitalCount': digital_count,
        'formulaChanges': formula_changes, 'retainedFormulaChangeCount': formula_count,
    }
