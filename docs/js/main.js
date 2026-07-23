/* ===== 首页：行业国家队总览 ===== */
const IND_COLORS = ['#c8102e','#e07b39','#2b6cb0','#1a9e5f','#8a56c2','#d4a017',
  '#3aa0a0','#c2506e','#5b8c2a','#b5651d','#4a6fa5','#9c3848','#2f8f6b','#a06cd5',
  '#c99a2e','#6b7280','#0e7490','#be123c'];
const colorOf = (i)=>IND_COLORS[i%IND_COLORS.length];

// —— 数值格式化 ——
function yi(v){ // 元/份 -> 亿；上万亿转“万亿”
  if(v==null) return '—';
  const a=Math.abs(v);
  if(a>=1e12) return (v/1e12).toFixed(2)+'万亿';
  if(a>=1e8)  return (v/1e8).toFixed(1)+'亿';
  if(a>=1e4)  return (v/1e4).toFixed(1)+'万';
  return v.toFixed(0);
}
function pct(v){ return v==null?'—':(v).toFixed(2)+'%'; }
function signCls(v){ return v>0?'pos':(v<0?'neg':''); }
function signStr(v,fmt){ if(v==null||v===0) return fmt(v); return (v>0?'+':'')+fmt(v); }

let INDUSTRIES=[], META={}, sortKey='nt_value', sortDesc=true;

async function boot(){
  try{
    const [meta,industries]=await Promise.all([
      fetch('data/meta.json').then(r=>r.json()),
      fetch('data/industries.json').then(r=>r.json()),
    ]);
    INDUSTRIES=industries; META=meta;
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

function renderMeta(m){
  document.getElementById('meta').innerHTML=
    `<span>更新时间：${m.generated_at}</span>`+
    `<span>行情日：${m.trade_date||'—'}</span>`+
    `<span>持仓报告期：${m.report_date||'—'}</span>`;
}

function renderStats(m,inds){
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
  const top=[...inds].sort((a,b)=>(b.nt_value||0)-(a.nt_value||0)).slice(0,14).reverse();
  const el=echarts.init(document.getElementById('chart-industry'));
  el.setOption({
    grid:{left:8,right:24,top:10,bottom:10,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},
      formatter:p=>`${p[0].name}<br/>持有市值：<b>${yi(p[0].value)}元</b>`},
    xAxis:{type:'value',axisLabel:{formatter:v=>yi(v)},splitLine:{lineStyle:{color:'#eef1f6'}}},
    yAxis:{type:'category',data:top.map(d=>d.industry),axisLabel:{fontSize:12}},
    series:[{type:'bar',data:top.map((d,i)=>({value:d.nt_value||0,
      itemStyle:{color:colorOf(inds.indexOf(d)),borderRadius:[0,4,4,0]}})),
      barMaxWidth:20}]
  });
  window.addEventListener('resize',()=>el.resize());
}

function renderGroupChart(m,inds){
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
      data:data.map((d,i)=>({...d,itemStyle:{color:colorOf(i)}}))}]
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
  const rows=[...INDUSTRIES].sort((a,b)=>{
    const x=a[sortKey]??-Infinity,y=b[sortKey]??-Infinity;
    return sortDesc?(y-x):(x-y);
  });
  const tb=document.querySelector('#industry-table tbody');
  tb.innerHTML='';
  rows.forEach(b=>{
    const ci=INDUSTRIES.indexOf(b);
    const groups=Object.entries(b.groups||{}).slice(0,3)
      .map(([k,v])=>`${k} ${yi(v)}`).join(' · ');
    const chg=b.amount_change, chgpct=b.amount_change_pct;
    const tr=document.createElement('tr');
    tr.className='ind-row';
    tr.innerHTML=
      `<td class="ta-l"><span class="ind-name"><span class="dot" style="background:${colorOf(ci)}"></span>${b.industry}`+
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
