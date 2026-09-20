# -*- coding: utf-8 -*-
"""
每日资讯简报 - GitHub Actions 版
================================================
跑在 GitHub 的免费云端 runner 上，与你家电脑是否开机完全无关。
使用公开仓库时 GitHub Actions 永久免费，无需绑定任何支付方式。

【输出方式（二选一或都开）】
  1. 邮件：在仓库 Settings -> Secrets 配置 MAIL_USER / MAIL_PASS / MAIL_TO
  2. 微信：在仓库 Settings -> Secrets 配置 PUSHPLUS_TOKEN

  两者都配就都发；只配一个就只发一个。

【本版说明】
  纯 Python 标准库，无需安装任何第三方包。
  抓取公开 RSS 新闻源 -> 生成 HTML 简报 -> 邮件 / 微信推送。
"""

import smtplib
import ssl
import re
import os
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ============ 配置（优先读环境变量，未配置则用默认值）============
MAIL_USER = os.environ.get("MAIL_USER", "").strip()
MAIL_PASS = os.environ.get("MAIL_PASS", "").strip()
MAIL_TO   = os.environ.get("MAIL_TO", "").strip()
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CST = timezone(timedelta(hours=8))

# ============ RSS 新闻源 ============
FEEDS = {
    "科技互联网": [
        ("36氪",        "https://36kr.com/feed"),
        ("少数派",      "https://sspai.com/feed"),
        ("Solidot奇客", "https://www.solidot.org/index.rss"),
        ("机器之心",    "https://www.jiqizhixin.com/rss"),
    ],
    "国际新闻": [
        ("BBC中文",     "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
        ("纽约时报中文", "https://cn.nytimes.com/rss/"),
        ("联合早报",    "https://www.zaobao.com/realtime/world/rss.xml"),
    ],
    "世界局势": [
        ("参考消息",    "http://www.cankaoxiaoxi.com/rss/world.xml"),
        ("环球网国际",  "https://www.huanqiu.com/rss/world.xml"),
    ],
    "社会热点": [
        ("澎湃新闻",    "https://www.thepaper.cn/rss.jsp"),
        ("中国新闻网",  "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ],
    "金融市场": [
        ("华尔街见闻",  "https://dedicated.wallstreetcn.com/rss.xml"),
        ("新浪财经",    "https://rss.sina.com.cn/finance/stock1.xml"),
        ("第一财经",    "https://www.yicai.com/rss/news.xml"),
    ],
}

TOP_N = 3
HOURS_WINDOW = 36


def fetch(url, timeout=15):
    """抓取 URL，失败返回 None"""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        print("[WARN] fetch failed %s -> %s" % (url, e))
        return None


def clean(text):
    if not text:
        return ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def parse_rss(xml, source):
    items = []
    if not xml:
        return items
    blocks = re.findall(r"<item[\s>].*?</item>", xml, flags=re.S)
    if not blocks:
        blocks = re.findall(r"<entry[\s>].*?</entry>", xml, flags=re.S)

    for b in blocks[:12]:
        def pick(*tags):
            for t in tags:
                m = re.search(r"<%s[^>]*>(.*?)</%s>" % (t, t), b, flags=re.S)
                if m:
                    return clean(m.group(1))
            return ""

        title = pick("title")
        desc = pick("description", "summary", "content:encoded", "content")
        pub = pick("pubDate", "published", "updated", "dc:date")
        if not title:
            continue

        dt = None
        if pub:
            for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(pub.strip(), fmt)
                    break
                except Exception:
                    continue
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

        if len(desc) > 150:
            desc = desc[:150].rstrip() + "…"
        items.append((title, desc, source, dt))
    return items


def recent(items):
    now = datetime.now(timezone.utc)
    out = []
    for it in items:
        dt = it[3]
        if dt is None:
            out.append(it)
        else:
            try:
                if (now - dt).total_seconds() <= HOURS_WINDOW * 3600:
                    out.append(it)
            except Exception:
                out.append(it)
    return out


def collect_news():
    sections = {}
    for sec, feeds in FEEDS.items():
        pool, seen = [], set()
        for src_name, url in feeds:
            for it in recent(parse_rss(fetch(url), src_name)):
                key = it[0][:24]
                if key in seen:
                    continue
                seen.add(key)
                pool.append(it)
            if len(pool) >= TOP_N * 2:
                break
        pool.sort(key=lambda x: x[3] or datetime(1970, 1, 1, tzinfo=timezone.utc),
                  reverse=True)
        sections[sec] = pool[:TOP_N]
    return sections


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(sections, date_str):
    parts = [("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
        "<title>每日资讯简报 - __DATE__</title></head>"
        "<body style=\"margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,"
        "BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        "style=\"background:#f5f6f8;padding:24px 12px;\"><tr><td align=\"center\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" "
        "style=\"max-width:680px;background:#ffffff;border-radius:10px;overflow:hidden;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.06);\">"
        "<tr><td style=\"background:#1a3a6b;padding:26px 28px;\">"
        "<h1 style=\"margin:0;font-size:23px;color:#ffffff;font-weight:600;"
        "letter-spacing:1px;\">每日资讯简报</h1>"
        "<p style=\"margin:8px 0 0;font-size:13px;color:#b8c6de;\">__DATE__</p>"
        "</td></tr><tr><td style=\"padding:24px 28px 8px;\">")]

    cn = ["一", "二", "三", "四", "五", "六"]
    idx = 0
    for sec, items in sections.items():
        if not items:
            continue
        parts.append('<h2 style="margin:0 0 14px;font-size:17px;color:#1a3a6b;'
                     'border-left:4px solid #1a3a6b;padding-left:10px;">%s、%s</h2>'
                     % (cn[idx], esc(sec)))
        idx += 1
        for i, (title, desc, source, dt) in enumerate(items, 1):
            parts.append('<h3 style="margin:0 0 6px;font-size:14px;color:#222;">%d. %s</h3>'
                         % (i, esc(title)))
            if desc:
                parts.append('<p style="margin:0 0 4px;font-size:13px;line-height:1.75;'
                             'color:#444;">%s</p>' % esc(desc))
            parts.append('<p style="margin:0 0 16px;font-size:12px;color:#888;">'
                         '来源：%s</p>' % esc(source))

    parts.append("<p style=\"margin:22px 0 0;padding-top:14px;border-top:1px solid #e8e8e8;"
                 "font-size:12px;color:#999;text-align:center;\">"
                 "本简报由 WorkBuddy AI 自动生成，仅供参考</p>"
                 "</td></tr></table></td></tr></table></body></html>")
    return "".join(parts).replace("__DATE__", date_str)


def build_text(sections, date_str):
    """纯文本版，用于微信推送"""
    lines = ["每日资讯简报 %s" % date_str, ""]
    for sec, items in sections.items():
        if not items:
            continue
        lines.append("【%s】" % sec)
        for i, (title, desc, source, dt) in enumerate(items, 1):
            lines.append("%d. %s" % (i, title))
            if desc:
                lines.append("   %s" % desc)
        lines.append("")
    lines.append("本简报由 WorkBuddy AI 自动生成，仅供参考")
    return "\n".join(lines)


def send_mail(html, date_str):
    if not (MAIL_USER and MAIL_PASS and MAIL_TO):
        print("[SKIP] 邮件配置不完整，跳过邮件发送")
        return False
    m = MIMEMultipart("alternative")
    m["From"] = MAIL_USER
    m["To"] = MAIL_TO
    m["Subject"] = Header("每日资讯简报 - " + date_str, "utf-8")
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()
    m.attach(MIMEText(plain, "plain", "utf-8"))
    m.attach(MIMEText(html, "html", "utf-8"))
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30,
                         context=ssl.create_default_context())
    s.login(MAIL_USER, MAIL_PASS)
    s.sendmail(MAIL_USER, [MAIL_TO], m.as_string())
    s.quit()
    print("[OK] 邮件已发送 -> %s" % MAIL_TO)
    return True


def send_pushplus(text, date_str):
    """通过 pushplus 推送到微信"""
    if not PUSHPLUS_TOKEN:
        print("[SKIP] 未配置 PUSHPLUS_TOKEN，跳过微信推送")
        return False
    body = json.dumps({
        "token": PUSHPLUS_TOKEN,
        "title": "每日资讯简报 %s" % date_str,
        "content": text,
        "template": "txt",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = r.read().decode("utf-8", errors="ignore")
    print("[OK] 微信推送已发送 -> %s" % resp[:200])
    return True


def main():
    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    print("[INFO] ===== 开始执行 每日资讯简报 =====")
    print("[INFO] 日期: %s" % date_str)
    print("[INFO] 邮件配置: MAIL_USER=%s, MAIL_TO=%s, MAIL_PASS=%s" % (
        "已配置" if MAIL_USER else "缺失",
        "已配置" if MAIL_TO else "缺失",
        "已配置" if MAIL_PASS else "缺失",
    ))
    print("[INFO] 微信推送配置: PUSHPLUS_TOKEN=%s" % (
        "已配置" if PUSHPLUS_TOKEN else "缺失"))

    sections = collect_news()
    total = sum(len(v) for v in sections.values())
    print("[INFO] 共抓取 %d 条资讯" % total)
    for sec, items in sections.items():
        print("[INFO]   %s: %d 条" % (sec, len(items)))

    if total == 0:
        print("[ERROR] 未抓取到任何资讯（可能是 RSS 源暂时不可用）")
        sys.exit(1)

    html = build_html(sections, date_str)
    text = build_text(sections, date_str)

    with open("briefing_%s.html" % date_str, "w", encoding="utf-8") as f:
        f.write(html)
    print("[INFO] HTML 简报已生成: briefing_%s.html" % date_str)

    sent = False
    try:
        sent = send_mail(html, date_str) or sent
    except Exception as e:
        print("[ERROR] 邮件发送失败: %s: %s" % (type(e).__name__, e))

    try:
        sent = send_pushplus(text, date_str) or sent
    except Exception as e:
        print("[ERROR] 微信推送失败: %s: %s" % (type(e).__name__, e))

    if not sent:
        print("[ERROR] 没有任何推送渠道成功")
        print("[ERROR] 请检查：1) Secrets 是否配置 2) 名称是否完全一致（大小写敏感）")
        sys.exit(1)
    print("[DONE] 全部完成")


if __name__ == "__main__":
    main()
