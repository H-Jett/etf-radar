/* ===== 行业详情：成员 ETF 份额多线 + 总量线 + 图例勾选；持有人下拉切换 ===== */
const IND_COLORS=['#2b6cb0','#e07b39','#1a9e5f','#8a56c2','#d4a017','#3aa0a0',
  '#c2506e','#5b8c2a','#b5651d','#4a6fa5','#9c3848','#2f8f6b','#a06cd5','#0e7490',
  '#be123c','#7c3aed','#0891b2','#6b7280','#c99a2e','#5b21b6'];
function yi(v){ if(v==null)return'—'; const a=Math.abs(v);
  if(a>=1e12)return(v/1e12).toFixed(2)+'万亿'; if(a>=1e8)return(v/1e8).toFixed(1)+'亿';
  if(a>=1e4)return(v/1e4).toFixed(1)+'万'; return(+v).toFixed(0);}
function pct(v){return v==null?'—':(+v).toFixed(2)+'%';}
const qs=new URLSearchParams(location.search);

let CHART, PERIOD='D', INDEX=null, ETFMAP={}, CUR=null, PREFER_CODE=null;

async function boot(){
  CHART=echarts.init(document.getElementById('chart'));
  window.addEventListener('resize',()=>CHART.resize());
  bindPeriod();
  document.getElementById('btn-all').onclick=()=>toggleAll(true);
  document.getElementById('btn-none').onclick=()=>toggleAll(false);
  try{
    const [index,etfs]=await Promise.all([
      fetch('data/industry/index.json').then(r=>r.json()),
      fetch('data/etfs.json').then(r=>r.json()),
    ]);
    INDEX=index; etfs.forEach(e=>ETFMAP[e.code]=e);
    buildIndSelect();
    const wantCode=qs.get('code');
    const want=(wantCode&&ETFMAP[wantCode])?ETFMAP[wantCode].industry:qs.get('ind');
    const hit=index.industries.find(x=>x.name===want)||index.industries[0];
    document.getElementById('ind-select').value=String(hit.id);
    PREFER_CODE=(wantCode&&ETFMAP[wantCode])?wantCode:null;
    await loadIndustry(hit);
  }catch(e){
    document.getElementById('chart').innerHTML='<div class="loading">数据尚未生成或仍在采集中：'+e+'</div>';
  }
}

function buildIndSelect(){
  const sel=document.getElementById('ind-select');
  sel.innerHTML=INDEX.industries.map(x=>
    `<option value="${x.id}">${x.name}（${x.num_etfs} 只 ETF）</option>`).join('');
  sel.onchange=()=>{
    const x=INDEX.industries.find(i=>String(i.id)===sel.value);
    history.replaceState(null,'','detail.html?ind='+encodeURIComponent(x.name));
    loadIndustry(x);
  };
}
function bindPeriod(){
  document.querySelectorAll('#period-seg button').forEach(b=>b.onclick=()=>{
    document.querySelectorAll('#period-seg button').forEach(x=>x.classList.remove('active'));
    b.classList.add('active'); PERIOD=b.dataset.p; renderChart();
  });
}

async function loadIndustry(ind){
  LEGEND_SEL=null;   // 换行业重置图例勾选
  const parts=await Promise.all((ind.years||[]).map(y=>
    fetch(`data/industry/${ind.id}/${y}.json`).then(r=>r.json()).catch(()=>null)));
  const dates=[], total=[], etfMap={};
  parts.filter(Boolean).forEach(p=>{
    p.dates.forEach(d=>dates.push(d));
    p.total.forEach(v=>total.push(v));
    p.etfs.forEach(e=>{
      const cur=etfMap[e.code]||(etfMap[e.code]={code:e.code,name:e.name,shares:[]});
      cur.shares.push(...e.shares);
    });
  });
  CUR={ind, dates, total, etfs:Object.values(etfMap)};
  renderHead(); renderChart(); buildHolderSelect();
}

function renderHead(){
  const b=INDEX.industries.find(i=>i.id===CUR.ind.id);
  const last=[...CUR.total].reverse().find(v=>v!=null);
  document.getElementById('ind-head').innerHTML=
    `<b style="font-size:19px">${CUR.ind.name}</b>`+
    `<span class="badge">${b.num_etfs} 只成员 ETF</span>`+
    `<span class="badge">最新总份额 ${yi(last)}份</span>`+
    `<span class="badge">数据始于 ${CUR.dates[0]||'—'}</span>`;
}

// —— 周期分桶：返回每桶最后一个交易日的索引 ——
function bucketKey(d,p){
  if(p==='M')return d.slice(0,7);
  if(p==='W'){const t=new Date(d);t.setHours(0,0,0,0);t.setDate(t.getDate()+3-((t.getDay()+6)%7));
    const w1=new Date(t.getFullYear(),0,4);
    const wn=1+Math.round(((t-w1)/864e5-3+((w1.getDay()+6)%7))/7);
    return t.getFullYear()+'-W'+String(wn).padStart(2,'0');}
  return d;
}
function bucketize(dates,p){
  const m=new Map(); dates.forEach((d,i)=>m.set(bucketKey(d,p),i));
  const keys=[...m.keys()].sort();
  return {labels:keys.map(k=>dates[m.get(k)]), idx:keys.map(k=>m.get(k))};
}

