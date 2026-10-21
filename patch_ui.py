path = r'agent_team/static/index.html'
s = open(path, encoding='utf-8').read()

old = '''  $("#f").addEventListener("submit",async e=>{
    e.preventDefault();$("#ferr").classList.add("hidden");$("#go").disabled=true;
    try{
      const r=await post("/onboard",{work_email:$("#email").value.trim(),user_role:$("#role").value||null,language:$("#lang").value,
        company_name:$("#cname").value||null,website:$("#web").value||null});
      if(r.status==="ready")return openTenant(r.tenant_id);
      startLog();poll(r.tenant_id);
    }catch(err){
      $("#go").disabled=false;
      if(err.status===422){$("#extra").classList.remove("hidden");$("#cname").focus()}
      else{const x=$("#ferr");x.textContent=err.message;x.classList.remove("hidden")}
    }
  });
  async function poll(id){
    let misses=0;
    const fail=msg=>{clearInterval(stageTimer);$("#go").disabled=false;
      const x=$("#ferr");x.textContent=msg;x.classList.remove("hidden")};
    const tick=async()=>{
      try{const t=await api("/tenants/"+id);misses=0;
        if(t.status==="ready"){stopLog();return setTimeout(()=>openTenant(id),600)}
        if(t.status==="failed")return fail("Research failed: "+(t.error||"unknown error")+". Fix the setting in .env and try again.");
      }catch(e){
        if(++misses>=8)return fail("Lost contact with the server. Is uvicorn still running?");
      }
      setTimeout(tick,4000);
    };
    tick();
  }'''

new = '''  $("#f").addEventListener("submit",async e=>{
    e.preventDefault();$("#ferr").classList.add("hidden");$("#go").disabled=true;
    $("#log").classList.add("hidden");$("#log").innerHTML="";
    if(stageTimer){clearInterval(stageTimer);stageTimer=null;}
    try{
      const r=await post("/onboard",{work_email:$("#email").value.trim(),user_role:$("#role").value||null,language:$("#lang").value,
        company_name:$("#cname").value||null,website:$("#web").value||null});
      if(r.status==="ready")return openTenant(r.tenant_id);
      startLog();poll(r.tenant_id);
    }catch(err){
      $("#go").disabled=false;
      if(err.status===422){$("#extra").classList.remove("hidden");$("#cname").focus()}
      else{const x=$("#ferr");x.textContent=err.message;x.classList.remove("hidden")}
    }
  });
  async function poll(id){
    let misses=0;
    const fail=msg=>{
      if(stageTimer){clearInterval(stageTimer);stageTimer=null;}
      const logEl=$("#log");if(logEl){logEl.classList.add("hidden");logEl.innerHTML="";}
      $("#go").disabled=false;
      const x=$("#ferr");x.textContent=msg;x.classList.remove("hidden");
    };
    const tick=async()=>{
      try{const t=await api("/tenants/"+id);misses=0;
        if(t.status==="ready"){if(stageTimer){clearInterval(stageTimer);stageTimer=null;}const logEl=$("#log");if(logEl){logEl.classList.add("hidden");}return setTimeout(()=>openTenant(id),600)}
        if(t.status==="failed"){
          const err=t.error||"unknown error";
          let hint="";
          const e=err.toLowerCase();
          if(e.includes("truncat")) hint=" The reply was cut off (truncated). On free models, large single replies can be cut off - try again or use a model with higher output limits.";
          else if(e.includes("429")||e.includes("rate")||e.includes("quota")||e.includes("exhausted")) hint=" Rate/quota limit reached. Check your primary key or configure LLM_FALLBACK_*.";
          else if(e.includes("json")||e.includes("schema")||e.includes("playbook")) hint=" Invalid JSON/schema. The model returned incomplete output; retrying may help.";
          else hint=" Check your LLM settings and retry.";
          return fail("Onboarding failed: "+err+"."+hint);
        }
      }catch(e){
        if(++misses>=8)return fail("Lost contact with the server. Is uvicorn still running?");
      }
      setTimeout(tick,4000);
    };
    tick();
  }'''

if old not in s:
    print('not found')
    raise SystemExit(1)
s2 = s.replace(old, new)
open(path, 'w', encoding='utf-8').write(s2)
print('ok')
