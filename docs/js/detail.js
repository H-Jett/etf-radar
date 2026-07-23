/* ===== 详情页：价格/份额走势（日/周/月 + 拖动）+ ETF 切换 + 持有人 ===== */
function yi(v){ if(v==null)return'—'; const a=Math.abs(v);
  if(a>=1e12)return(v/1e12).toFixed(2)+'万亿'; if(a>=1e8)return(v/1e8).toFixed(1)+'亿';
  if(a>=1e4)return(v/1e4).toFixed(1)+'万'; return(+v).toFixed(0);}
function pct(v){return v==null?'—':(+v).toFixed(2)+'%';}
const qs=new URLSearchParams(location.search);

let CHART, PERIOD='D', ETFS=[], CUR=null;

async function boot(){
  const [etfs,meta]=await Promise.all([
    fetch('data/etfs.json').then(r=>r.json()),
    fetch('data/meta.json').then(r=>r.json()).catch(()=>({})),
  ]);
  ETFS=etfs;
  buildSelect(etfs);
  const code=qs.get('code')||etfs[0].code;
  document.getElementById('etf-select').value=code;
  bindPeriod();
  CHART=echarts.init(document.getElementById('chart'));
  window.addEventListener('resize',()=>CHART.resize());
  await load(code);
}

function buildSelect(etfs){
  const sel=document.getElementById('etf-select');
  const byInd={};
  etfs.forEach(e=>(byInd[e.industry]=byInd[e.industry]||[]).push(e));
  const order=Object.keys(byInd).sort((a,b)=>byInd[b].length-byInd[a].length);
  sel.innerHTML=order.map(ind=>
    `<optgroup label="${ind}">`+
    byInd[ind].sort((a,b)=>(b.nt_value||0)-(a.nt_value||0)).map(e=>
      `<option value="${e.code}">${e.name}（${e.code}）· 国家队${pct(e.nt_ratio)}</option>`
    ).join('')+`</optgroup>`).join('');
  sel.addEventListener('change',()=>{
    const c=sel.value;
    history.replaceState(null,'','detail.html?code='+c);
    load(c);
  });
}

function bindPeriod(){
  document.querySelectorAll('#period-seg button').forEach(b=>{
    b.addEventListener('click',()=>{
      document.querySelectorAll('#period-seg button').forEach(x=>x.classList.remove('active'));
      b.classList.add('active'); PERIOD=b.dataset.p; renderChart();
    });
  });
}

async function load(code){
  CUR=ETFS.find(e=>e.code===code);
  const px=await fetch(`data/prices/${code}.json`).then(r=>r.json()).catch(()=>({prices:[],shares:[]}));
  CUR._px=px;
  renderHead(); renderChart(); renderHolders();
}

function renderHead(){
  const e=CUR;
  document.getElementById('detail-head').innerHTML=
    `<b style="font-size:19px">${e.name}</b>`+
    `<span class="code">${e.code}</span>`+
    `<span class="badge">${e.industry}</span>`+
    (e.index_name?`<span class="badge">${e.index_name}</span>`:'')+
    `<span class="badge nt">国家队持有 ${pct(e.nt_ratio)}</span>`+
    `<span class="badge">持有市值 ${yi(e.nt_value)}</span>`+
    `<span class="badge">今日份额 ${yi(e.total_share)}份</span>`;
  document.getElementById('rpt').textContent=e.report_date||'—';
}

// —— 按周期聚合：取每个桶内最后一个值 ——
function bucketKey(dateStr,p){
  const d=new Date(dateStr);
  if(p==='M') return dateStr.slice(0,7);
  if(p==='W'){ // ISO 周
    const t=new Date(d); t.setHours(0,0,0,0);
    t.setDate(t.getDate()+3-((t.getDay()+6)%7));
    const wk1=new Date(t.getFullYear(),0,4);
    const wn=1+Math.round(((t-wk1)/86400000-3+((wk1.getDay()+6)%7))/7);
    return t.getFullYear()+'-W'+String(wn).padStart(2,'0');
  }
  return dateStr;
}
function aggregate(series,p){
  if(!series||!series.length) return {x:[],v:[]};
  const m=new Map(); // key -> [label(last date), value(last)]
  series.forEach(([date,val])=>{ m.set(bucketKey(date,p),[date,val]); });
  const keys=[...m.keys()].sort();
  return {x:keys.map(k=>m.get(k)[0]), v:keys.map(k=>m.get(k)[1]), keys};
}

