from __future__ import annotations
import csv, json, pathlib
from category_integration import ingest_csv_bytes, map_clusters

DEFAULT_CLUSTER_64='220_phh_category_mapping_64_clusters.csv'
DEFAULT_COHORT_531='220_unclustered_998_cohort_summary.csv'


def _read_csv(path):
    with open(path,'r',encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def run(authoritative_category_file, cluster64_file=DEFAULT_CLUSTER_64, cohort531_file=DEFAULT_COHORT_531, out_dir='category-stage-output'):
    schema=ingest_csv_bytes(pathlib.Path(authoritative_category_file).read_bytes())
    clusters=_read_csv(cluster64_file)
    cohorts=_read_csv(cohort531_file)
    mapped64=map_clusters(clusters,schema)
    mapped531=map_clusters(cohorts,schema)
    out=pathlib.Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    (out/'normalized-category-schema.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'cluster64-category-candidates.json').write_text(json.dumps(mapped64,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'cohort531-category-candidates.json').write_text(json.dumps(mapped531,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={
      'authoritative_category_file_loaded':True,
      'normalized_categories':len(schema['categories']),
      'source_clusters_64_rows':len(clusters),
      'source_cohorts_531_rows':len(cohorts),
      'cluster64_status':{s:sum(x['status']==s for x in mapped64) for s in ('CANDIDATE','AMBIGUOUS','UNMAPPED')},
      'cohort531_status':{s:sum(x['status']==s for x in mapped531) for s in ('CANDIDATE','AMBIGUOUS','UNMAPPED')},
      'master_writes':0,'shopify_writes':0,'phh_sends':0,'product_xml_publication':'OFF'
    }
    (out/'category-pipeline-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    raise SystemExit('Disabled by design: invoke run(...) only after authoritative PHH Categories fields and values file is supplied.')