let LEGEND_SEL=null;
function renderChart(){
  const {labels,idx}=bucketize(CUR.dates,PERIOD);
  const pick=arr=>idx.map(i=>arr[i]==null?null:arr[i]/1e8);   // 亿份
  const etfs=[...CUR.etfs].sort((a,b)=>{
    const la=[...a.shares].reverse().find(v=>v!=null)||0, lb=[...b.shares].reverse().find(v=>v!=null)||0;
    return lb-la;
  });
  const names=['总量',...etfs.map(e=>`${e.name}(${e.code})`)];
  if(!LEGEND_SEL){ LEGEND_SEL={}; names.forEach(n=>LEGEND_SEL[n]=true); }
  const series=[
    {name:'总量',type:'line',smooth:true,showSymbol:false,z:10,sampling:'lttb',
     lineStyle:{width:3,color:'#c8102e'},itemStyle:{color:'#c8102e'},
     areaStyle:{color:'rgba(200,16,46,.05)'},connectNulls:true,data:pick(CUR.total)},
    ...etfs.map((e,i)=>({name:`${e.name}(${e.code})`,type:'line',smooth:true,
     showSymbol:false,connectNulls:true,sampling:'lttb',
     lineStyle:{width:1.5,color:IND_COLORS[i%IND_COLORS.length]},
     itemStyle:{color:IND_COLORS[i%IND_COLORS.length]},data:pick(e.shares)})),
  ];
  const startPct=labels.length>90?Math.round((1-90/labels.length)*100):0;
  CHART.setOption({
    animationDuration:400,
    grid:{left:58,right:20,top:8,bottom:76},
    legend:{type:'scroll',bottom:44,data:names,selected:LEGEND_SEL,textStyle:{fontSize:11}},
    tooltip:{trigger:'axis',
      formatter:ps=>{let s=ps[0].axisValue+'<br/>';
        ps.filter(p=>p.value!=null).sort((a,b)=>b.value-a.value).slice(0,15).forEach(p=>{
          s+=`${p.marker}${p.seriesName}：<b>${p.value.toFixed(2)}亿份</b><br/>`;});return s;}},
    xAxis:{type:'category',data:labels,boundaryGap:false,axisLabel:{fontSize:11}},
    yAxis:{type:'value',name:'份额(亿)',scale:false,splitLine:{lineStyle:{color:'#eef1f6'}},
      axisLabel:{fontSize:11}},
    dataZoom:[{type:'slider',start:startPct,end:100,height:18,bottom:20},
              {type:'inside',start:startPct,end:100}],
    series,
  },true);
  CHART.off('legendselectchanged');
  CHART.on('legendselectchanged',p=>{LEGEND_SEL=p.selected;});
}
function toggleAll(on){
  if(!LEGEND_SEL)return;
  Object.keys(LEGEND_SEL).forEach(k=>LEGEND_SEL[k]=on|| k==='总量');
  CHART.setOption({legend:{selected:LEGEND_SEL}});
}

// —— 持有人下拉：切换行业内某只 ETF 的十大持有人 ——
function buildHolderSelect(){
  const sel=document.getElementById('holder-select');
  const opts=CUR.ind.codes.map(c=>ETFMAP[c]).filter(Boolean)
    .sort((a,b)=>(b.nt_value||0)-(a.nt_value||0));
  sel.innerHTML=opts.map(e=>`<option value="${e.code}">${e.name}（${e.code}）· 国家队${pct(e.nt_ratio)}</option>`).join('');
  sel.onchange=()=>renderHolders(sel.value);
  const def=(PREFER_CODE&&opts.some(e=>e.code===PREFER_CODE))?PREFER_CODE:(opts[0]&&opts[0].code);
  PREFER_CODE=null;
  if(def){ sel.value=def; renderHolders(def); }
}
function renderHolders(code){
  const e=ETFMAP[code]; if(!e)return;
  document.getElementById('holder-rpt').textContent='报告期 '+(e.report_date||'—');
  document.querySelector('#nt-holders tbody').innerHTML=(e.nt_holders||[]).map(h=>{
    const d=h.delta_ratio;
    const dt=d==null?'<span class="tag">新进</span>':`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(2)}pt</span>`;
    return `<tr class="nt-row"><td class="ta-l nt">${h.name}</td><td class="ta-l">${h.group||'—'}</td>
      <td>${yi(h.amount)}份</td><td>${pct(h.ratio)}</td>
      <td>${h.prev_ratio==null?'—':pct(h.prev_ratio)}</td><td>${dt}</td></tr>`;
  }).join('')||'<tr><td colspan="6" class="muted">无</td></tr>';
  document.querySelector('#all-holders tbody').innerHTML=(e.all_holders||[]).map(h=>
    `<tr class="${h.is_nt?'nt-row':''}"><td>${h.rank}</td>
      <td class="ta-l ${h.is_nt?'nt':''}">${h.name}${h.is_nt?' <span class="tag">国家队</span>':''}</td>
      <td>${yi(h.amount)}份</td><td>${pct(h.ratio)}</td></tr>`
  ).join('')||'<tr><td colspan="4" class="muted">无</td></tr>';
}

boot();
