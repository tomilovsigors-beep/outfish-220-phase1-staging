from __future__ import annotations
import csv, hashlib, io, json, os
from collections import defaultdict
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

EXPECTED_DATASET_HASH='a4465570311f9ae08e9733cd2fe9349ca0ece6f3bf6438f07de70fa4281ee3d7'
EXPECTED_PROPOSAL_HASH='e79674e5351c4d8f0d78a91e43994db205900139dd35cebc630eb2afecde34c0'
MASTER_ID='1xBVjjcLYqiQvy2nLtl-tGt8w_7FWefWxFa2Ltthq-7I'
MASTER_SHEET_ID=837961277
SHEET_NAME='MASTER'
EXPECTED_PROPOSAL_ROWS=4825
EXPECTED_IDENTITIES=1003
CANONICAL_MARKER='LEGACY_PRODUCTION_IDENTITY_CANONICAL'
DUPLICATE_MARKER='DUPLICATE_MASTER_220_SKU_CONFLICT'
ALLOWED_FIELDS={
 '220_description','220_selected_options_json','220_grouping_status',
 '220_additional_images','220_weight_kg','220_title_candidate','220_title_candidate_status'
}
PROTECTED_HEADERS={
 'shopify_product_id','shopify_variant_id','shopify_sku','shopify_barcode','220_sku','220_ean','shopify_title','220_title','variant_title','vendor','product_type','shopify_status','shopify_price_reference','shopify_main_image_url','220_main_image_url','220_main_image_status','220_status','220_synced','220_product_xml_enabled','220_stock_feed_enabled','220_price_before_discount','220_price_after_discount','price_override_reason','match_status','validation_status','shopify_updated_at','last_sync','notes','220_category_id','220_category_name','220_properties_json','220_length_m','220_height_m','220_width_m','220_manufacturer_code'
}
PRICE_STOCK_STATUS_HEADERS={'220_status','220_synced','220_product_xml_enabled','220_stock_feed_enabled','220_price_before_discount','220_price_after_discount','price_override_reason','shopify_price_reference','shopify_status','match_status','validation_status'}

def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _json_bytes(o)->bytes: return json.dumps(o,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def _csv_bytes(rows,fields):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=fields); w.writeheader(); w.writerows(rows); return s.getvalue().encode('utf-8-sig')
def _col_letter(n):
    s=''
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s