function renderChart(){
  const px=CUR._px||{};
  const price=aggregate(px.prices,PERIOD);
  const share=aggregate(px.shares,PERIOD);
  // 以价格日期为主轴，份额对齐到同一桶
  const shareMap=new Map();
  (share.keys||[]).forEach((k,i)=>shareMap.set(k,share.v[i]));
  const xkeys=price.keys&&price.keys.length?price.keys:(share.keys||[]);
  const xLabels=xkeys.map(k=>{ // 用桶内代表日期做标签
    return k;
  });
  const priceMap=new Map();(price.keys||[]).forEach((k,i)=>priceMap.set(k,price.v[i]));
  const priceVals=xkeys.map(k=>priceMap.has(k)?priceMap.get(k):null);
  const shareVals=xkeys.map(k=>shareMap.has(k)?shareMap.get(k)/1e8:null); // 亿份

  const startPct=xkeys.length>60?Math.round((1-60/xkeys.length)*100):0;
  CHART.setOption({
    animationDuration:400,
    grid:{left:52,right:60,top:24,bottom:64},
    legend:{data:['单位净值','总份额'],top:0,right:0},
    tooltip:{trigger:'axis',
      formatter:ps=>{let s=ps[0].axisValue+'<br/>';
        ps.forEach(p=>{const val=p.seriesName==='总份额'?(p.value!=null?p.value.toFixed(2)+'亿份':'—'):(p.value!=null?p.value:'—');
          s+=`${p.marker}${p.seriesName}：<b>${val}</b><br/>`;});return s;}},
    xAxis:{type:'category',data:xLabels,boundaryGap:PERIOD!=='D',
      axisLabel:{fontSize:11},axisTick:{alignWithLabel:true}},
    yAxis:[
      {type:'value',name:'净值',scale:true,splitLine:{lineStyle:{color:'#eef1f6'}},
       axisLabel:{fontSize:11}},
      {type:'value',name:'份额(亿)',position:'right',splitLine:{show:false},
       axisLabel:{fontSize:11,formatter:v=>v}},
    ],
    dataZoom:[
      {type:'slider',start:startPct,end:100,height:20,bottom:24},
      {type:'inside',start:startPct,end:100},
    ],
    series:[
      {name:'单位净值',type:'line',smooth:true,showSymbol:false,yAxisIndex:0,
       lineStyle:{width:2,color:'#c8102e'},itemStyle:{color:'#c8102e'},
       areaStyle:{color:'rgba(200,16,46,.06)'},connectNulls:true,data:priceVals},
      {name:'总份额',type:'bar',yAxisIndex:1,itemStyle:{color:'rgba(43,108,176,.55)'},
       barMaxWidth:14,data:shareVals},
    ],
  },true);
}

function renderHolders(){
  const e=CUR;
  const nt=document.querySelector('#nt-holders tbody');
  nt.innerHTML=(e.nt_holders||[]).map(h=>{
    const d=h.delta_ratio;
    const dtxt=d==null?'<span class="tag">新进</span>':
      `<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(2)}pt</span>`;
    return `<tr class="nt-row">
      <td class="ta-l nt">${h.name}</td>
      <td class="ta-l">${h.group||'—'}</td>
      <td>${yi(h.amount)}份</td>
      <td>${pct(h.ratio)}</td>
      <td>${h.prev_ratio==null?'—':pct(h.prev_ratio)}</td>
      <td>${dtxt}</td></tr>`;
  }).join('')||'<tr><td colspan="6" class="muted">无</td></tr>';

  const all=document.querySelector('#all-holders tbody');
  all.innerHTML=(e.all_holders||[]).map(h=>
    `<tr class="${h.is_nt?'nt-row':''}">
      <td>${h.rank}</td>
      <td class="ta-l ${h.is_nt?'nt':''}">${h.name}${h.is_nt?' <span class="tag">国家队</span>':''}</td>
      <td>${yi(h.amount)}份</td>
      <td>${pct(h.ratio)}</td></tr>`
  ).join('')||'<tr><td colspan="4" class="muted">无</td></tr>';
}

boot();
