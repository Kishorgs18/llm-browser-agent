"""Minimal LLM browser agent: Playwright + gpt-oss-120b (Groq).
Observe -> decide (JSON action) -> act, until the task is done.
Safety: never clicks submit/pay/confirm buttons; max steps capped."""
import os, json, re, sys, time, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

def load_key():
    for p in [Path('.env'), Path.home()/'Desktop/job-hunt-os/.env']:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith('GROQ_API_KEY='): return line.split('=',1)[1].strip().strip('"')
    return os.environ.get('GROQ_API_KEY')
KEY=load_key(); MODEL='openai/gpt-oss-120b'
BLOCK=re.compile(r'submit|pay|purchase|buy now|confirm|place order|delete',re.I)

SYSTEM="""You control a web browser to complete a task. Each turn you get the page URL, title, a short text excerpt and a numbered list of interactive elements.
Reply with ONE JSON object only, no prose:
{"thought": "...", "action": "goto|click|type|select|check|extract|done", "id": <element number or null>, "value": "<url, text, option or extracted fact>"}
Rules: use only element ids from the list; type into an input before moving on; never press submit, pay or confirm buttons; never 'goto' the URL you are already on - interact with its elements instead; fill one field per turn; before replying done, verify every required field shows the right 'current' value (radios/checkboxes must be CHECKED); only then reply action "done" with a short summary in value. When extracting, copy the full value exactly as written."""

def observe(pg):
    els=pg.evaluate("""() => {
      const out=[]; let i=0;
      document.querySelectorAll('a,button,input,select,textarea').forEach(e=>{
        const r=e.getBoundingClientRect(); if(r.width<1||r.height<1) return;
        const label=(e.labels&&e.labels[0]?e.labels[0].innerText:'')||e.getAttribute('aria-label')||e.placeholder||e.name||e.innerText||e.value||e.title||'';
        e.setAttribute('data-agent-id',i);
        out.push({id:i++,tag:e.tagName.toLowerCase(),type:e.type||'',text:label.trim().slice(0,60),
                  options:e.tagName==='SELECT'?[...e.options].map(o=>o.text).slice(0,10):undefined,
                  current:(e.type==='checkbox'||e.type==='radio')?(e.checked?'CHECKED':'unchecked'):((e.value||'').slice(0,40))});
      }); return out.slice(0,60);}""")
    text=' '.join(pg.inner_text('body').split())[:2000]
    return {'url':pg.url,'title':pg.title(),'text':text,'elements':els}

def decide(task,obs,history,notes):
    msgs=[{'role':'system','content':SYSTEM},{'role':'user','content':f"CURRENT URL: {obs['url']}\nTASK: {task}\nNOTES (facts already found, trust these and do not re-check): {json.dumps(notes)}\nRECENT ACTIONS: {json.dumps(history[-6:])}\nPAGE: {json.dumps(obs)[:5000]}"}]
    for attempt in range(10):
        try:
            r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {KEY}'},
                json={'model':MODEL,'messages':msgs,'temperature':0,'reasoning_effort':'low','response_format':{'type':'json_object'}},timeout=60)
        except requests.exceptions.RequestException as e:
            print(f'     network error ({type(e).__name__}), retrying'); time.sleep(5); continue
        if r.status_code==429:
            wait=float(r.headers.get('retry-after',0) or 0) or 5*(attempt+1); print(f'     rate limited, waiting {wait:.0f}s'); time.sleep(wait); continue
        r.raise_for_status(); return json.loads(r.json()['choices'][0]['message']['content'])
    raise RuntimeError('rate limit persisted')

def act(pg,a,obs):
    sel=lambda i:pg.locator(f'[data-agent-id="{i}"]').first
    kind=a.get('action'); i=a.get('id'); v=a.get('value') or ''
    if kind=='goto': pg.goto(v,timeout=30000)
    elif kind in ('click','check'):
        el=next((e for e in obs['elements'] if e['id']==i),None)
        if el and BLOCK.search(el['text']+' '+el['type']): return f'BLOCKED unsafe click on "{el["text"]}"'
        (sel(i).check() if kind=='check' else sel(i).click(timeout=10000))
    elif kind=='type': sel(i).fill(v)
    elif kind=='select': sel(i).select_option(label=v)
    pg.wait_for_timeout(1200); return 'ok'

def run(task,start,max_steps=15,video_dir='videos',headless=True):
    Path(video_dir).mkdir(exist_ok=True); history=[]; notes=[]; seen={}
    with sync_playwright() as p:
        b=p.chromium.launch(headless=headless,slow_mo=250)
        ctx=b.new_context(record_video_dir=video_dir,viewport={'width':1280,'height':800}); pg=ctx.new_page()
        pg.goto(start,timeout=30000)
        for step in range(1,max_steps+1):
            obs=observe(pg); a=decide(task,obs,history,notes)
            print(f"[{step}] {a.get('action')} id={a.get('id')} value={str(a.get('value'))[:70]!r}\n     thought: {a.get('thought','')[:120]}")
            if a.get('action')=='done':
                missing=[e['text'] for e in observe(pg)['elements'] if e['type'] in ('checkbox','radio') and e['text'] in task and e['current']!='CHECKED']
                if missing: history.append({'step':step,'warning':f'Not done yet: still unchecked {missing}'}); continue
                history.append(a); break
            if a.get('action')=='extract':
                v=str(a.get('value')); body=pg.inner_text('body')
                if not re.search(r'(?<![\w.])'+re.escape(v)+r'(?![\w])',body):
                    history.append({'step':step,'warning':f'"{v}" is not an exact standalone value on this page; extract the full value again.'}); continue
                notes.append(v); history.append({'step':step,'action':'extract','value':a.get('value')}); continue
            if a.get('action')=='goto' and str(a.get('value')).rstrip('/')==pg.url.rstrip('/'):
                history.append({'step':step,'warning':f'You are already on {pg.url}. Use the element ids to fill the form.'}); continue
            key=(a.get('action'),a.get('id'),str(a.get('value'))); seen[key]=seen.get(key,0)+1
            if seen[key]>2: history.append({'step':step,'warning':'You are repeating the same action; use NOTES and move on.'}); continue
            try: res=act(pg,a,obs)
            except Exception as e: res=f'error: {str(e)[:80]}'
            history.append({'step':step,'action':a.get('action'),'id':a.get('id'),'value':a.get('value'),'result':res})
        final=observe(pg)
        filled={e['text']:e['current'] for e in final['elements'] if e['current'] not in ('','unchecked') and e['type']!='submit'}
        print('\nVERIFIED STATE (read from the page, not the model):'); print('  facts:',notes); [print(f'  {k} -> {v}') for k,v in filled.items()]
        pg.screenshot(path=f'{video_dir}/final.png',full_page=True)
        vid=pg.video.path(); ctx.close(); b.close()
    print('\nRESULT:',history[-1]); print('VIDEO:',vid); return history

if __name__=='__main__':
    task=("Follow this plan in order. "
          "1) Open the product page for the book 'A Light in the Attic' on this bookshop. "
          "2) Use action 'extract' to record its price exactly as shown. "
          "3) goto https://httpbin.org/forms/post . "
          "4) Fill the form one field per turn: customer name 'Kishore', telephone '07000000000', email 'demo@example.com', "
          "check the Medium size radio, check the Bacon topping, and type 'Book price found: <price from NOTES>' into Delivery instructions. "
          "5) Do NOT click Submit. Reply done with a one line summary.")
    run(task,'https://books.toscrape.com/')
