/* ===== 行业走势：总份额(日频 日/周/月) + 国家队持仓份额/占比(报告期) ===== */
// PALETTE / colorOf / yi / pct / LS / bucketKey 见 js/util.js（四页共用）
let CHART, METRIC='share', PERIOD=LS.period;
let DAILY=null, PERIODS=null;   // 缓存
let LEGEND_SEL=null;            // 图例选择状态(跨周期/指标保持一致)

async function boot(){
  CHART=echarts.init(document.getElementById('chart'));
  window.addEventListener('resize',()=>CHART.resize());
  bindSeg('metric-seg','m',v=>{METRIC=v; onMetric(); render();});
  bindSeg('period-seg','p',v=>{PERIOD=v; LS.period=v; render();});
  document.getElementById('btn-all').onclick=()=>toggleAll(true);
  document.getElementById('btn-none').onclick=()=>toggleAll(false);
  // 恢复已保存的周期按钮高亮
  document.querySelectorAll('#period-seg button').forEach(b=>
    b.classList.toggle('active', b.dataset.p===PERIOD));
  try{
    const meta=await fetch('data/meta.json').then(r=>r.json());
    setIndustryOrder(meta.industry_order||[]);   // 供 colorOf 稳定取色
    await Promise.all([loadDaily(meta), loadPeriods()]);
    onMetric(); render();
  }catch(e){
    document.getElementById('chart').innerHTML='<div class="loading">数据尚未生成或仍在采集中：'+e+'</div>';
  }
}

function bindSeg(id,attr,cb){
  document.querySelectorAll('#'+id+' button').forEach(b=>{
    b.addEventListener('click',()=>{
      document.querySelectorAll('#'+id+' button').forEach(x=>x.classList.remove('active'));
      b.classList.add('active'); cb(b.dataset[attr]);
    });
  });
}

async function loadDaily(meta){
  const years=(meta.series_years||[]).slice().sort();
  const parts=await Promise.all(years.map(y=>
    fetch('data/trends/'+y+'.json').then(r=>r.json()).catch(()=>null)));
  const dates=[]; const inds={};
  parts.filter(Boolean).forEach(p=>{
    p.dates.forEach(d=>dates.push(d));
    for(const[ind,s]of Object.entries(p.industries)){
      (inds[ind]=inds[ind]||[]).push(...s.total_share);
    }
  });
  DAILY={dates,industries:inds};
}
async function loadPeriods(){
  PERIODS=await fetch('data/holders/periods.json').then(r=>r.json()).catch(()=>({periods:[],industries:{}}));
}

function onMetric(){
  const showPeriod=METRIC==='share';
  document.getElementById('period-seg').style.visibility=showPeriod?'visible':'hidden';
  document.getElementById('metric-note').textContent=
    METRIC==='share' ? '行业成员 ETF 每日总份额之和（日频，可切换日/周/月，拖动查看时段）'
    : METRIC==='ratio' ? '国家队持有份额 ÷ 报告期行业 ETF 总份额（份额加权）· 半年一个点'
    : '国家队在该行业各 ETF 的持有份额合计 · 半年一个点';
}

// —— 周期聚合（取桶内最后一个值）；bucketKey 见 util.js ——
function aggSeries(dates,vals,p){
  const m=new Map();
  dates.forEach((d,i)=>{ if(vals[i]!=null) m.set(bucketKey(d,p),[d,vals[i]]); });
  const keys=[...m.keys()].sort();
  return {labels:keys.map(k=>m.get(k)[0]), vals:keys.map(k=>m.get(k)[1])};
}

function currentData(){
  if(METRIC==='share'){
    const out={x:[],inds:{},unit:'份',fmt:yi};
    // 统一桶轴：所有行业共用同一条时间轴（从全部日期建桶，取每桶最后一个交易日为代表），
    // 各行业按桶映射，未上市时段为 null（connectNulls）。避免各行业各自聚合导致 X 轴错位。
    const bm=new Map();
    DAILY.dates.forEach((d,i)=>bm.set(bucketKey(d,PERIOD),i)); // 同桶后者覆盖 → 桶内最后一日
    const keys=[...bm.keys()].sort();
    const repIdx=keys.map(k=>bm.get(k));
    out.x=repIdx.map(i=>DAILY.dates[i]);
    for(const[ind,vals]of Object.entries(DAILY.industries)){
      out.inds[ind]=repIdx.map(i=>(vals[i]==null?null:vals[i]));
    }
    return out;
  }
  const key=METRIC==='ratio'?'nt_ratio':'nt_amount';
  const out={x:PERIODS.periods,inds:{},unit:METRIC==='ratio'?'%':'份',
             fmt:METRIC==='ratio'?pct:yi};
  for(const[ind,s]of Object.entries(PERIODS.industries)){
    out.inds[ind]=s[key];
  }
  return out;
}

function latestVal(arr){ for(let i=arr.length-1;i>=0;i--) if(arr[i]!=null) return arr[i]; return -Infinity; }

