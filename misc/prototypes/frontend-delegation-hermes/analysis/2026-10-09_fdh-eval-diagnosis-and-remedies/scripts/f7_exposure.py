import json,re,collections,sys
B='/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice/'
RUNS={'airline':(B+'2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_airline_regular',None),
 'retail':(B+'2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_collated',B+'2026-10-07_09-34-31Z_fdh-voice-geval-fixes/'),
 'telecom':(B+'2026-10-07_16-18-35Z_fdh-voice-geval-fixes/fdh_voice_geval_telecom_regular',None)}
READ=('get_','find_','list_','search_','calculate','check_','think')
AFF=re.compile(r"\b(yes|yeah|yep|yup|sure|ok(ay)?|go ahead|do it|do that|confirm(ed)?|correct|that'?s right|right|please|proceed|sounds good|perfect|fine|absolutely|let'?s do|go for it|that works|alright|all right)\b",re.I)
NEG=re.compile(r"\b(no|not|don'?t|wait|hold on|stop|cancel that|actually)\b",re.I)
def load(path,sessions):
  ev=collections.defaultdict(list)
  for l in open(path):
    e=json.loads(l)
    if e.get('session_id') in sessions: ev[e['session_id']].append(e)
  return ev
out={}
detail=[]
for dom,(run,base) in RUNS.items():
  tasks=json.load(open(run+'/observations/data/tasks.json'))
  passing=[t for t in tasks.values() if t.get('reward')==1.0]
  bysrc=collections.defaultdict(set)
  for t in passing: bysrc[t.get('source_run') and base+t['source_run'] or run].add(t['session'])
  stats=collections.Counter()
  for src,sess in bysrc.items():
    ev=load(src+'/agent/events.jsonl',sess)
    stats['sessions_found']+=len(ev)
    for s,es in ev.items():
      es.sort(key=lambda e:(e['audio_ms'],e['timestamp']))
      answers=[];notes={};pos=[];asr=[]
      for i,e in enumerate(es):
        k=e['kind']
        if k=='backend_answer': answers.append(e)
        elif k=='delivery_note': notes[e['answer_id']]=e['outcome']
        elif k=='playback_outcome' and e.get('entry_kind')=='backend_answer': pos.append(e)
        elif k=='asr_final': asr.append(e)
      def outcome(a):
        if a['answer_id'] in notes: 
          po=[p for p in pos if p['audio_ms']>=a['audio_ms'] and p['outcome']==notes[a['answer_id']]]
          return notes[a['answer_id']], (po[0] if po else None)
        po=[p for p in pos if p['audio_ms']>=a['audio_ms']]
        if po and po[0]['outcome']=='heard': return 'heard',po[0]
        return 'unknown',(po[0] if po else None)
      for e in es:
        if e['kind']!='tool_calls_out': continue
        for name in e['calls']:
          if name.startswith(READ): continue
          if name=='transfer_to_human_agents': stats['transfer_calls']+=1; continue
          stats['writes']+=1
          t=e['audio_ms']
          prev=[a for a in answers if a['audio_ms']<t and not (e.get('run_id') and a['run_id']==e['run_id'])]
          # exclude the answer of the run currently executing: answer of current run comes after write
          if not prev: stats['no_prior_answer']+=1; verdict='block_no_summary'; detail.append((dom,s,name,t,verdict,'',[])); continue
          a=prev[-1]; oc,po=outcome(a)
          endt=po['audio_ms'] if po else a['audio_ms']
          turns=[x['transcript'] for x in asr if x['audio_ms']>=endt-300 and x['audio_ms']<t]
          aff=any(AFF.search(x) for x in turns)
          if oc!='heard' and not aff: verdict='block_both'
          elif oc!='heard': verdict='block_not_fully_heard'
          elif not aff: verdict='block_no_affirm'
          else: verdict='pass'
          stats[verdict]+=1; stats['summary_'+oc]+=1
          detail.append((dom,s,name,t/1000,verdict,oc,a['chars'],po and po.get('heard_chars'),turns[-3:]))
  stats['passing_tasks']=len(passing)
  out[dom]=dict(stats)
print(json.dumps(out,indent=1))
json.dump(detail,open(sys.argv[1],'w'),indent=0,default=str)
