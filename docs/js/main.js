/* ===== 首页：行业国家队总览 ===== */
// PALETTE / colorOf / colorAt / yi / pct 见 js/util.js（四页共用）
function signCls(v){ return v>0?'pos':(v<0?'neg':''); }
function signStr(v,fmt){ if(v==null||v===0) return fmt(v); return (v>0?'+':'')+fmt(v); }

let INDUSTRIES=[], META={}, sortKey='nt_value', sortDesc=true;

async function boot(){
  // 采集状态独立加载，即使主数据缺失也能显示"今天是否跑成功"
  fetch('data/status.json').then(r=>r.json()).then(renderStatus).catch(()=>{});
  try{
    const [meta,industries]=await Promise.all([
      fetch('data/meta.json').then(r=>r.json()),
      fetch('data/industries.json').then(r=>r.json()),
    ]);
    INDUSTRIES=industries; META=meta;
    setIndustryOrder(meta.industry_order||[]);   // 供 colorOf 稳定取色
    fetch('data/stock/meta.json').then(r=>r.json())
      .then(sm=>renderOverviewCards(meta,sm)).catch(()=>renderOverviewCards(meta,null));
    const hint=document.querySelector('.table-head .hint');
    if(hint) hint.innerHTML=`持仓口径：报告期 <b>${meta.report_date||'—'}</b>（十大持有人半年披露一次）· 点击任意行展开成员 ETF`;
    renderMeta(meta); renderStats(meta,industries);
    renderIndustryChart(industries); renderGroupChart(meta,industries);
    renderTable();
    bindSort();
  }catch(e){
    document.querySelector('main').innerHTML='<div class="loading">数据尚未生成，请先运行采集脚本或等待 GitHub Actions 首次运行。<br>'+e+'</div>';
  }
}

function renderOverviewCards(etf, stk){
  const el=document.getElementById('ov-cards'); if(!el) return;
  const etfCard=
    `<a class="ov-card etf" href="etf.html">
       <div class="ov-tag">ETF 渠道（间接持股）</div>
       <div class="ov-mv">${yi(etf.total_nt_value)}<small>元</small></div>
       <div class="ov-sub">国家队持有 ${etf.num_nt_etfs} 只 ETF · ${etf.num_industries} 行业 · 报告期 ${etf.report_date||'—'}</div>
       <div class="ov-links"><span>进入 ETF 板块 ›</span></div>
     </a>`;
  const stkCard = stk ?
    `<a class="ov-card stk" href="stock.html">
       <div class="ov-tag">个股渠道（直接持股）</div>
       <div class="ov-mv">${yi(stk.total_mv)}<small>元</small></div>
       <div class="ov-sub">国家队重仓 ${stk.num_stocks} 只个股 · ${stk.num_industries} 行业 · 报告期 ${stk.report_date||'—'}</div>
       <div class="ov-links"><span>进入 个股 板块 ›</span></div>
     </a>`
    : `<a class="ov-card stk" href="stock.html"><div class="ov-tag">个股渠道（直接持股）</div>
       <div class="ov-mv muted" style="font-size:18px">数据生成中…</div>
       <div class="ov-links"><span>进入 个股 板块 ›</span></div></a>`;
  el.innerHTML=etfCard+stkCard;
}

