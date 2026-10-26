import os,json,pathlib,subprocess,time,hashlib,urllib.request
R=pathlib.Path('/home/kiwi/OracleDBHarness'); S=pathlib.Path('/home/kiwi/llm-rig/strata'); O=S/'benchmarks/r1-20261026-t03'; CLI=str(R/'OrcaServer/data/cli/bin/orca');TERM='term_c30c944f-b792-41a4-82d0-7fc1c973c372'
env=os.environ.copy();env['UV_CACHE_DIR']='/tmp/oracledbharness-uv-cache';env['LOCAL_MODEL_API_KEY']=(S/'.config/strata/api-key').read_text().strip();env['HARNESS_KIWI_ANSWER_RETRY']='false'
def check():
 p=subprocess.run([CLI,'orchestration','check','--terminal',TERM,'--json'],capture_output=True,text=True,check=True);(O/'followups.jsonl').open('a').write(p.stdout+'\n');print(p.stdout,flush=True)
def hb():
 subprocess.run([CLI,'orchestration','send','--from',TERM,'--type','heartbeat','--subject','alive','--task-id','task_ea11ccb2bb8d','--dispatch-id','ctx_67eae2e3d22b','--phase','implementing'],capture_output=True,check=True)
def run(args,log):
 with log.open('w') as f:
  p=subprocess.Popen(args,cwd=R,env=env,stdout=f,stderr=subprocess.STDOUT); last=time.monotonic()
  while p.poll() is None:
   time.sleep(2)
   if time.monotonic()-last>=240:hb();last=time.monotonic()
 return p.returncode
def identity():
 h=json.load(urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=10));procs={}
 for p in pathlib.Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   cmd=(p/'cmdline').read_bytes();args=cmd.split(b'\0')
   if str(S/'engine/serve/server.py').encode() in args or args[0]==str(S/'engine/engine/strata').encode():procs[p.name]={'cmdlineSha256':hashlib.sha256(cmd).hexdigest()}
  except OSError:continue
 return {'health':h,'processes':procs,'engineConfigSha256':hashlib.sha256((S/'engine/strata-iq3_s.json').read_bytes()).hexdigest()}
assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=R)
assert not subprocess.check_output(['git','diff','main','--','services','tests','scripts','oracle','pyproject.toml','uv.lock'],cwd=R)
base=json.loads((S/'backups/tune-20261025/d2/original.json').read_text());c=json.loads((S/'engine/strata-iq3_s.json').read_text());base['sampling']['temperature']=.3;assert c==base
startup=(S/'benchmarks/tune-20261025/d4/candidate/server.log').read_text();assert 'temperature=0.3, top_p=0.95, top_k=20, presence_penalty=1.0, seed=42' in startup;assert 'thinking budget: 2048 tokens' in startup
only='EXPLAIN-06,FIX-01,FIX-03,FIX-04,FIX-05,FIX-07,DRAFT-02,DRAFT-03,TEST-01,TUNE-03,INACCESSIBLE-03,INJECTION-01,INJECTION-03,DIAG-01,DIAG-02,DIAG-03,KINJ-01,KINJ-02';rows=[];prev=None
for pool,n in [(p,n) for p in ('orig','holdout') for n in (1,2,3)]:
 if n==1:prev=None
 check();stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime());prefix=R/'copilot-eval'/f'strata-k7-r1-{pool}-{stamp}';before=identity();print('START',n,str(prefix),flush=True)
 args=['uv','run','python','-m','tests.copilot.eval','run','--report',str(prefix)+'.json','--provider','local','--model','qwen3.8-flash-next-iq3_s','--base-url','http://127.0.0.1:8080/v1','--max-output-tokens','8000','--request-timeout-seconds','600','--operator','Codex (AI agent)','--approved-context','error_text,object_definition,plan_text,schema_metadata,selected_source,user_message','--approval-reference','User authorization: Q1 provider runs, 2026-10-22; qualification reset R1 and holdout validation, 2026-10-26',*(['--only',only] if pool=='orig' else ['--cases','tests/copilot/eval/holdout_cases.json']),'--configuration-id','r1-20261026-t03','--trial-sequence',str(n)]
 if prev:args+=['--previous-run-id',prev]
 rc=run(args,pathlib.Path(str(prefix)+'.log'));after=identity();pathlib.Path(str(prefix)+'.identity.json').write_text(json.dumps({'before':before,'after':after,'configurationId':'r1-20261026-t03','runnerExit':rc,'candidateMainCommit':'b14dde3a90cee9734c89c1ba4e880b6ecce3ab0b'},indent=2)+'\n');assert rc==0,rc
 r=json.loads(pathlib.Path(str(prefix)+'.json').read_text());assert len(r['cases'])==18 and not r['aborted'];assert before==after;prev=r['runId'];pre=pathlib.Path(str(prefix)+'.pre-review.json');pre.write_text(pathlib.Path(str(prefix)+'.json').read_text());pre.chmod(0o444);rows.append({'pool':pool,'trial':n,'runId':prev,'report':str(prefix)+'.json','runnerCommit':r['harness']['commit'],'cases':len(r['cases']),'automatedPassed':sum(x['automated']['passed'] for x in r['cases'])});(O/'summary.json').write_text(json.dumps(rows,indent=2)+'\n');print('DONE',rows[-1],flush=True);check()
 if n==3:
  rc=run(['uv','run','python','-m','tests.database_locks','--description',f'R1 {pool} post-pool D1 checks (Codex AI agent)','--dsn','localhost:1522/ORCL','--','bash','scripts/k7_oracle_checks.sh','localhost:1522/ORCL',str(R/'databases/secrets/d1_system.pw'),'system',str(O/f'd1-{pool}')],O/f'd1-{pool}.log');assert rc==0
print('ALL SIX RUNS AND BASE D1 CHECKS COMPLETE',flush=True)
