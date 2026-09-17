import csv, io
from category_integration import ingest_csv_bytes, map_clusters, build_category_write_plan, verify_category_readback, category_aware_readiness, CategoryImportError

FIXTURE=b'''category_id,category_name,property_key,property_name,required,allowed_values,value_type,package_dimensions_required,fashion_package_exempt\nTEST-100,Fixture Jackets,color,Color,true,Black|Green,string,false,true\nTEST-100,Fixture Jackets,size,Size,true,S|M|L|XL,string,false,true\nTEST-200,Fixture Equipment,material,Material,false,Steel|Aluminium,string,true,false\n'''

def test_ingest():
    s=ingest_csv_bytes(FIXTURE)
    assert s['schema_version']=='phh-category-normalized-v1'
    assert s['validation']['category_count']==2
    c={x['category_id']:x for x in s['categories']}
    assert c['TEST-100']['package_rules']['fashion_package_exempt'] is True
    assert c['TEST-200']['package_rules']['dimensions_required'] is True
    assert [p['key'] for p in c['TEST-100']['properties']]==['color','size']

def test_conflict_rejected():
    bad=FIXTURE+b'TEST-100,Other Name,,,,,,false,true\n'
    try: ingest_csv_bytes(bad)
    except CategoryImportError: return
    assert False, 'conflict must be rejected'

def test_mapping_no_invented_ids():
    s=ingest_csv_bytes(FIXTURE)
    m=map_clusters([{'cluster':'Fixture Jackets'}],s)[0]
    assert m['status']=='CANDIDATE'
    assert m['candidates'][0]['category_id']=='TEST-100'
    assert m['authoritative_category_write']=='NO'

def test_writer_dry_run_and_verify():
    master=[{'220_sku':'A','220_ean':'123','220_status':'ACTIVE_220','match_status':'','220_category_id':'','220_category_name':'','220_properties_json':''}]
    p=build_category_write_plan(master,[{'220_sku':'A','220_ean':'123','220_category_id':'TEST-100','220_category_name':'Fixture Jackets','220_properties':{'color':'Black'}}])
    assert p['enabled'] is False and p['errors']==[] and p['write_count']==3
    after=[dict(master[0])]
    for ch in p['plan']: after[0][ch['field']]=ch['after']
    assert verify_category_readback(master,after,p)['status']=='PASS'

def test_readiness_external_gates_separate():
    s=ingest_csv_bytes(FIXTURE); cat={x['category_id']:x for x in s['categories']}['TEST-100']
    ident={'properties':{'color':'Black','size':'M'},'title_pass':True,'description_pass':True,'grouping_pass':True,'ean_pass':True,'supplier_code_pass':True,'images_ge2_pass':True}
    r=category_aware_readiness(ident,cat,background_status=None,external_metafield_gate_resolved=False)
    assert r['category_pass'] and r['required_properties_pass'] and r['package_dimensions_pass']
    assert not r['first_pilot_candidate'] and not r['ready_product_xml']