function renderStatus(s){
  const el=document.getElementById('run-status');
  if(!el||!s||!s.latest) return;
  const L=s.latest;
  const MAP={ok:['✅','当天采集正常','ok'],warning:['⚠️','采集有警告','warn'],error:['❌','采集异常','err']};
  const [icon,label,cls]=MAP[L.status]||MAP.ok;
  const dots=(s.recent||[]).slice(0,16).map(r=>{
    const c=(MAP[r.status]||MAP.ok)[2];
    return `<i class="rs-dot ${c}" title="${r.run_at} · ${(MAP[r.status]||MAP.ok)[1]} · ${r.mode==='init'?'初始化':'每日'}"></i>`;
  }).join('');
  // 展开详情:关键指标 + 完整警告/错误 + 最近运行记录(供针对性排查)
  const st=L.stats||{};
  const facts=[
    `模式 ${L.mode==='init'?'初始化(全量)':'每日增量'}`,
    L.num_nt_etfs!=null?`国家队 ETF ${L.num_nt_etfs} 只`:'',
    L.num_industries!=null?`行业 ${L.num_industries} 个`:'',
    st.price_total?`收盘价覆盖 ${st.price_ok}/${st.price_total}`:'',
    st.share_days_added!=null?`份额新增 ${st.share_days_added} 天`:'',
    st.sse_etfs!=null?`沪深接口 ${st.sse_etfs}/${st.szse_etfs}`:'',
    L.report_rescan?'本次重扫持有人':'',
    st.no_new_trading_day?'非交易日·份额未变':'',
    L.duration_sec!=null?`用时 ${L.duration_sec}s`:'',
  ].filter(Boolean);
  const msgs=[...(L.errors||[]).map(m=>`<li class="err">✕ ${m}</li>`),
              ...(L.warnings||[]).map(m=>`<li class="warn">! ${m}</li>`)].join('');
  const recent=(s.recent||[]).map(r=>{
    const c=MAP[r.status]||MAP.ok;
    const bad=(r.errors&&r.errors[0])||(r.warnings&&r.warnings[0])||'';
    return `<tr><td class="muted">${r.run_at}</td><td class="rs-${c[2]}">${c[0]} ${c[1]}</td>`+
      `<td>${r.mode==='init'?'初始化':'每日'}</td><td class="muted">${bad}</td></tr>`;
  }).join('');
  el.className='run-status '+cls;
  el.innerHTML=
    `<div class="rs-head" id="rs-toggle">
       <span class="rs-icon">${icon}</span>
       <span class="rs-label">${label}</span>
       <span class="rs-time">最近采集 ${L.run_at} · 行情日 ${L.trade_date||'—'}</span>
       <span class="rs-dots" title="最近 ${(s.recent||[]).length} 次采集">${dots}</span>
       <span class="rs-more">详情 ▾</span>
     </div>
     <div class="rs-detail" id="rs-detail" style="display:none">
       <div class="rs-facts">${facts.map(f=>`<span>${f}</span>`).join('')}</div>
       ${msgs?`<ul class="rs-msgs">${msgs}</ul>`:'<div class="muted" style="font-size:12.5px">本次无警告/错误</div>'}
       <div class="rs-recent-title">最近运行记录</div>
       <div class="table-scroll"><table class="rs-recent"><tbody>${recent}</tbody></table></div>
     </div>`;
  const tog=document.getElementById('rs-toggle'), det=document.getElementById('rs-detail'), more=el.querySelector('.rs-more');
  tog.onclick=()=>{const open=det.style.display==='none';det.style.display=open?'':'none';more.textContent=open?'收起 ▴':'详情 ▾';};
}

function renderMeta(m){
  document.getElementById('meta').innerHTML=
    `<span>更新时间：${m.generated_at}</span>`+
    `<span>行情日：${m.trade_date||'—'}</span>`+
    `<span>持仓报告期：${m.report_date||'—'}</span>`;
}

function renderStats(m,inds){
  if(!document.getElementById('stats')) return;
  const cards=[
    ['国家队 ETF 数量', m.num_nt_etfs, '只'],
    ['覆盖行业 / 主题', m.num_industries, '个'],
    ['国家队持有总市值', yi(m.total_nt_value), ''],
    ['国家队持有总份额', yi(m.total_nt_amount)+'份', ''],
  ];
  document.getElementById('stats').innerHTML=cards.map(c=>
    `<div class="stat"><div class="k">${c[0]}</div><div class="v">${c[1]}<small>${c[2]}</small></div></div>`
  ).join('');
}

function renderIndustryChart(inds){
  if(!document.getElementById('chart-industry')) return;
  const top=[...inds].sort((a,b)=>(b.nt_value||0)-(a.nt_value||0)).slice(0,14).reverse();
  const el=echarts.init(document.getElementById('chart-industry'));
  el.setOption({
    grid:{left:8,right:24,top:10,bottom:10,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},
      formatter:p=>`${p[0].name}<br/>持有市值：<b>${yi(p[0].value)}元</b>`},
    xAxis:{type:'value',axisLabel:{formatter:v=>yi(v)},splitLine:{lineStyle:{color:'#eef1f6'}}},
    yAxis:{type:'category',data:top.map(d=>d.industry),axisLabel:{fontSize:12}},
    series:[{type:'bar',data:top.map((d,i)=>({value:d.nt_value||0,
      itemStyle:{color:colorOf(d.industry),borderRadius:[0,4,4,0]}})),
      barMaxWidth:20}]
  });
  window.addEventListener('resize',()=>el.resize());
}

