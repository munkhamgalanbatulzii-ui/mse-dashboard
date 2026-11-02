#!/usr/bin/env python3
import html, json, os, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
TZ=ZoneInfo("Asia/Ulaanbaatar")
URL="https://munkhamgalanbatulzii-ui.github.io/mse-dashboard/"
REQ=["AZURE_TENANT_ID","AZURE_CLIENT_ID","AZURE_CLIENT_SECRET","OUTLOOK_SENDER","EMAIL_TO"]

def mnt(v):
    v=float(v or 0)
    if abs(v)>=1e9: return f"₮{v/1e9:.2f} тэрбум"
    if abs(v)>=1e6: return f"₮{v/1e6:.1f} сая"
    if abs(v)>=1e3: return f"₮{v/1e3:.1f} мянга"
    return f"₮{v:,.0f}"

def usd(v):
    v=float(v or 0)
    if abs(v)>=1e6: return "$"+f"{v/1e6:.2f} сая"
    if abs(v)>=1e3: return "$"+f"{v/1e3:.1f} мянга"
    return "$"+f"{v:,.0f}"

def pct(v):
    return f"{float(v or 0)*100:+.1f}%"

def sym(data,idx):
    s=data["syms"][idx]
    return s[0],s[1]

def post_form(url,fields):
    body=urllib.parse.urlencode(fields).encode()
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/x-www-form-urlencoded"},method="POST")
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.loads(r.read().decode())

def get_token():
    tenant=os.environ["AZURE_TENANT_ID"].strip()
    return post_form(
        f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token",
        {
            "client_id":os.environ["AZURE_CLIENT_ID"].strip(),
            "client_secret":os.environ["AZURE_CLIENT_SECRET"].strip(),
            "scope":"https://graph.microsoft.com/.default",
            "grant_type":"client_credentials",
        }
    )["access_token"]

def send_mail(sender,recipients,subject,body):
    token=get_token()
    payload={
        "message":{
            "subject":subject,
            "body":{"contentType":"HTML","content":body},
            "toRecipients":[{"emailAddress":{"address":sender}}],
            "bccRecipients":[{"emailAddress":{"address":x}} for x in recipients if x.lower()!=sender.lower()],
        }
    }
    req=urllib.request.Request(
        "https://graph.microsoft.com/v1.0/users/"+urllib.parse.quote(sender,safe="")+"/sendMail",
        data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            if r.status!=202:
                raise RuntimeError("Graph sendMail HTTP "+str(r.status))
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors="replace")
        raise RuntimeError(f"Graph sendMail HTTP {e.code}: {detail[:500]}")

def td(x,bold=False):
    x=html.escape(str(x))
    if bold: x="<b>"+x+"</b>"
    return "<td style='padding:8px;border-bottom:1px solid #e5e9e6'>"+x+"</td>"

