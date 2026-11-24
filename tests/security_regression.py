import asyncio, hashlib, os, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
with tempfile.TemporaryDirectory(prefix='security-regression-',dir=root) as tmp:
 os.environ['SDR_DATA_DIR']=tmp
 from app import accounts, security
 from app.http_security import SecurityEnvelope
 accounts.initialize()
 password='Synthetic regression password 2026'
 salt='ab'*16
 legacy=salt+':'+hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
 user=accounts.create_user('legacy_test','Synthetic legacy',saved_hash=legacy)
 assert accounts.authenticate('legacy_test','incorrect') is None
 before=time.perf_counter()
 user=accounts.authenticate('legacy_test',password)
 elapsed=time.perf_counter()-before
 assert user and user['password_hash'].startswith('scrypt$131072$8$1$')
 assert security.verify_password(password,user['password_hash'])
 assert not security.verify_password('incorrect',user['password_hash'])
 print(f'PASS: legacy login upgrades hash; new verification succeeds; upgrade took {elapsed:.3f}s locally')
 session=accounts.new_session(user)
 assert accounts.resolve_session(session)
 with accounts.connect() as db:
  db.execute('UPDATE sessions SET last_seen=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),session['sid']))
 assert accounts.resolve_session(session) is None
 print('PASS: one-hour idle timeout rejects a session with a future absolute expiration')
 async def exercise(chunks,status=401):
  emitted=[]; calls=[]
  async def downstream(scope,receive,send):
   calls.append(True)
   body=await receive()
   assert body['body']==b''.join(chunks)
   await send({'type':'http.response.start','status':status,'headers':[]})
   await send({'type':'http.response.body','body':b'{}'})
  messages=[{'type':'http.request','body':c,'more_body':i<len(chunks)-1} for i,c in enumerate(chunks)]
  async def receive(): return messages.pop(0)
  async def send(message): emitted.append(message)
  await SecurityEnvelope(downstream)({'type':'http','method':'POST'},receive,send)
  headers=dict(emitted[0]['headers'])
  assert headers[b'content-security-policy'] and headers[b'cache-control']==b'no-store'
  assert headers[b'strict-transport-security']==b'max-age=86400'
  return emitted[0]['status'],calls
 assert asyncio.run(exercise([b'{}']))[0]==401
 result,calls=asyncio.run(exercise([b'x'*3_000_001,b'x'*3_000_000]))
 assert result==413 and not calls
 print('PASS: headers on errors; chunked oversized body blocked before downstream parser without Content-Length')
print('CLEANUP: synthetic files removed')