function renderGroupChart(m,inds){
  if(!document.getElementById('chart-group')) return;
  const g={};
  inds.forEach(b=>{for(const[k,v]of Object.entries(b.groups||{})) g[k]=(g[k]||0)+v;});
  const data=Object.entries(g).map(([k,v])=>({name:k,value:v})).sort((a,b)=>b.value-a.value);
  const el=echarts.init(document.getElementById('chart-group'));
  el.setOption({
    tooltip:{trigger:'item',formatter:p=>`${p.name}<br/>持有份额：<b>${yi(p.value)}份</b>（${p.percent}%）`},
    legend:{bottom:0,type:'scroll',textStyle:{fontSize:11}},
    series:[{type:'pie',radius:['42%','68%'],center:['50%','44%'],
      avoidLabelOverlap:true,itemStyle:{borderColor:'#fff',borderWidth:2},
      label:{show:false},
      data:data.map((d,i)=>({...d,itemStyle:{color:colorAt(i)}}))}]
  });
  window.addEventListener('resize',()=>el.resize());
}

function bindSort(){
  document.querySelectorAll('th[data-sort]').forEach(th=>{
    th.addEventListener('click',()=>{
      const k=th.dataset.sort;
      if(sortKey===k) sortDesc=!sortDesc; else{sortKey=k;sortDesc=true;}
      document.querySelectorAll('th').forEach(x=>x.classList.remove('active','asc','desc'));
      th.classList.add('active',sortDesc?'desc':'asc');
      renderTable();
    });
  });
}

function renderTable(){
  const tb=document.querySelector('#industry-table tbody');
  if(!tb) return;
  const rows=[...INDUSTRIES].sort((a,b)=>{
    const x=a[sortKey]??-Infinity,y=b[sortKey]??-Infinity;
    return sortDesc?(y-x):(x-y);
  });
  tb.innerHTML='';
  rows.forEach(b=>{
    const groups=Object.entries(b.groups||{}).slice(0,3)
      .map(([k,v])=>`${k} ${yi(v)}`).join(' · ');
    const chg=b.amount_change, chgpct=b.amount_change_pct;
    const tr=document.createElement('tr');
    tr.className='ind-row';
    tr.innerHTML=
      `<td class="ta-l"><span class="ind-name"><span class="dot" style="background:${colorOf(b.industry)}"></span>${b.industry}`+
        `${b.new_entries?`<span class="chip new">新进 ${b.new_entries}</span>`:''}</span></td>`+
      `<td>${b.num_etfs}</td>`+
      `<td><b>${yi(b.nt_value)}</b></td>`+
      `<td>${pct(b.nt_ratio)}</td>`+
      `<td class="${signCls(chg)}">${signStr(chg,yi)}份`+
        `${chgpct!=null?` <span class="groups-mini">(${signStr(chgpct*100,v=>v.toFixed(1))}%)</span>`:''}</td>`+
      `<td class="ta-l groups-mini">${groups||'—'}</td>`;
    tb.appendChild(tr);

    const dr=document.createElement('tr');
    dr.className='detail-row';dr.style.display='none';
    dr.innerHTML=`<td colspan="6"><div class="detail-inner">${etfListHtml(b)}</div></td>`;
    tb.appendChild(dr);
    tr.addEventListener('click',()=>{dr.style.display=dr.style.display==='none'?'':'none';});
  });
}

function etfListHtml(b){
  const rows=b.etfs.map(e=>
    `<tr>
      <td class="ta-l"><a href="detail.html?code=${e.code}">${e.name}</a>`+
        `${e.is_new?'<span class="tag">新进</span>':''}</td>`+
      `<td class="ta-l muted">${e.code}</td>`+
      `<td>${yi(e.nt_value)}</td>`+
      `<td>${yi(e.nt_amount)}份</td>`+
      `<td>${pct(e.nt_ratio)}</td>`+
      `<td>${e.close??'—'}</td>`+
    `</tr>`).join('');
  return `<table class="etf-list"><thead><tr>
    <th class="ta-l">ETF</th><th class="ta-l">代码</th><th>持有市值</th>
    <th>持有份额</th><th>持有占比</th><th>最新净值</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

boot();