def build(data,latest,is_test):
    s=list(data.get("summary",{}).get(latest,[]))
    while len(s)<11: s.append(0)
    stock_turn,stock_n,up,down=s[0],s[1],s[2],s[3]
    block_n,block_val=s[7],s[8]

    alerts=data.get("alerts",{}).get(latest,[])
    crit=sum(1 for a in alerts if a[1]=="CRITICAL")
    watch=sum(1 for a in alerts if a[1]=="WATCH")
    info=sum(1 for a in alerts if a[1]=="INFO")

    arows=[]
    labels={"CRITICAL":"🔴 Critical","WATCH":"🟠 Watch","INFO":"🔵 Info"}
    for a in alerts[:8]:
        code,name=sym(data,a[0])
        reason=a[8] if len(a)>8 and a[8] else ""
        arows.append("<tr>"+td(code,True)+td(labels.get(a[1],a[1]))+td(pct(a[2]),True)+td(reason)+"</tr>")
    alerts_html=("Өнөөдөр автомат дохио илрээгүй." if not arows else
        "<table style='border-collapse:collapse;width:100%;font-size:14px'><tr><th>Хувьцаа</th><th>Дохио</th><th>Ханш</th><th>Тайлбар</th></tr>"+''.join(arows)+"</table>")

    stocks=[]
    for r in data.get("days",{}).get(latest,[]):
        code,name=sym(data,r[0])
        stocks.append((r[4],code,r[2]))
    stocks.sort(reverse=True)
    trows=["<tr>"+td(code,True)+td(pct(ret))+td(mnt(turn),True)+"</tr>" for turn,code,ret in stocks[:5]]
    top_html="<table style='border-collapse:collapse;width:100%;font-size:14px'><tr><th>Хувьцаа</th><th>Ханш</th><th>Арилжааны дүн</th></tr>"+''.join(trows)+"</table>"

    blocks=sorted(data.get("blocks",{}).get(latest,[]),key=lambda x:x[4],reverse=True)
    brows=[]
    for b in blocks[:5]:
        prem="—" if len(b)<6 or b[5] is None else pct(b[5])
        brows.append("<tr>"+td(b[0],True)+td(f"{int(b[3]):,}")+td(mnt(b[4]),True)+td(prem)+"</tr>")
    block_html=("Багцын арилжаа хийгдээгүй." if not brows else
        "<table style='border-collapse:collapse;width:100%;font-size:14px'><tr><th>Симбол</th><th>Ширхэг</th><th>Дүн</th><th>Зах зээлтэй</th></tr>"+''.join(brows)+"</table>")

    other=data.get("other",{}).get(latest,[])
    bond_mnt=sum(float(r[6] or 0) for r in other if len(r)>6 and r[0]=="Компанийн бонд" and r[3]=="MNT")
    bond_usd=sum(float(r[6] or 0) for r in other if len(r)>6 and r[0]=="Компанийн бонд" and r[3]=="USD")
    bonds=[]
    if bond_mnt: bonds.append(mnt(bond_mnt))
    if bond_usd: bonds.append(usd(bond_usd))
    bond_text=" / ".join(bonds) if bonds else "Арилжаа бүртгэгдээгүй"

    subject=("[TEST] " if is_test else "")+f"МХБ өдрийн тойм | {latest} — {crit} Critical, {mnt(stock_turn)}"
    body=f"""
    <div style="font-family:Arial,sans-serif;max-width:760px;margin:auto;color:#17211b;line-height:1.45">
      <h2 style="margin-bottom:6px">МХБ өдрийн тойм · {latest}</h2>
      <p>Өнөөдөр <b>{int(stock_n)} хувьцаа {mnt(stock_turn)}</b>-өөр арилжаалагдаж,
      <b>{int(up)} өсөж, {int(down)} буурлаа.</b></p>

      <table style="border-collapse:collapse;width:100%;font-size:14px;margin:14px 0">
        <tr><td style="padding:8px;border:1px solid #e5e9e6"><b>Дохио</b><br>{len(alerts)} · {crit} Critical · {watch} Watch · {info} Info</td>
        <td style="padding:8px;border:1px solid #e5e9e6"><b>Багцын арилжаа</b><br>{mnt(block_val)} · {int(block_n)} хэлцэл</td>
        <td style="padding:8px;border:1px solid #e5e9e6"><b>Компанийн бонд</b><br>{bond_text}</td></tr>
      </table>

      <h3>🚨 Өнөөдрийн дохио</h3>{alerts_html}
      <h3>💰 Хамгийн том арилжаа</h3>{top_html}
      <h3>📦 Багцын арилжаа</h3>{block_html}

      <p style="margin-top:18px"><a href="{URL}" style="background:#154f38;color:white;text-decoration:none;padding:9px 13px;border-radius:6px">Дэлгэрэнгүй самбар</a></p>
      <p style="font-size:11px;color:#6f7972;margin-top:18px">Автомат дохио нь ханшийн хөдөлгөөн болон түүхэн арилжааны идэвхэд суурилсан бөгөөд хөрөнгө оруулалтын зөвлөмж биш.</p>
    </div>
    """
    return subject,body

def main():
    missing=[k for k in REQ if not os.getenv(k,"").strip()]
    if missing:
        print("[mail] skipped: missing secrets: "+", ".join(missing))
        return 0

    data=json.loads(DATA.read_text(encoding="utf-8"))
    latest=data.get("latest")
    today=datetime.now(TZ).date().isoformat()
    if latest!=today:
        print(f"[mail] skipped: latest={latest}, today={today}")
        return 0

    sender=os.environ["OUTLOOK_SENDER"].strip()
    recipients=[x.strip() for x in os.environ["EMAIL_TO"].replace(";",",").split(",") if x.strip()]
    if not recipients:
        print("[mail] skipped: EMAIL_TO is empty")
        return 0

    is_test=os.getenv("GITHUB_EVENT_NAME")=="workflow_dispatch"
    if is_test:
        recipients=[sender]
        print("[mail] manual run: TEST email only to sender")
    else:
        print(f"[mail] scheduled run: {len(recipients)} BCC recipient(s)")

    subject,body=build(data,latest,is_test)
    send_mail(sender,recipients,subject,body)
    print("[mail] Microsoft Graph accepted: "+subject)
    return 0

if __name__=="__main__":
    sys.exit(main())
