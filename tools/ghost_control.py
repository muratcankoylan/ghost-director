"""Loopback operator client. No provider credentials are read by this tool."""
import argparse,json,time,uuid
import httpx
p=argparse.ArgumentParser();p.add_argument('operation');p.add_argument('--payload',default='{}');p.add_argument('--wait',type=int,default=0);a=p.parse_args()
base='http://127.0.0.1:8000/api/productions/ghost-director-hackathon'
with httpx.Client(timeout=30) as client:
 snapshot=client.get(base+'/snapshot');snapshot.raise_for_status();s=snapshot.json()
 if a.operation=='snapshot':
  print(json.dumps({'scene':s['scene']['id'],'counts':{k:len(s.get(k,[])) for k in ('shots','runs','tasks','jobs','artifacts','takes','observations')},'budget':s['budget'],'jobs':[{'id':j['id'],'status':j['status'],'model':j['model']} for j in s['jobs'][-8:]]},indent=2));raise SystemExit
 command={'id':str(uuid.uuid4()),'operation':a.operation,'expected_revision':s['scene']['id'],'payload':json.loads(a.payload)}
 response=client.post(base+'/commands',json=command);response.raise_for_status();receipt=response.json();print(json.dumps(receipt),flush=True)
 deadline=time.monotonic()+a.wait
 while time.monotonic()<deadline:
  receipt=client.get(base+'/commands/'+command['id']).json()
  if receipt.get('status')!='accepted':print(json.dumps(receipt),flush=True);break
  time.sleep(2)
