"""Create opaque answer records from every successful nondegraded synthesis call."""
import hashlib,json,random
from pathlib import Path
source=Path('results/synthesis/20261005T1842Z/replay.json')
out=Path('.synthesis/grading');out.mkdir(exist_ok=False)
d=json.loads(source.read_text())
assert d['status']=='recorded',d['status']
rows=[r for r in d['rows'] if r['status']=='answered' and r.get('synthesis',{}).get('record',{}).get('calls')]
random.Random(202610051842).shuffle(rows)
blind=[];mapping=[]
for n,r in enumerate(rows,1):
 record=r['synthesis']['record'];bid=f'B{n:03}'
 assert not record['synthesis_outcome']['Degraded']
 blind.append({'id':bid,'case_id':r['id'],'query':record['input']['query'],'answer':record['synthesis_outcome']['Answer']})
 mapping.append({'id':bid,'case_id':r['id'],'stratum':r['stratum'],'trial':r['trial'],'arm':r['arm']})
for name,value in [('blind-answers.json',blind),('private-map.json',mapping)]:
 (out/name).write_text(json.dumps(value,indent=2)+'\n')
(out/'provenance.json').write_text(json.dumps({'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'selected':'Every nondegraded successful synthesis with an actual model call; operational/degraded/deferred results retained separately in replay.json','count':len(blind),'shuffle_seed':202610051842,'blinded_fields':'stratum,arm,trial,model,latency,usage; caseID only joins predeclared visible evidence'},indent=2)+'\n')
print('blinded',len(blind))