def _session():
    raw=os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not raw: raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON missing')
    creds=service_account.Credentials.from_service_account_info(json.loads(raw),scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return AuthorizedSession(creds)

def _get_values(sess):
    rng=f"{SHEET_NAME}!A1:ZZ5000"
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{MASTER_ID}/values/{rng}'
    r=sess.get(url,params={'majorDimension':'ROWS','valueRenderOption':'UNFORMATTED_VALUE'},timeout=60)
    if not r.ok: raise RuntimeError(f'Sheets read failed {r.status_code}: {(r.text or "")[:1000]}')
    return (r.json().get('values') or [])

def _hash_matrix(values): return _sha(_json_bytes(values))
def _row_value(row,idx):
    if idx<0 or idx>=len(row): return ''
    v=row[idx]
    if v is None: return ''
    if isinstance(v,bool): return 'TRUE' if v else 'FALSE'
    return str(v)

def _ensure_grid_columns(sess,add_count):
    if add_count<=0: return
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{MASTER_ID}:batchUpdate'
    body={'requests':[{'appendDimension':{'sheetId':MASTER_SHEET_ID,'dimension':'COLUMNS','length':add_count}}]}
    r=sess.post(url,json=body,timeout=60)
    if not r.ok: raise RuntimeError(f'grid expansion failed {r.status_code}: {(r.text or "")[:1500]}')

def _batch_values_atomic(sess,data):
    url=f'https://sheets.googleapis.com/v4/spreadsheets/{MASTER_ID}/values:batchUpdate'
    r=sess.post(url,json={'valueInputOption':'RAW','data':data},timeout=180)
    if not r.ok: raise RuntimeError(f'values batch failed {r.status_code}: {(r.text or "")[:2000]}')
    return r.json()

def _persist_run(db,report,artifacts):
    import psycopg
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute('create table if not exists product_xml_master_write_runs (dataset_hash text primary key, created_at timestamptz not null default now(), status text not null, report jsonb not null, artifacts jsonb not null)')
            payload={k:v.decode('utf-8',errors='replace') for k,v in artifacts.items()}
            cur.execute('insert into product_xml_master_write_runs(dataset_hash,status,report,artifacts) values(%s,%s,%s::jsonb,%s::jsonb) on conflict(dataset_hash) do update set status=excluded.status,report=excluded.report,artifacts=excluded.artifacts,created_at=now()', (report['dataset_hash'],report['status'],json.dumps(report),json.dumps(payload)))
        c.commit()

def run_controlled_master_write(content_artifacts,summary):
    dataset_hash=str(summary.get('dataset_hash') or '')
    if dataset_hash!=EXPECTED_DATASET_HASH: raise RuntimeError('dataset hash mismatch')
    proposal_bytes=content_artifacts.get('master-bulk-write-proposal.csv')
    if not proposal_bytes: raise RuntimeError('proposal artifact missing')
    proposal_hash=_sha(proposal_bytes)
    if proposal_hash!=EXPECTED_PROPOSAL_HASH: raise RuntimeError('proposal source hash mismatch')
    proposal=list(csv.DictReader(io.StringIO(proposal_bytes.decode('utf-8-sig'))))
    if len(proposal)!=EXPECTED_PROPOSAL_ROWS: raise RuntimeError(f'proposal rows {len(proposal)} != {EXPECTED_PROPOSAL_ROWS}')
    identities={(r.get('220_sku','').strip(),r.get('220_ean','').strip()) for r in proposal}
    if len(identities)!=EXPECTED_IDENTITIES: raise RuntimeError(f'proposal identities {len(identities)} != {EXPECTED_IDENTITIES}')
    bad_fields=sorted({r.get('field','') for r in proposal}-ALLOWED_FIELDS)
    if bad_fields: raise RuntimeError('proposal contains disallowed fields: '+','.join(bad_fields))
    if any(r.get('write_class')!='SAFE_TO_BULK_WRITE_AFTER_APPROVAL' for r in proposal): raise RuntimeError('proposal write_class mismatch')
    pairs=[(r.get('220_sku','').strip(),r.get('220_ean','').strip(),r.get('field','')) for r in proposal]
    if len(pairs)!=len(set(pairs)): raise RuntimeError('duplicate proposal target identity+field')

    sess=_session(); before=_get_values(sess)
    if not before: raise RuntimeError('Master empty')
    source_values_hash=_hash_matrix(before)
    headers=[str(x).strip() for x in before[0]]
    required={'220_sku','220_ean','match_status','220_status'}
    if not required.issubset(set(headers)): raise RuntimeError('Master required identity/marker columns missing')
    sku_i=headers.index('220_sku'); ean_i=headers.index('220_ean'); match_i=headers.index('match_status'); status_i=headers.index('220_status')
    idmap=defaultdict(list)
    for rn,row in enumerate(before[1:],start=2):
        sku=_row_value(row,sku_i).strip(); ean=_row_value(row,ean_i).strip()
        if sku or ean: idmap[(sku,ean)].append(rn)
    missing=[x for x in sorted(identities) if x not in idmap]
    resolved={}; duplicate_resolutions=[]; ambiguous=[]; blocked_rows=set()
    for ident in sorted(identities):
        rows=idmap.get(ident,[])
        if len(rows)==1:
            resolved[ident]=rows[0]; continue
        canonical=[rn for rn in rows if _row_value(before[rn-1],match_i).strip()==CANONICAL_MARKER and _row_value(before[rn-1],status_i).strip()=='ACTIVE_220']
        blocked=[rn for rn in rows if _row_value(before[rn-1],match_i).strip()==DUPLICATE_MARKER and _row_value(before[rn-1],status_i).strip()=='BLOCKED']
        if len(canonical)==1 and len(blocked)==len(rows)-1 and set(canonical).isdisjoint(blocked):
            resolved[ident]=canonical[0]; blocked_rows.update(blocked)
            duplicate_resolutions.append({'220_sku':ident[0],'220_ean':ident[1],'canonical_row':canonical[0],'blocked_rows':','.join(map(str,blocked))})
        else:
            ambiguous.append({'220_sku':ident[0],'220_ean':ident[1],'rows':','.join(map(str,rows)),'canonical_candidates':','.join(map(str,canonical)),'blocked_candidates':','.join(map(str,blocked))})
    if missing or ambiguous or len(resolved)!=EXPECTED_IDENTITIES:
        raise RuntimeError(f'identity resolution failed missing={len(missing)} ambiguous={len(ambiguous)} resolved={len(resolved)}')
    if len(duplicate_resolutions)!=28: raise RuntimeError(f'duplicate resolution count {len(duplicate_resolutions)} != 28')

    original_headers=list(headers)
    missing_headers=[f for f in sorted(ALLOWED_FIELDS) if f not in headers]
    headers_extended=headers+missing_headers
    hidx={h:i for i,h in enumerate(headers_extended)}
    row_count=len(before); col_count=len(headers_extended)
    normalized=[list(r)+['']*(col_count-len(r)) for r in before]; normalized[0]=headers_extended
    protected_cols=[h for h in original_headers if h in PROTECTED_HEADERS]
    protected_before={h:[_row_value(normalized[r],hidx[h]) for r in range(row_count)] for h in protected_cols}
    identity_sequence_before=[(_row_value(normalized[r],sku_i),_row_value(normalized[r],ean_i)) for r in range(1,row_count)]
    blocked_before={rn:list(normalized[rn-1]) for rn in sorted(blocked_rows)}
    before_master_hash=_hash_matrix(normalized)

    diff=[]; rollback=[]; conflicts=[]; already=[]; writes=[]
    for p in proposal:
        sku=p['220_sku'].strip(); ean=p['220_ean'].strip(); field=p['field']; val=p.get('value','')
        rn=resolved[(sku,ean)]
        if rn in blocked_rows: raise RuntimeError('blocked duplicate row selected')
        ci=hidx[field]; old=_row_value(normalized[rn-1],ci)
        rec={'row':rn,'220_sku':sku,'220_ean':ean,'field':field,'before':old,'after':val,'source':p.get('source',''),'write_class':p.get('write_class','')}
        if old==val: already.append(rec)
        elif old!='': conflicts.append(rec)
        else: writes.append(rec); diff.append(rec); rollback.append({'row':rn,'220_sku':sku,'220_ean':ean,'field':field,'before':old})
    dry={'proposal_cells':len(proposal),'identities':len(identities),'to_write':len(writes),'already_equal':len(already),'conflicts':len(conflicts),'missing_headers':missing_headers,'identity_missing':len(missing),'duplicate_identities_resolved':len(duplicate_resolutions),'ambiguous_identities':len(ambiguous),'blocked_duplicate_rows_targeted':sum(1 for w in writes if w['row'] in blocked_rows),'proposal_hash':proposal_hash,'dataset_hash':dataset_hash,'target_spreadsheet_id':MASTER_ID,'before_master_hash':before_master_hash,'source_values_hash':source_values_hash}
    if len(writes)!=EXPECTED_PROPOSAL_ROWS or already or conflicts or dry['blocked_duplicate_rows_targeted']!=0:
        report={'status':'DRY_RUN_BLOCKED','dataset_hash':dataset_hash,'proposal_hash':proposal_hash,'dry_run':dry,'cells_written':0,'identities_affected':0,'protected_fields_changed':0,'identity_fields_changed':0,'stock_price_fields_changed':0,'post_write_verification':'NOT_RUN'}
        arts={'master-bulk-write-dry-run.csv':_csv_bytes(diff,['row','220_sku','220_ean','field','before','after','source','write_class']),'master-bulk-write-rollback.csv':_csv_bytes(rollback,['row','220_sku','220_ean','field','before']),'master-bulk-write-duplicate-resolution.csv':_csv_bytes(duplicate_resolutions,['220_sku','220_ean','canonical_row','blocked_rows']),'master-bulk-write-report.json':_json_bytes(report)}
        _persist_run(os.getenv('DATABASE_URL'),report,arts); return report,arts

    # Structural schema expansion only; then re-read and verify all existing values are still identical before any content value write.
    _ensure_grid_columns(sess,len(missing_headers))
    prewrite=_get_values(sess)
    if _hash_matrix(prewrite)!=source_values_hash:
        raise RuntimeError('Master values changed between preflight and write; aborting before content write')

    data=[]
    for f in missing_headers:
        c=hidx[f]+1; data.append({'range':f'{SHEET_NAME}!{_col_letter(c)}1','values':[[f]]})
    for w in writes:
        c=hidx[w['field']]+1; data.append({'range':f"{SHEET_NAME}!{_col_letter(c)}{w['row']}",'values':[[w['after']]]})
    if len(data)!=(len(missing_headers)+EXPECTED_PROPOSAL_ROWS): raise RuntimeError('atomic payload count mismatch')
    _batch_values_atomic(sess,data)

    after=_get_values(sess)
    after_norm=[list(r)+['']*(col_count-len(r)) for r in after]
    if after_norm: after_norm[0]=headers_extended
    failures=[]
    for w in writes:
        got=_row_value(after_norm[w['row']-1],hidx[w['field']])
        if got!=str(w['after']): failures.append({**w,'readback':got})
    identity_sequence_after=[(_row_value(after_norm[r],sku_i),_row_value(after_norm[r],ean_i)) for r in range(1,len(after_norm))]
    identity_changed=0 if identity_sequence_after==identity_sequence_before else 1
    protected_changed=[]
    for h in protected_cols:
        vals=[_row_value(after_norm[r],hidx[h]) for r in range(len(after_norm))]
        if vals!=protected_before[h]: protected_changed.append(h)
    stock_price_changed=[h for h in protected_changed if h in PRICE_STOCK_STATUS_HEADERS]
    blocked_rows_touched=[]
    for rn,brow in blocked_before.items():
        if rn>len(after_norm) or list(after_norm[rn-1])!=brow: blocked_rows_touched.append(rn)
    post_hash=_hash_matrix(after_norm)
    verified=(len(failures)==0 and identity_changed==0 and len(protected_changed)==0 and len(after_norm)==row_count and len(blocked_rows_touched)==0)
    report={'status':'PASS' if verified else 'FAIL','dataset_hash':dataset_hash,'proposal_hash':proposal_hash,'dry_run':dry,'cells_written':len(writes)-len(failures),'cells_verified':len(writes)-len(failures),'identities_affected':len({(w['220_sku'],w['220_ean']) for w in writes}),'schema_headers_added':len(missing_headers),'schema_headers':missing_headers,'blocked_duplicate_rows_touched':len(blocked_rows_touched),'blocked_duplicate_rows_touched_numbers':blocked_rows_touched,'protected_fields_changed':len(protected_changed),'protected_fields_changed_names':protected_changed,'manual_overrides_overwritten':0 if len(conflicts)==0 else len(conflicts),'identity_fields_changed':identity_changed,'stock_price_fields_changed':len(stock_price_changed),'row_count_before':row_count,'row_count_after':len(after_norm),'before_master_hash':before_master_hash,'post_write_master_hash':post_hash,'readback_failures':len(failures),'post_write_verification':'PASS' if verified else 'FAIL','updated_product_xml_ready':0,'readiness_note':'authoritative 220_title, PHH category mapping/fields, background review and package dimensions remain blockers'}
    arts={'master-bulk-write-dry-run.csv':_csv_bytes(diff,['row','220_sku','220_ean','field','before','after','source','write_class']),'master-bulk-write-rollback.csv':_csv_bytes(rollback,['row','220_sku','220_ean','field','before']),'master-bulk-write-duplicate-resolution.csv':_csv_bytes(duplicate_resolutions,['220_sku','220_ean','canonical_row','blocked_rows']),'master-bulk-write-failures.csv':_csv_bytes(failures,['row','220_sku','220_ean','field','before','after','source','write_class','readback']) if failures else b'row,220_sku,220_ean,field,before,after,source,write_class,readback\r\n','master-bulk-write-report.json':_json_bytes(report)}
    _persist_run(os.getenv('DATABASE_URL'),report,arts); return report,arts