function render(){
  const D=currentData();
  const order=Object.keys(D.inds).sort((a,b)=>latestVal(D.inds[b])-latestVal(D.inds[a]));
  const colorFor={}; order.forEach(ind=>colorFor[ind]=colorOf(ind));
  // 图例选择持久化:首次出现的行业默认显示前8;已有选择保持不变(切日/周/月不重置)
  if(!LEGEND_SEL) LEGEND_SEL={};
  order.forEach((ind,i)=>{ if(!(ind in LEGEND_SEL)) LEGEND_SEL[ind]=i<8; });

  const series=order.map(ind=>({
    name:ind, type:'line', smooth:METRIC!=='share'?false:true, showSymbol:METRIC!=='share',
    symbolSize:5, connectNulls:true, sampling:'lttb', lineStyle:{width:2,color:colorFor[ind]},
    itemStyle:{color:colorFor[ind]}, data:D.inds[ind],
  }));
  const z=LS.zoom;   // 恢复用户上次拖动的时间段（与详情页共享）
  const zStart=z?z.start:(D.x.length>60?Math.round((1-60/D.x.length)*100):0);
  const zEnd=z?z.end:100;
  CHART.setOption({
    animationDuration:400,
    grid:{left:60,right:24,top:48,bottom:70},
    legend:{type:'scroll',top:8,data:order,selected:LEGEND_SEL,textStyle:{fontSize:11}},
    tooltip:{trigger:'axis',
      formatter:ps=>{let s=ps[0].axisValue+'<br/>';
        ps.filter(p=>p.value!=null).sort((a,b)=>b.value-a.value).slice(0,12).forEach(p=>{
          s+=`${p.marker}${p.seriesName}：<b>${METRIC==='ratio'?pct(p.value):yi(p.value)+D.unit}</b><br/>`;});
        return s;}},
    xAxis:{type:'category',data:D.x,boundaryGap:false,axisLabel:{fontSize:11}},
    yAxis:{type:'value',scale:METRIC!=='share',
      axisLabel:{fontSize:11,formatter:v=>METRIC==='ratio'?v+'%':yi(v)},
      splitLine:{lineStyle:{color:'#eef1f6'}}},
    dataZoom:[{type:'slider',start:zStart,end:zEnd,height:20,bottom:28},
              {type:'inside',start:zStart,end:zEnd}],
    series,
  },true);
  CHART.off('datazoom');
  CHART.on('datazoom',()=>{                     // 保存拖动的时间段(与详情页共享)
    const dz=(CHART.getOption().dataZoom||[])[0];
    if(dz) LS.zoom={start:dz.start,end:dz.end};
  });
  CHART.off('legendselectchanged');
  CHART.on('legendselectchanged',p=>{LEGEND_SEL=p.selected;});  // 记住勾选
  renderTable(D,order,colorFor);
}

function toggleAll(on){
  if(!LEGEND_SEL) return;
  Object.keys(LEGEND_SEL).forEach(k=>LEGEND_SEL[k]=on);
  CHART.setOption({legend:{selected:LEGEND_SEL}});
}

function renderTable(D,order,colorFor){
  // 取最近 ~14 个时间桶为列（最新在右）
  const n=D.x.length, take=Math.min(14,n);
  const cols=[]; for(let i=n-take;i<n;i++) cols.push(i);
  const thead=document.querySelector('#trend-table thead');
  thead.innerHTML='<tr><th class="ta-l">行业</th>'+
    cols.map(i=>`<th>${D.x[i]}</th>`).join('')+'<th>区间变化</th></tr>';
  const tb=document.querySelector('#trend-table tbody'); tb.innerHTML='';
  // 汇总行：份额/份数可跨行业相加 → 合计；占比不可加 → 显示 —
  if(METRIC!=='ratio'){
    const sums=cols.map(i=>{let t=0,has=false;order.forEach(ind=>{const v=D.inds[ind][i];if(v!=null){t+=v;has=true;}});return has?t:null;});
    const f=sums[0],l=sums[sums.length-1];
    let chg='—'; if(f!=null&&l!=null&&f){const d=(l-f)/f*100;chg=`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(1)}%</span>`;}
    tb.innerHTML+=`<tr class="total-row"><td class="ta-l"><b>合计（全部行业）</b></td>`+
      sums.map(v=>`<td><b>${v==null?'—':yi(v)}</b></td>`).join('')+`<td><b>${chg}</b></td></tr>`;
  }
  order.forEach(ind=>{
    const vals=D.inds[ind];
    const first=vals[cols[0]], last=vals[cols[cols.length-1]];
    let chg='—';
    if(first!=null&&last!=null){
      if(METRIC==='ratio'){const d=last-first;chg=`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(2)}pt</span>`;}
      else{const d=first?(last-first)/first*100:null; chg=d==null?'—':`<span class="${d>0?'pos':(d<0?'neg':'')}">${d>0?'+':''}${d.toFixed(1)}%</span>`;}
    }
    tb.innerHTML+=`<tr><td class="ta-l"><span class="ind-name"><span class="dot" style="background:${colorFor[ind]}"></span>${ind}</span></td>`+
      cols.map(i=>`<td>${vals[i]==null?'—':(METRIC==='ratio'?pct(vals[i]):yi(vals[i]))}</td>`).join('')+
      `<td>${chg}</td></tr>`;
  });
  document.getElementById('table-hint').textContent=
    (METRIC==='ratio'?'占比(%)':(METRIC==='share'?'总份额':'国家队持仓份额'))+
    ' · 最近 '+take+' 个'+(METRIC==='share'?({D:'交易日',W:'周',M:'月'}[PERIOD]):'报告期');
}

boot();
