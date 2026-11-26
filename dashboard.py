from __future__ import annotations

import csv
import getpass
import io
import json
import os
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import paramiko


ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = ROOT / "dashboard.local.json"
try:
    LOCAL_CONFIG = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    LOCAL_CONFIG = {}
CSV_PATH = ROOT / "sensor_readings.csv"
PENDING_CSV_PATH = ROOT / "sensor_readings.pending.csv"
ALERT_CSV_PATH = ROOT / "sensor_alerts.csv"
PENDING_ALERT_CSV_PATH = ROOT / "sensor_alerts.pending.csv"
PI_HOST = os.environ.get("AUTOHOME_PI_HOST") or LOCAL_CONFIG.get("host", "raspberrypi.local")
PI_USER = os.environ.get("AUTOHOME_PI_USER") or LOCAL_CONFIG.get("user", "pi")
REMOTE_PROJECT_DIR = os.environ.get("AUTOHOME_PI_PROJECT_DIR") or LOCAL_CONFIG.get("project_dir", f"/home/{PI_USER}/autohome")
REMOTE_STATUS = f"{REMOTE_PROJECT_DIR.rstrip('/')}/oled-demo-status.json"
POLL_SECONDS = 2
PORT = 8765
TEMP_THRESHOLD_C = 27.0
TEMP_REPEAT_SECONDS = 300
CSV_COLUMNS = [
    "timestamp_utc",
    "received_at_local",
    "temperature_c",
    "humidity_percent",
    "gas_raw",
    "gas_input",
    "dht_age_seconds",
    "dht_error",
    "frame",
    "status",
    "gas_concentration_calibrated",
]
ALERT_COLUMNS = [
    "event_id",
    "timestamp_utc",
    "received_at_local",
    "sensor",
    "event",
    "value",
    "threshold",
    "message",
]

