/* ===== 个股总览：国家队直接持股 行业排行 ===== */
let INDUSTRIES=[], META={}, sortKey='mv', sortDesc=true;

async function boot(){
  try{
    const [meta,industries]=await Promise.all([
      fetch('data/stock/meta.json').then(r=>r.json()),
      fetch('data/stock/industries.json').then(r=>r.json()),
    ]);
    INDUSTRIES=industries; META=meta; setIndustryOrder(meta.industry_order||[]);
    const hint=document.querySelector('.table-head .hint');
    if(hint) hint.innerHTML=`持仓口径：报告期 <b>${meta.report_date||'—'}</b>（十大流通股东季度披露）· 点击任意行展开成员个股`;
    document.getElementById('meta').innerHTML=
      `<span>更新时间：${meta.generated_at}</span><span>报告期：${meta.report_date||'—'}</span>`;
    renderStats(meta); renderFundStats(meta); renderIndustryChart(); renderGroupChart(); renderTable(); bindSort();
  }catch(e){
    document.querySelector('main').innerHTML='<div class="loading">个股数据尚未生成：'+e+'</div>';
  }
}
function renderStats(m){
  const cards=[['国家队重仓个股',m.num_stocks,'只'],['覆盖行业',m.num_industries,'个'],
    ['国家队持股总市值',yi(m.total_mv),''],['报告期',m.report_date||'—','']];
  document.getElementById('stats').innerHTML=cards.map(c=>
    `<div class="stat"><div class="k">${c[0]}</div><div class="v">${c[1]}<small>${c[2]}</small></div></div>`).join('');
}
function renderFundStats(m){
  const tb=document.querySelector('#fund-table tbody'); if(!tb) return;
  const el=document.getElementById('fund-rpt'); if(el) el.textContent=m.report_date||'—';
  const gs=m.nt_group_stats||[];
  const chg=v=>v==null?'—':`<span class="${v>0?'pos':(v<0?'neg':'')}">${v>0?'+':''}${yi(v)}</span>`;
  tb.innerHTML=gs.map(g=>
    `<tr class="nt-row"><td class="ta-l nt">${g.group}</td><td><b>${yi(g.mv)}</b></td>`+
    `<td>${pct(g.ratio)}</td><td>${g.num_stocks} 只</td><td>${chg(g.mv_change)}</td></tr>`).join('')
    +`<tr class="total-row"><td class="ta-l"><b>合计</b></td><td><b>${yi(m.total_mv)}</b></td>`+
     `<td><b>100.00%</b></td><td></td><td></td></tr>`;
}
function renderIndustryChart(){
  const top=[...INDUSTRIES].sort((a,b)=>b.mv-a.mv).slice(0,14).reverse();
  const el=echarts.init(document.getElementById('chart-industry'));
  el.setOption({grid:{left:8,right:24,top:10,bottom:10,containLabel:true},
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},formatter:p=>`${p[0].name}<br/>持股市值：<b>${yi(p[0].value)}元</b>`},
    xAxis:{type:'value',axisLabel:{formatter:v=>yi(v)},splitLine:{lineStyle:{color:'#eef1f6'}}},
    yAxis:{type:'category',data:top.map(d=>d.industry),axisLabel:{fontSize:12}},
    series:[{type:'bar',barMaxWidth:20,data:top.map(d=>({value:d.mv,itemStyle:{color:colorOf(d.industry),borderRadius:[0,4,4,0]}}))}]});
  window.addEventListener('resize',()=>el.resize());
}
function renderGroupChart(){
  const g={}; INDUSTRIES.forEach(b=>{for(const[k,v]of Object.entries(b.groups||{}))g[k]=(g[k]||0)+v;});
  const data=Object.entries(g).map(([k,v])=>({name:k,value:v})).sort((a,b)=>b.value-a.value);
  const el=echarts.init(document.getElementById('chart-group'));
  el.setOption({tooltip:{trigger:'item',formatter:p=>`${p.name}<br/>持股市值：<b>${yi(p.value)}元</b>（${p.percent}%）`},
    legend:{bottom:0,type:'scroll',textStyle:{fontSize:11}},
    series:[{type:'pie',radius:['42%','68%'],center:['50%','44%'],itemStyle:{borderColor:'#fff',borderWidth:2},label:{show:false},
      data:data.map((d,i)=>({...d,itemStyle:{color:colorAt(i)}}))}]});
  window.addEventListener('resize',()=>el.resize());
}
function bindSort(){
  document.querySelectorAll('th[data-sort]').forEach(th=>th.addEventListener('click',()=>{
    const k=th.dataset.sort;
    if(sortKey===k) sortDesc=!sortDesc; else{sortKey=k;sortDesc=true;}
    document.querySelectorAll('th').forEach(x=>x.classList.remove('active','asc','desc'));
    th.classList.add('active',sortDesc?'desc':'asc'); renderTable();
  }));
}
function renderTable(){
  const rows=[...INDUSTRIES].sort((a,b)=>{const x=a[sortKey]??-Infinity,y=b[sortKey]??-Infinity;return sortDesc?(y-x):(x-y);});
  const tb=document.querySelector('#industry-table tbody'); tb.innerHTML='';
  rows.forEach(b=>{
    const groups=Object.entries(b.groups||{}).slice(0,3).map(([k,v])=>`${k} ${yi(v)}`).join(' · ');
    const chg=b.mv_change, pct2=b.mv_change_pct;
    const tr=document.createElement('tr'); tr.className='ind-row';
    tr.innerHTML=`<td class="ta-l"><span class="ind-name"><span class="dot" style="background:${colorOf(b.industry)}"></span>${b.industry}`+
      `${b.new_entries?`<span class="chip new">新进 ${b.new_entries}</span>`:''}</span></td>`+
      `<td>${b.num_stocks}</td><td><b>${yi(b.mv)}</b></td><td>${pct(b.ratio)}</td>`+
      `<td class="${chg>0?'pos':(chg<0?'neg':'')}">${chg>0?'+':''}${yi(chg)}`+
      `${pct2!=null?` <span class="groups-mini">(${chg>0?'+':''}${(pct2*100).toFixed(1)}%)</span>`:''}</td>`+
      `<td class="ta-l groups-mini">${groups||'—'}</td>`;
    tb.appendChild(tr);
    const dr=document.createElement('tr'); dr.className='detail-row'; dr.style.display='none';
    dr.innerHTML=`<td colspan="6"><div class="detail-inner">${stockListHtml(b)}</div></td>`;
    tb.appendChild(dr);
    tr.addEventListener('click',()=>{dr.style.display=dr.style.display==='none'?'':'none';});
  });
}
function stockListHtml(b){
  const rows=b.stocks.map(s=>
    `<tr><td class="ta-l"><a href="stock_detail.html?ind=${encodeURIComponent(b.industry)}&code=${s.code}">${s.name}</a>`+
    `${s.is_new?'<span class="tag">新进</span>':''}</td><td class="ta-l muted">${s.code}</td>`+
    `<td>${yi(s.mv)}</td><td>${pct(s.ratio)}</td></tr>`).join('');
  return `<table class="etf-list"><thead><tr><th class="ta-l">个股</th><th class="ta-l">代码</th><th>持股市值</th><th>国家队占比</th></tr></thead><tbody>${rows}</tbody></table>`;
}
boot();
