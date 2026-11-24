import os,sys,tempfile,json,socket
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
checks=[]
def passed(name): checks.append(name); print('PASS:',name)
with tempfile.TemporaryDirectory(prefix='lume-security-synthetic-',dir=root) as temporary:
 os.environ.update(SDR_DATA_DIR=temporary,LUME_PUBLIC_URL='https://lume.tznog.com',LUME_ALLOWED_HOSTS='lume.tznog.com',COOKIE_SECURE='false',TLDEXTRACT_CACHE=temporary+'/suffix-cache')
 from app import accounts,database,network,service
 from app.identity import tenant
 from app.auth_routes import ATTEMPTS,secure_cookie
 from app.main import app
 from fastapi.testclient import TestClient
 accounts.initialize()
 alice=accounts.create_user('review_alice','Synthetic Alice','Synthetic password alpha 2026')
 bob=accounts.create_user('review_bob','Synthetic Bob','Synthetic password beta 2026')
 admin=accounts.create_user('review_admin','Synthetic Admin','Synthetic password admin 2026',role='admin')
 # No lifespan is entered: the production scheduler is never started.
 clients=[TestClient(app,base_url='https://lume.tznog.com') for _ in range(3)]
 a,b,anon=clients
 headers={'Origin':'https://lume.tznog.com'}
 try:
  with patch('socket.create_connection',side_effect=AssertionError('External network disabled')):
   response=a.post('/api/login',json={'username':'review_alice','password':'Synthetic password alpha 2026'},headers=headers)
   assert response.status_code==200,response.status_code
   assert all(x in response.headers['set-cookie'] for x in ['Secure','HttpOnly','SameSite=strict','Max-Age=43200'])
   b.post('/api/login',json={'username':'review_bob','password':'Synthetic password beta 2026'},headers=headers).raise_for_status()
   passed('Lume public-host login cookie is Secure/HttpOnly/SameSite=Strict, 12-hour maximum')
   assert anon.get('/api/settings').status_code==401
   assert a.get('/api/admin/stats').status_code==403
   assert a.patch('/api/admin/users/'+str(bob['id']),json={'role':'admin'},headers=headers).status_code==403
   passed('Anonymous access and ordinary-user administrative actions refused')
   response=a.post('/api/import/domains',json={'domains':'example.com'},headers=headers)
   assert response.status_code==200,(response.status_code,response.text)
   a_leads=a.get('/api/leads').json()
   with tenant(alice['id']):
    with database.connect() as db: lead_id=db.execute('SELECT id FROM leads LIMIT 1').fetchone()[0]
   assert a.get('/api/leads/'+str(lead_id)).status_code==200
   blocked=b.get('/api/leads/'+str(lead_id))
   assert blocked.status_code==400 and blocked.json()=={'detail':'Loja não encontrada.'}
   assert b.get('/api/leads').json()['items']==[]
   passed('Synthetic imported lead visible only to its tenant; no crawling triggered')
   assert a.put('/api/settings',json={},headers={'Origin':'https://audit.invalid'}).status_code==403
   assert a.post('/api/import/domains',json={'domains':'example.com'}).status_code==403
   passed('Mutations reject missing and foreign Origin')
   old=a.cookies.get('lume_session')
   assert a.post('/api/logout',headers=headers).status_code==200
   assert anon.get('/api/settings',headers={'Cookie':'lume_session='+old}).status_code==401
   passed('Logout rejects replay of the previous session')
   a.post('/api/login',json={'username':'review_alice','password':'Synthetic password alpha 2026'},headers=headers).raise_for_status()
   old=a.cookies.get('lume_session')
   a.put('/api/account/password',json={'current_password':'Synthetic password alpha 2026','new_password':'Synthetic changed password 2026'},headers=headers).raise_for_status()
   assert anon.get('/api/settings',headers={'Cookie':'lume_session='+old}).status_code==401
   assert a.get('/api/settings').status_code==200
   passed('Password change invalidates the old session and creates a valid replacement')
   a.post('/api/logout',headers=headers).raise_for_status();b.post('/api/logout',headers=headers).raise_for_status()
   with accounts.connect() as db: assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
   passed('All sessions created by the local tests revoked')
   payload=accounts.new_session(accounts.get_user(alice['id']))
   with accounts.connect() as db:db.execute('UPDATE sessions SET expires_at=? WHERE id=?',('2000-01-01T00:00:00+00:00',payload['sid']))
   assert accounts.resolve_session(payload) is None
   accounts.revoke_session(payload['sid'])
   passed('Expired synthetic sessions rejected server-side')
  for address in ['127.0.0.1','192.168.1.2','169.254.169.254','::1','fd00::1']:
   family=socket.AF_INET6 if ':' in address else socket.AF_INET
   with patch.object(network.socket,'getaddrinfo',return_value=[(family,socket.SOCK_STREAM,6,'',(address,443))]):
    try: network.public_address('example.com',443)
    except network.FetchError: pass
    else: raise AssertionError('Non-public address accepted')
  passed('SSRF resolver rejects loopback, RFC1918, link-local and IPv6 ULA using synthetic DNS answers')
  public=(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',80))
  private=(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))
  with patch.object(network.socket,'getaddrinfo',return_value=[public,private]):
   try: network.public_address('example.com',80)
   except network.FetchError: pass
   else: raise AssertionError('Mixed DNS answers accepted')
  passed('Mixed public/private DNS answers are rejected')
  class Redirect:
   status=302
   def getheader(self,name,default=None):return 'http://second.example.com/' if name=='Location' else default
  class Connection:
   def __init__(self,address,port,timeout): assert address=='93.184.216.34'
   def request(self,*args,**kwargs):pass
   def getresponse(self):return Redirect()
   def close(self):pass
  with patch.object(network.socket,'getaddrinfo',side_effect=[[public],[private]]),patch.object(network.http.client,'HTTPConnection',Connection):
   try:network.fetch('http://example.com/')
   except network.FetchError:pass
   else:raise AssertionError('Private redirect accepted')
  passed('Connection receives validated IP; redirect to private DNS destination is blocked without a socket')
  assert service.spreadsheet_safe('=1+1').startswith("'")
  assert service.spreadsheet_safe('  @SUM(1,1)').startswith("'")
  passed('CSV export neutralizes harmless spreadsheet formula markers')
 finally:
  for c in clients:c.close()
  ATTEMPTS.clear()
print('CLEANUP: synthetic local database and secret files removed; no production test accounts created.')