STATE: dict[str, Any] = {
    "connected": False,
    "error": "Starting sensor connection",
    "csv_error": None,
    "alert_error": None,
    "sample": None,
    "received_at": None,
    "last_saved_epoch": None,
    "temperature_alert_active": False,
    "gas_alert_active": False,
    "last_temperature_alert_time": None,
}
STATE_LOCK = threading.Lock()
STOP = threading.Event()
ALERT_LOCK = threading.Lock()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0c111d">
  <title>AutoHome · Live sensors</title>
  <style>
    :root{color-scheme:dark;--bg:#0c111d;--panel:#141c2b;--panel2:#192437;--line:#29364b;--muted:#98a8bf;--text:#edf3fb;--cyan:#67e8f9;--green:#4ade80;--amber:#fbbf24;--red:#fb7185}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 15% -20%,#1c3850 0,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}
    .wrap{max-width:1180px;margin:auto;padding:36px 24px 54px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#164e63,#0e7490);display:grid;place-items:center;color:#a5f3fc;font-size:23px;box-shadow:0 8px 28px #0e749055}.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--cyan);font-weight:700}.title{font-size:23px;font-weight:700;letter-spacing:-.03em}.topright{display:flex;align-items:center;gap:14px}.pill{display:flex;align-items:center;gap:8px;border:1px solid var(--line);background:#121a28;padding:8px 12px;border-radius:999px;font-size:12px;color:var(--muted)}.dot{width:8px;height:8px;border-radius:50%;background:#64748b}.pill.live{color:#bbf7d0;border-color:#166534}.pill.live .dot{background:var(--green);box-shadow:0 0 12px #4ade80aa}.pill.stale{color:#fde68a;border-color:#854d0e}.pill.stale .dot{background:var(--amber)}.pill.offline{color:#fecdd3;border-color:#9f1239}.pill.offline .dot{background:var(--red)}a.button{display:inline-flex;align-items:center;gap:8px;color:var(--text);text-decoration:none;border:1px solid var(--line);border-radius:10px;padding:9px 13px;font-size:12px;background:#141c2b}a.button:hover{background:#202e43;border-color:#3e5474}
    .intro{margin-bottom:20px}.intro h1{font-size:30px;letter-spacing:-.045em;margin:0 0 4px}.intro p{margin:0;color:var(--muted);font-size:14px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{background:linear-gradient(145deg,#172132,#121a28);border:1px solid var(--line);border-radius:17px;padding:20px;min-height:174px;box-shadow:0 12px 36px #0002}.cardtop{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:13px}.icon{width:34px;height:34px;display:grid;place-items:center;border-radius:11px;background:#233248;font-size:17px}.value{font-size:46px;letter-spacing:-.06em;font-weight:650;margin:16px 0 0;line-height:1.05}.unit{font-size:18px;color:#a8b8ca;letter-spacing:0;font-weight:500}.sub{font-size:12px;color:var(--muted);margin-top:9px}.gasstate{display:flex;align-items:center;gap:10px;margin-top:20px}.gasbadge{font-weight:750;font-size:26px;letter-spacing:.04em}.gasbadge.idle{color:var(--green)}.gasbadge.active{color:var(--amber)}.gasbadge.unknown{color:var(--muted)}.gasled{width:12px;height:12px;border-radius:50%;background:#334155}.gasled.idle{background:var(--green);box-shadow:0 0 16px #4ade8066}.gasled.active{background:var(--amber);box-shadow:0 0 16px #fbbf2466}.section{margin-top:18px;display:grid;grid-template-columns:1.45fr 1fr;gap:16px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:17px;padding:18px 20px}.panelhead{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.panel h2{font-size:15px;margin:0}.panel p{color:var(--muted);font-size:12px;margin:3px 0 0}.legend{display:flex;gap:14px;color:var(--muted);font-size:11px}.legend span{display:flex;align-items:center;gap:6px}.key{width:8px;height:8px;border-radius:50%;background:var(--cyan)}.key.hum{background:#a78bfa}.chartbox{height:225px;margin-top:14px;position:relative}canvas{display:block;width:100%;height:100%}.history{display:flex;flex-direction:column;min-height:285px}.tablewrap{overflow:auto;margin-top:13px;max-height:230px}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th{text-align:left;color:var(--muted);font-weight:500;padding:9px 7px;border-bottom:1px solid var(--line)}td{padding:9px 7px;border-bottom:1px solid #202b3c}.gas-tag{font-size:10px;font-weight:700;border-radius:6px;padding:3px 6px;background:#123323;color:#86efac}.gas-tag.active{background:#42250c;color:#fcd34d}.empty{color:var(--muted);font-size:13px;padding:22px 4px}.note{margin-top:16px;padding:12px 14px;border:1px solid #3b4a61;background:#111a28;border-radius:12px;color:#a9b8cb;font-size:12px}.note strong{color:#dbeafe}.footer{display:flex;justify-content:space-between;gap:12px;color:#7889a1;font-size:11px;margin-top:15px}.error{color:#fda4af}.hidden{display:none}.alert-strip{margin:0 0 18px;border:1px solid #9a3412;background:linear-gradient(110deg,#431407,#271525);border-radius:14px;padding:15px 18px;box-shadow:0 10px 32px #0003}.alert-strip.gas-alert{border-color:#be123c;background:linear-gradient(110deg,#4c0519,#2b1426)}.alert-title{font-weight:800;color:#fff;font-size:15px}.alert-item{color:#ffedd5;margin-top:4px}.alert-strip.gas-alert .alert-item{color:#ffe4e6}.alert-help{font-size:11px;color:#f3c6b8;margin-top:5px}.alert-button{border:1px solid #3d536d;background:#172437;color:#e4f4ff;border-radius:10px;padding:9px 12px;font:600 12px system-ui;cursor:pointer}.alert-button:hover{background:#22364f}.alert-permission{font-size:11px;color:var(--muted)}.alert-event{font-size:10px;font-weight:700;border-radius:6px;padding:3px 6px;background:#26364d;color:#bdeaff;text-transform:uppercase}.alert-event.repeat{background:#42250c;color:#fcd34d}.alert-event.cleared{background:#123323;color:#86efac}
    @media(max-width:800px){.grid{grid-template-columns:1fr 1fr}.gas-card{grid-column:span 2}.section{grid-template-columns:1fr}.history{min-height:0}}
    @media(max-width:520px){.wrap{padding:22px 14px 36px}.top{align-items:flex-start}.topright{align-items:flex-end;flex-direction:column;gap:8px}.title{font-size:18px}.intro h1{font-size:25px}.grid{grid-template-columns:1fr}.gas-card{grid-column:auto}.card{min-height:155px}.value{font-size:42px}.footer{flex-direction:column}}
  </style>
</head>
<body>
<main class="wrap">
  <header class="top">
    <div class="brand"><div class="mark">⌂</div><div><div class="eyebrow">AutoHome · Sensor station</div><div class="title">Live dashboard</div></div></div>
    <div class="topright"><div id="connection" class="pill offline"><i class="dot"></i><span id="connectionText">CONNECTING</span></div><button id="enableAlerts" class="alert-button" type="button">Enable desktop alerts</button><a class="button" href="/download.csv" download>↓ &nbsp;Download CSV</a></div>
  </header>
  <section class="intro"><h1>Home environment</h1><p>Live readings from the Raspberry Pi sensor board</p></section>
  <section id="alertBanner" class="alert-strip hidden" role="alert" aria-live="assertive"><div class="alert-title">⚠ Sensor alert</div><div id="alertMessages"></div><div id="alertHelp" class="alert-help">Temperature alert: above 27 °C, reminders every 5 minutes until 27 °C or below. Gas input is a prototype threshold, not a certified alarm.</div></section>
  <section class="grid">
    <article class="card"><div class="cardtop"><span>Temperature</span><span class="icon">🌡</span></div><div class="value"><span id="temp">—</span><span class="unit"> °C</span></div><div class="sub" id="tempSub">Waiting for sensor data</div></article>
    <article class="card"><div class="cardtop"><span>Humidity</span><span class="icon">💧</span></div><div class="value"><span id="humidity">—</span><span class="unit"> %</span></div><div class="sub" id="humiditySub">Relative humidity</div></article>
    <article class="card gas-card"><div class="cardtop"><span>Gas threshold input</span><span class="icon">◉</span></div><div class="gasstate"><i id="gasLed" class="gasled"></i><span id="gas" class="gasbadge unknown">WAITING</span></div><div class="sub" id="gasSub">MQ-2 digital output · not concentration calibrated</div></article>
  </section>
  <section class="section">
    <article class="panel"><div class="panelhead"><div><h2>Recent sensor history</h2><p>Last 60 recorded samples · updates as new readings arrive</p></div><div class="legend"><span><i class="key"></i>Temp °C</span><span><i class="key hum"></i>Humidity %</span></div></div><div class="chartbox"><canvas id="chart"></canvas></div></article>
    <article class="panel history"><div class="panelhead"><div><h2>Latest readings</h2><p id="lastUpdated">Waiting for first sample</p></div></div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Temp</th><th>RH</th><th>Gas</th></tr></thead><tbody id="rows"><tr><td colspan="4" class="empty">Connecting to the Pi…</td></tr></tbody></table></div><div class="note"><strong>Gas status:</strong> ACTIVE means the digital threshold input crossed. This is not a calibrated gas concentration or a safety alarm.</div></article>
  </section>
  <section class="panel" style="margin-top:18px"><div class="panelhead"><div><h2>Previous alerts</h2><p>Saved locally in sensor_alerts.csv</p></div><a class="button" href="/download-alerts.csv" download>↓ &nbsp;Download alert CSV</a></div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Alert</th><th>Event</th><th>Reading</th><th>Message</th></tr></thead><tbody id="alertRows"><tr><td colspan="5" class="empty">No alerts recorded yet</td></tr></tbody></table></div><div id="alertLogError" class="sub"></div></section>
  <div id="error" class="note error hidden"></div>
  <div class="footer"><span>Local dashboard · polling every 2 seconds · CSV saved in this project folder</span><span id="csvState">CSV logger starting</span></div>
</main>
<script>
const $=id=>document.getElementById(id);let history=[];let lastFrame=null;let resizeTimer;let latestSample=null;let lastAlertId=0;let alertBootstrapDone=false;const TEMP_LIMIT=27;
function fmt(v,n=1){return v===null||v===undefined||Number.isNaN(Number(v))?'—':Number(v).toFixed(n)}
function setConnection(kind,text){const e=$('connection');e.className='pill '+kind;$('connectionText').textContent=text}
function desktopNotice(title,body,tag){if('Notification'in window&&Notification.permission==='granted'){try{new Notification(title,{body,tag,renotify:true})}catch(e){}}}
function updateAlertPermission(){const b=$('enableAlerts');if(!('Notification'in window)){b.textContent='Desktop alerts unavailable';b.disabled=true;return}if(Notification.permission==='granted'){b.textContent='Desktop alerts enabled';b.disabled=true}else if(Notification.permission==='denied'){b.textContent='Alerts blocked in browser settings';b.disabled=true}else{b.textContent='Enable desktop alerts';b.disabled=false}}
function notifySavedAlert(a){if(a.sensor==='temperature'&&(a.event==='trigger'||a.event==='repeat'))desktopNotice(a.event==='repeat'?'Temperature reminder':'High temperature detected',a.message,'autohome-temperature');else if(a.sensor==='gas'&&a.event==='detected')desktopNotice('Gas/smoke threshold detected',a.message,'autohome-gas')}
function updateAlerts(s){const messages=[];if(s.temperature_alert_active){const left=Math.max(0,Number(s.temperature_next_reminder_seconds)||0),mins=Math.floor(left/60),secs=left%60;const fresh=s.connected&&Number(s.age_seconds)<=10&&Number(s.dht_age_seconds)<=30;messages.push(fresh?`Temperature is ${fmt(s.temperature_c)} °C — turn on the fan or AC. Reminder in ${mins}:${String(secs).padStart(2,'0')}.`:'Temperature is above 27 °C; the latest reading is stale.')}if(s.gas_alert_active)messages.push(s.connected&&Number(s.age_seconds)<=10?'Gas/smoke threshold detected — check the area and act safely.':'Gas/smoke threshold remains active; latest reading is stale.');$('alertBanner').classList.toggle('hidden',messages.length===0);$('alertBanner').classList.toggle('gas-alert',Boolean(s.gas_alert_active));$('alertMessages').innerHTML=messages.map(m=>`<div class="alert-item">${m}</div>`).join('');latestSample=s;updateAlertPermission()}
async function enableDesktopAlerts(){if(!('Notification'in window))return;try{const permission=await Notification.requestPermission();updateAlertPermission();if(permission==='granted'&&latestSample){if(latestSample.temperature_alert_active)desktopNotice('Temperature alert active',`Temperature is ${fmt(latestSample.temperature_c)} °C. Turn on the fan or AC.`,'autohome-temperature');if(latestSample.gas_alert_active)desktopNotice('Gas/smoke threshold detected','Check the area and act safely. This sensor is not a certified alarm.','autohome-gas')}}catch(e){$('enableAlerts').textContent='Could not enable alerts'}}
function draw(){const c=$('chart'),box=c.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(!box.width||!box.height)return;c.width=box.width*dpr;c.height=box.height*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);const w=box.width,h=box.height,p={l:38,r:10,t:12,b:25};x.clearRect(0,0,w,h);x.font='10px system-ui';x.lineWidth=1;const vals=history.slice(-60);const datasets=[{key:'temperature_c',color:'#67e8f9',min:Math.floor(Math.min(18,...vals.map(s=>Number(s.temperature_c)||0))-1),max:Math.ceil(Math.max(32,...vals.map(s=>Number(s.temperature_c)||0))+1)},{key:'humidity_percent',color:'#a78bfa',min:0,max:100}];for(let i=0;i<=4;i++){const yy=p.t+(h-p.t-p.b)*i/4;x.strokeStyle='#29364b';x.beginPath();x.moveTo(p.l,yy);x.lineTo(w-p.r,yy);x.stroke();x.fillStyle='#8393aa';x.textAlign='right';x.fillText(String(Math.round(datasets[0].max-(datasets[0].max-datasets[0].min)*i/4)),p.l-7,yy+3)}if(vals.length<2){x.fillStyle='#8393aa';x.textAlign='center';x.fillText('Collecting live samples…',w/2,h/2);return}for(let di=0;di<datasets.length;di++){const ds=datasets[di];x.beginPath();x.strokeStyle=ds.color;x.lineWidth=2;vals.forEach((s,i)=>{const v=Number(s[ds.key]);const px=p.l+(w-p.l-p.r)*i/(vals.length-1),py=p.t+(h-p.t-p.b)*(1-(v-ds.min)/(ds.max-ds.min));if(i===0)x.moveTo(px,py);else x.lineTo(px,py)});x.stroke()}x.fillStyle='#8393aa';x.textAlign='left';x.fillText('oldest',p.l,h-5);x.textAlign='right';x.fillText('latest',w-p.r,h-5)}
function render(s){if(!s)return;const age=Number(s.dht_age_seconds);$('temp').textContent=fmt(s.temperature_c);$('humidity').textContent=fmt(s.humidity_percent);$('tempSub').textContent=s.dht_error?`Last valid reading · ${s.dht_error}`:'DHT22 live reading';$('humiditySub').textContent=s.dht_error?'Most recent DHT read had a retry':'Relative humidity';const active=s.gas_input==='ACTIVE';$('gas').textContent=s.gas_input||'UNKNOWN';$('gas').className='gasbadge '+(active?'active':s.gas_input==='IDLE'?'idle': 'unknown');$('gasLed').className='gasled '+(active?'active':s.gas_input==='IDLE'?'idle':'');$('gasSub').textContent=`MQ-2 digital output · raw ${s.gas_raw??'—'} · not concentration calibrated`;$('alertLogError').textContent=s.alert_error||'';const stale=Number(s.age_seconds)>10||s.status!=='RUNNING';const connection=!s.connected?'offline':stale?'stale':'live';setConnection(connection,connection.toUpperCase());$('lastUpdated').textContent=`Pi sample ${s.pi_time||'—'} · received ${s.received_at||'—'}`;$('csvState').textContent=s.csv_error|| (s.csv_rows===0?'CSV ready · waiting for a new Pi sample':`Saved locally · ${s.csv_rows} samples`);$('csvState').className=s.csv_error?'error':'';$('error').classList.toggle('hidden',!s.error);$('error').textContent=s.error||'';updateAlerts(s);if(s.frame!==lastFrame&&s.frame!==undefined){lastFrame=s.frame;if(s.history){history=s.history.slice(-60);draw()}else pollHistory()} }
async function pollHistory(){try{const r=await fetch('/api/history?limit=60',{cache:'no-store'});const d=await r.json();history=d.samples||[];draw();const body=$('rows');if(!history.length){body.innerHTML='<tr><td colspan="4" class="empty">Waiting for the next sensor sample…</td></tr>';return}body.innerHTML=history.slice(-8).reverse().map(s=>`<tr><td>${(s.received_at||'').slice(11,19)||'—'}</td><td>${fmt(s.temperature_c)} °C</td><td>${fmt(s.humidity_percent)}%</td><td><span class="gas-tag ${s.gas_input==='ACTIVE'?'active':''}">${s.gas_input||'—'}</span></td></tr>`).join('')}catch(e){}}
function escapeHtml(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function pollAlertHistory(initial=false){try{const url=initial?'/api/alerts?limit=20&initial=1':`/api/alerts?limit=20&after=${lastAlertId}`;const r=await fetch(url,{cache:'no-store'});const d=await r.json();const body=$('alertRows'),items=d.alerts||[];if(!items.length)body.innerHTML='<tr><td colspan="5" class="empty">No alerts recorded yet</td></tr>';else body.innerHTML=items.slice().reverse().map(a=>`<tr><td>${escapeHtml((a.received_at_local||'').replace('T',' ').slice(0,19))}</td><td>${escapeHtml(a.sensor||'')}</td><td><span class="alert-event ${escapeHtml(a.event||'')}">${escapeHtml(a.event||'')}</span></td><td>${escapeHtml(a.value??'—')}${a.sensor==='temperature'?' °C':''}</td><td>${escapeHtml(a.message||'')}</td></tr>`).join('');$('alertLogError').textContent=d.error||'';if(initial&&!alertBootstrapDone){lastAlertId=Number(d.latest_id)||0;alertBootstrapDone=true;const recent=items.filter(a=>Date.now()-Date.parse(a.timestamp_utc)<15000);recent.forEach(notifySavedAlert)}else{(d.new_events||[]).forEach(notifySavedAlert);lastAlertId=Math.max(lastAlertId,Number(d.latest_id)||0)}}catch(e){}}
async function tick(){try{const r=await fetch('/api/latest',{cache:'no-store'});const d=await r.json();render(d);await Promise.all([pollHistory(),pollAlertHistory(false)])}catch(e){setConnection('offline','OFFLINE');$('error').textContent='Could not read the local dashboard service.';$('error').classList.remove('hidden')}}
window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(draw,100)});$('enableAlerts').addEventListener('click',enableDesktopAlerts);updateAlertPermission();async function startup(){await pollAlertHistory(true);await tick();setInterval(tick,2000)}startup();
</script>
</body>
</html>"""


def row_from_sample(sample: dict[str, Any]) -> dict[str, Any]:
    received = datetime.now().astimezone().isoformat(timespec="seconds")
    pi_time = datetime.fromtimestamp(float(sample["updated_epoch"]), timezone.utc).isoformat(timespec="seconds")
    return {
        "timestamp_utc": pi_time,
        "received_at_local": received,
        "temperature_c": sample.get("temperature_c"),
        "humidity_percent": sample.get("humidity_percent"),
        "gas_raw": sample.get("gas_raw"),
        "gas_input": sample.get("gas_input"),
        "dht_age_seconds": sample.get("dht_age_seconds"),
        "dht_error": sample.get("dht_error"),
        "frame": sample.get("frame"),
        "status": sample.get("status"),
        "gas_concentration_calibrated": sample.get("gas_concentration_calibrated"),
    }


def append_row(path: Path, row: dict[str, Any], columns: list[str] = CSV_COLUMNS) -> None:
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
        f.flush()


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def all_saved_rows() -> list[dict[str, Any]]:
    rows = read_rows(CSV_PATH)
    frames = {str(row.get("frame")) for row in rows}
    for row in read_rows(PENDING_CSV_PATH):
        frame = str(row.get("frame"))
        if frame not in frames:
            rows.append(row)
            frames.add(frame)
    return rows


def flush_pending_rows() -> None:
    pending = read_rows(PENDING_CSV_PATH)
    if not pending:
        return
    existing_frames = {str(row.get("frame")) for row in read_rows(CSV_PATH)}
    new_rows = [row for row in pending if str(row.get("frame")) not in existing_frames]
    if new_rows:
        new_file = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerows(new_rows)
            f.flush()
    PENDING_CSV_PATH.unlink(missing_ok=True)


def save_sample(sample: dict[str, Any]) -> bool:
    row = row_from_sample(sample)
    if PENDING_CSV_PATH.exists():
        append_row(PENDING_CSV_PATH, row)
        try:
            flush_pending_rows()
            return False
        except PermissionError:
            return True
    try:
        append_row(CSV_PATH, row)
        return False
    except PermissionError:
        append_row(PENDING_CSV_PATH, row)
        return True


def csv_sample_count() -> int:
    return len(all_saved_rows())


def all_alert_rows() -> list[dict[str, Any]]:
    rows = read_rows(ALERT_CSV_PATH)
    seen = {str(row.get("event_id")) for row in rows}
    for row in read_rows(PENDING_ALERT_CSV_PATH):
        event_id = str(row.get("event_id"))
        if event_id not in seen:
            rows.append(row)
            seen.add(event_id)
    rows.sort(key=lambda row: int(row.get("event_id") or 0))
    return rows


def flush_pending_alerts() -> None:
    pending = read_rows(PENDING_ALERT_CSV_PATH)
    if not pending:
        return
    existing_ids = {str(row.get("event_id")) for row in read_rows(ALERT_CSV_PATH)}
    new_rows = [row for row in pending if str(row.get("event_id")) not in existing_ids]
    if new_rows:
        new_file = not ALERT_CSV_PATH.exists() or ALERT_CSV_PATH.stat().st_size == 0
        with ALERT_CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ALERT_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerows(new_rows)
            f.flush()
    PENDING_ALERT_CSV_PATH.unlink(missing_ok=True)


def save_alert(payload: dict[str, Any]) -> dict[str, Any]:
    sensor = payload.get("sensor")
    event = payload.get("event")
    if sensor not in {"temperature", "gas"} or event not in {"trigger", "repeat", "cleared", "detected"}:
        raise ValueError("Unsupported alert event")
    with ALERT_LOCK:
        rows = all_alert_rows()
        event_id = max((int(row.get("event_id") or 0) for row in rows), default=0) + 1
        row = {
            "event_id": event_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "received_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sensor": sensor,
            "event": event,
            "value": payload.get("value"),
            "threshold": payload.get("threshold"),
            "message": str(payload.get("message") or "")[:300],
        }
        if PENDING_ALERT_CSV_PATH.exists():
            append_row(PENDING_ALERT_CSV_PATH, row, ALERT_COLUMNS)
            try:
                flush_pending_alerts()
            except PermissionError:
                pass
        else:
            try:
                append_row(ALERT_CSV_PATH, row, ALERT_COLUMNS)
            except PermissionError:
                append_row(PENDING_ALERT_CSV_PATH, row, ALERT_COLUMNS)
        with STATE_LOCK:
            STATE["alert_error"] = None
        return row


def restore_alert_state() -> None:
    latest: dict[str, dict[str, Any]] = {}
    for row in all_alert_rows():
        latest[row["sensor"]] = row
    temperature = latest.get("temperature")
    gas = latest.get("gas")
    temp_active = bool(temperature and temperature["event"] != "cleared")
    gas_active = bool(gas and gas["event"] != "cleared")
    last_time = None
    if temp_active and temperature:
        try:
            last_time = datetime.fromisoformat(temperature["timestamp_utc"]).timestamp()
        except (ValueError, TypeError):
            last_time = time.time()
    with STATE_LOCK:
        STATE.update(temperature_alert_active=temp_active, gas_alert_active=gas_active, last_temperature_alert_time=last_time)


def process_sensor_alerts(sample: dict[str, Any]) -> None:
    now = time.time()
    sample_fresh = now - float(sample.get("updated_epoch", 0)) <= 10
    valid_temp = sample_fresh and sample.get("status") == "RUNNING" and sample.get("temperature_c") is not None and float(sample.get("dht_age_seconds", 9999)) <= 30
    if valid_temp:
        temperature = float(sample["temperature_c"])
        with STATE_LOCK:
            active = STATE["temperature_alert_active"]
            last_time = STATE["last_temperature_alert_time"]
        if temperature > TEMP_THRESHOLD_C:
            if not active:
                save_alert({"sensor": "temperature", "event": "trigger", "value": temperature, "threshold": TEMP_THRESHOLD_C, "message": f"Temperature is {temperature:.1f} °C — turn on the fan or AC."})
                with STATE_LOCK:
                    STATE.update(temperature_alert_active=True, last_temperature_alert_time=now)
            elif last_time is None or now - float(last_time) >= TEMP_REPEAT_SECONDS:
                save_alert({"sensor": "temperature", "event": "repeat", "value": temperature, "threshold": TEMP_THRESHOLD_C, "message": f"Reminder: temperature is {temperature:.1f} °C — turn on the fan or AC."})
                with STATE_LOCK:
                    STATE["last_temperature_alert_time"] = now
        elif active:
            save_alert({"sensor": "temperature", "event": "cleared", "value": temperature, "threshold": TEMP_THRESHOLD_C, "message": f"Temperature returned to {temperature:.1f} °C, at or below {TEMP_THRESHOLD_C:.0f} °C."})
            with STATE_LOCK:
                STATE.update(temperature_alert_active=False, last_temperature_alert_time=None)
        else:
            with STATE_LOCK:
                STATE["temperature_alert_active"] = False

    gas_known = sample_fresh and sample.get("status") == "RUNNING" and sample.get("gas_input") in {"IDLE", "ACTIVE"}
    if gas_known:
        gas_active = sample["gas_input"] == "ACTIVE"
        with STATE_LOCK:
            previous_gas_active = STATE["gas_alert_active"]
        if gas_active and not previous_gas_active:
            save_alert({"sensor": "gas", "event": "detected", "value": sample.get("gas_raw"), "threshold": "digital input", "message": "Gas/smoke threshold detected — check the area and act safely."})
            with STATE_LOCK:
                STATE["gas_alert_active"] = True
        elif not gas_active and previous_gas_active:
            save_alert({"sensor": "gas", "event": "cleared", "value": sample.get("gas_raw"), "threshold": "digital input", "message": "Gas input returned to IDLE."})
            with STATE_LOCK:
                STATE["gas_alert_active"] = False


def connect_pi(password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        hostname=PI_HOST,
        username=PI_USER,
        password=password,
        look_for_keys=True,
        allow_agent=True,
        timeout=8,
        banner_timeout=8,
        auth_timeout=8,
    )
    return client


def poll_pi(password: str) -> None:
    client: paramiko.SSHClient | None = None
    while not STOP.is_set():
        try:
            transport = client.get_transport() if client else None
            if not transport or not transport.is_active():
                if client:
                    client.close()
                client = connect_pi(password)
            _stdin, stdout, stderr = client.exec_command(f"cat {REMOTE_STATUS}", timeout=5)
            payload = stdout.read().decode("utf-8")
            err = stderr.read().decode("utf-8").strip()
            if not payload:
                raise RuntimeError(err or "Pi returned an empty sensor status")
            sample = json.loads(payload)
            epoch = float(sample["updated_epoch"])
        except Exception as exc:
            if client:
                client.close()
                client = None
            with STATE_LOCK:
                STATE["connected"] = False
                STATE["error"] = f"Pi connection or sensor read failed: {type(exc).__name__}: {exc}"
            STOP.wait(5)
            continue

        try:
            process_sensor_alerts(sample)
        except Exception as alert_exc:
            with STATE_LOCK:
                STATE["alert_error"] = f"Alert history save failed: {type(alert_exc).__name__}: {alert_exc}"
        try:
            with STATE_LOCK:
                prior_epoch = STATE["last_saved_epoch"]
            if prior_epoch is None or epoch > prior_epoch:
                csv_locked = save_sample(sample)
                with STATE_LOCK:
                    STATE["last_saved_epoch"] = epoch
                    STATE["csv_error"] = ("CSV is open or locked; buffering new rows in sensor_readings.pending.csv. Close the CSV to merge them automatically." if csv_locked else None)
            else:
                flush_pending_rows()
                with STATE_LOCK:
                    STATE["csv_error"] = None
        except PermissionError:
            with STATE_LOCK:
                STATE["csv_error"] = "CSV is open or locked; new rows are buffered locally until it can be updated."
        except Exception as csv_exc:
            with STATE_LOCK:
                STATE["csv_error"] = f"CSV save failed: {type(csv_exc).__name__}: {csv_exc}"
        received_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with STATE_LOCK:
            STATE.update(connected=True, error=None, sample=sample, received_at=received_at)
        STOP.wait(POLL_SECONDS)
    if client:
        client.close()


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def send_json(self, obj: Any, code: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            raw = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if route == "/api/latest":
            with STATE_LOCK:
                snap = dict(STATE)
            sample = dict(snap["sample"]) if snap["sample"] else None
            age = None
            if sample:
                age = max(0, time.time() - float(sample.get("updated_epoch", 0)))
            result = {
                "connected": snap["connected"],
                "error": snap["error"],
                "csv_error": snap["csv_error"],
                "alert_error": snap["alert_error"],
                "sample": sample,
                "received_at": snap["received_at"],
                "age_seconds": age,
                "csv_rows": csv_sample_count(),
                "temperature_alert_active": snap["temperature_alert_active"],
                "gas_alert_active": snap["gas_alert_active"],
                "temperature_threshold_c": TEMP_THRESHOLD_C,
                "temperature_next_reminder_seconds": max(0, TEMP_REPEAT_SECONDS - int(time.time() - snap["last_temperature_alert_time"])) if snap["temperature_alert_active"] and snap["last_temperature_alert_time"] else None,
                "pi_time": datetime.fromtimestamp(float(sample["updated_epoch"]), timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if sample else None,
            }
            if sample:
                result.update(sample)
            self.send_json(result)
            return
        if route == "/api/history":
            try:
                from urllib.parse import parse_qs
                limit = min(500, max(1, int(parse_qs(urlparse(self.path).query).get("limit", ["60"])[0])))
                samples = all_saved_rows()[-limit:]
                for s in samples:
                    for key in ("temperature_c", "humidity_percent", "gas_raw", "dht_age_seconds", "frame"):
                        if s.get(key) not in (None, ""):
                            s[key] = float(s[key])
                self.send_json({"samples": samples})
            except FileNotFoundError:
                self.send_json({"samples": []})
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc), "samples": []}, 500)
            return
        if route == "/api/alerts":
            try:
                from urllib.parse import parse_qs
                query = parse_qs(urlparse(self.path).query)
                limit = min(500, max(1, int(query.get("limit", ["20"])[0])))
                after = max(0, int(query.get("after", ["0"])[0]))
                initial = query.get("initial", ["0"])[0] == "1"
                with ALERT_LOCK:
                    rows = all_alert_rows()
                latest_by_sensor: dict[str, dict[str, Any]] = {}
                for row in rows:
                    latest_by_sensor[row["sensor"]] = row
                new_events = [] if initial else [row for row in rows if int(row["event_id"]) > after]
                self.send_json({"alerts": rows[-limit:], "new_events": new_events, "latest_id": int(rows[-1]["event_id"]) if rows else 0, "last_by_sensor": latest_by_sensor, "csv_path": str(ALERT_CSV_PATH)})
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc), "alerts": []}, 500)
            return
        if route == "/download.csv":
            rows = all_saved_rows()
            if not rows:
                self.send_json({"error": "No sensor samples have been saved yet."}, 404)
                return
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            raw = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="sensor_readings.csv"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if route == "/download-alerts.csv":
            with ALERT_LOCK:
                rows = all_alert_rows()
            if not rows:
                self.send_json({"error": "No alerts have been recorded yet."}, 404)
                return
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=ALERT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            raw = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="sensor_alerts.csv"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/alerts":
            self.send_error(404)
            return
        try:
            length = min(4096, int(self.headers.get("Content-Length", "0")))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            row = save_alert(payload)
            self.send_json({"saved": True, "alert": row})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"saved": False, "error": str(exc)}, 400)
        except OSError as exc:
            self.send_json({"saved": False, "error": f"Alert CSV write failed: {exc}"}, 500)


def main() -> None:
    password = getpass.getpass(f"SSH password for {PI_USER}@{PI_HOST} (used in memory only): ")
    restore_alert_state()
    thread = threading.Thread(target=poll_pi, args=(password,), daemon=True)
    thread.start()
    print(f"Connecting to {PI_USER}@{PI_HOST} in the background; the dashboard will retry SSH if it drops.")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Dashboard: {url}")
    print(f"CSV file:  {CSV_PATH}")
    print("Press Ctrl+C to stop the dashboard and logger.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping dashboard…")
    finally:
        STOP.set()
        server.server_close()


if __name__ == "__main__":
    main()
