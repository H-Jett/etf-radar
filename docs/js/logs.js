/* ===== 采集日志：按月翻页浏览全部运行记录 ===== */
// yi 见 js/util.js；MAP 为日志页专用状态标签
const MAP={ok:['✅','正常','ok'],warning:['⚠️','警告','warn'],error:['❌','异常','err']};
let MONTHS=[], CUR=0;

async function boot(){
  let idx;
  try{ idx=await fetch('data/runs/index.json').then(r=>r.json()); }
  catch(e){ document.querySelector('#log-table tbody').innerHTML=
    '<tr><td colspan="6" class="loading">暂无日志（等首次 GitHub Actions 运行后生成）</td></tr>'; return; }
  MONTHS=idx.months||[];
  document.getElementById('log-total').textContent='共 '+(idx.total||0)+' 条运行记录 · '+MONTHS.length+' 个月';
  const sel=document.getElementById('month-select');
  sel.innerHTML=MONTHS.map((m,i)=>`<option value="${i}">${m}</option>`).join('');
  sel.onchange=()=>{ CUR=+sel.value; load(); };
  document.getElementById('prev-month').onclick=()=>{ if(CUR<MONTHS.length-1){CUR++;sync();} };
  document.getElementById('next-month').onclick=()=>{ if(CUR>0){CUR--;sync();} };
  load();
}
function sync(){ document.getElementById('month-select').value=CUR; load(); }

async function load(){
  const m=MONTHS[CUR];
  document.getElementById('prev-month').disabled=CUR>=MONTHS.length-1;
  document.getElementById('next-month').disabled=CUR<=0;
  let rows;
  try{ rows=await fetch(`data/runs/${m}.json`).then(r=>r.json()); }
  catch(e){ rows=[]; }
  rows=(rows||[]).slice().reverse();   // 最新在上
  const tb=document.querySelector('#log-table tbody');
  tb.innerHTML=rows.map(L=>{
    const c=MAP[L.status]||MAP.ok;
    const st=L.stats||{};
    const facts=[
      st.price_total?`收盘价 ${st.price_ok}/${st.price_total}`:'',
      st.share_days_added!=null?`份额+${st.share_days_added}天`:'',
      (st.deep_found!=null)?`历史ETF ${st.deep_found}`:'',
      L.num_nt_etfs!=null?`国家队ETF ${L.num_nt_etfs}`:'',
      L.report_rescan?'重扫持有人':'',
      st.no_new_trading_day?'非交易日':'',
      L.duration_sec!=null?`${L.duration_sec}s`:'',
    ].filter(Boolean).join(' · ');
    const msgs=[...(L.errors||[]),...(L.warnings||[])].join('；')||'—';
    const modeTxt={init:'初始化',daily:'每日',deep:'深度回补'}[L.mode]||L.mode||'—';
    return `<tr>
      <td class="ta-l">${L.run_at||'—'}</td>
      <td class="ta-l rs-${c[2]}">${c[0]} ${c[1]}</td>
      <td>${modeTxt}</td>
      <td>${L.trade_date||'—'}</td>
      <td class="ta-l muted">${facts||'—'}</td>
      <td class="ta-l ${(L.errors&&L.errors.length)?'rs-err':((L.warnings&&L.warnings.length)?'rs-warn':'muted')}">${msgs}</td>
    </tr>`;
  }).join('')||'<tr><td colspan="6" class="muted">本月无记录</td></tr>';
}

boot();
