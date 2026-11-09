"""v0.4 「기전 근거」 시트의 URL 원문을 받아 sources/V04-*.txt로 저장하고 목록을 v04_sources.json에 남긴다.

HTML은 태그를 지운 본문 텍스트, PDF는 pdftotext 결과를 저장한다. 이미 받은 파일은 건너뛴다.
실행: python fetch_v04_sources.py <v0.4.xlsx>
"""
import hashlib
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import openpyxl

EV = Path(__file__).parent
SRC = EV / "sources"
wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
urls = sorted({r[7] for r in wb["기전 근거"].iter_rows(min_row=2, values_only=True) if r[1] and r[7]})


def sid_of(url):
    m = re.search(r"cntnts_sn=(\d+)", url)
    if m:
        return f"V04-KDCA-{m.group(1)}"
    return "V04-" + re.sub(r"^www\.", "", url.split("/")[2]).split(".")[0] + "-" + hashlib.md5(url.encode()).hexdigest()[:8]


def to_text(raw: bytes, ctype: str, path: Path) -> str:
    if raw[:4] == b"%PDF" or "pdf" in ctype:
        tmp = path.with_suffix(".pdf")
        tmp.write_bytes(raw)
        out = subprocess.run(["pdftotext", "-layout", str(tmp), "-"], capture_output=True, text=True).stdout
        tmp.unlink()
        return out
    s = raw.decode("utf-8", errors="ignore")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(s))


index = []
for url in urls:
    sid = sid_of(url)
    path = SRC / f"{sid}.txt"
    status = "cached"
    if not path.exists():
        r = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", "Mozilla/5.0", "-w", "\n%{content_type}", url],
                           capture_output=True)
        body, _, ctype = r.stdout.rpartition(b"\n")
        text = to_text(body, ctype.decode(errors="ignore"), path) if r.returncode == 0 else ""
        path.write_text(text)
        status = "curl" if len(text.strip()) > 500 else "empty"
        time.sleep(0.5)
    elif len(path.read_text(errors="ignore").strip()) <= 500:
        status = "empty"
    index.append({"source_id": sid, "url": url, "file": f"sources/{sid}.txt", "fetch_method": status,
                  "chars": len(path.read_text(errors="ignore"))})
    print(status, sid, index[-1]["chars"])

json.dump(index, open(EV / "v04_sources.json", "w"), ensure_ascii=False, indent=1)
print("총", len(index), "빈 응답", sum(1 for i in index if i["fetch_method"] == "empty"))
