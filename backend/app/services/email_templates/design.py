"""CrazzyDocs email design system.

One visual language for every operational email: a dark brand header (matching the Control Center),
a white content card, restrained status colours and table-based layout that renders in Gmail,
Outlook (Word engine), Apple Mail and mobile clients.

Rules every template follows:
* every dynamic value goes through :func:`esc` (HTML) or :func:`plain` (text);
* a value that is unknown is ``None`` and the row/metric is omitted, or shown as
  ``Unavailable`` where its absence matters. Zero is a value, never a stand-in for missing;
* inline styles only (a tiny ``<style>`` block carries the optional mobile tweaks);
* no JavaScript, no external CSS, no remote images. The only image is the brand mark (and an
  optional thumbnail) carried as a ``data:`` URI in the stored HTML; ``notification_service``
  converts those to CID inline attachments at send time, because Gmail strips ``data:`` images.
"""
from __future__ import annotations

import base64
import html as _html
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
TZ_NAME = "Asia/Kolkata"
UNAVAILABLE = "Unavailable"
BRAND = "CrazzyDocs"
PRODUCT = "CrazzyDocs Autonomous Studio"
DASHBOARD_URL = os.environ.get("CRAZZYDOCS_DASHBOARD_URL", "http://127.0.0.1:8000/")

# ---- design tokens --------------------------------------------------------------------------------------------
FONT = "'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO = "Consolas, 'SFMono-Regular', Menlo, Monaco, 'Courier New', monospace"
C = {
    "page": "#eef1f6", "card": "#ffffff", "border": "#e2e8f0", "divider": "#edf1f6",
    "text": "#0f172a", "body": "#1e293b", "muted": "#475569", "subtle": "#64748b",
    "header": "#0b1220", "header_text": "#f8fafc", "header_muted": "#a5b4c8", "brand": "#fee004",
    "surface": "#f8fafc", "link": "#1d4ed8",
}
# tone -> (text/accent, background, border, label)
TONES = {
    "success": ("#166534", "#ecfdf3", "#bbf7d0", "Completed"),
    "danger": ("#b91c1c", "#fef2f2", "#fecaca", "Failed"),
    "warning": ("#92400e", "#fffbeb", "#fde68a", "Attention"),
    "info": ("#1d4ed8", "#eff6ff", "#bfdbfe", "Information"),
    "neutral": ("#334155", "#f1f5f9", "#e2e8f0", "Status"),
}


# ---- escaping and formatting ------------------------------------------------------------------------------------
def esc(value: Any) -> str:
    """HTML-escape any value (``None`` -> empty)."""
    return "" if value is None else _html.escape(str(value), quote=True)


def plain(value: Any) -> str:
    """Plain-text rendering: single-line, no control characters."""
    if value is None:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))


def clip(text: Any, limit: int) -> Optional[str]:
    if text is None:
        return None
    s = str(text).strip()
    return s if len(s) <= limit else s[: max(0, limit - 1)].rstrip() + "…"


def to_ist(value: Any) -> Optional[datetime]:
    """datetime (naive = UTC, as stored by SQLAlchemy) or ISO string -> aware IST datetime."""
    if value is None or value == "":
        return None
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def fmt_datetime(value: Any) -> Optional[str]:
    """'24 Nov 2026, 17:11 IST'."""
    dt = to_ist(value)
    return f"{dt.day} {dt.strftime('%b %Y, %H:%M')} IST" if dt else None


def fmt_time(value: Any) -> Optional[str]:
    """'17:11 IST' (strings that are already a clock time such as '17:00:01 IST' are kept)."""
    if isinstance(value, str) and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?( IST)?", value.strip()):
        v = value.strip()
        return v if v.endswith("IST") else f"{v} IST"
    dt = to_ist(value)
    return dt.strftime("%H:%M IST") if dt else None


def fmt_clock(value: Any) -> Optional[str]:
    """'17:11:31 IST' (seconds shown, for stage timelines)."""
    dt = to_ist(value)
    return dt.strftime("%H:%M:%S IST") if dt else fmt_time(value)


def fmt_date(value: Any) -> Optional[str]:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        d = datetime.strptime(value, "%Y-%m-%d")
        return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"
    dt = to_ist(value)
    return f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}" if dt else None


def fmt_duration(seconds: Any) -> Optional[str]:
    """52.3 s / 11 min 16 s / 1 h 04 min. None/negative/unparseable -> None (0 -> '0 s')."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return None
    if s < 0:
        return None
    if s < 60:
        return f"{s:.1f} s" if s % 1 else f"{int(s)} s"
    total = int(round(s))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min {sec:02d} s"


def fmt_count(n: Any) -> Optional[str]:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return None


def fmt_pct(num: Any, den: Any) -> Optional[str]:
    try:
        num, den = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if den <= 0:
        return None
    return f"{round(100.0 * num / den):d}%"


def fmt_score(value: Any, scale: int = 10) -> Optional[str]:
    try:
        return f"{float(value):.2f} / {scale}"
    except (TypeError, ValueError):
        return None


def humanize(code: Any) -> Optional[str]:
    """'STORY_DEVELOPMENT' -> 'Story development'."""
    if code is None or code == "":
        return None
    s = str(code).replace("_", " ").strip()
    return s[:1].upper() + s[1:].lower() if s.isupper() or "_" in str(code) else s


def or_unavailable(value: Optional[str]) -> str:
    return value if value not in (None, "") else UNAVAILABLE


# ---- brand mark -------------------------------------------------------------------------------------------------
_LOGO_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), "assets", "crazzydocs_mark_80.png"),
)


@lru_cache(maxsize=1)
def logo_data_uri() -> Optional[str]:
    """Small (80 px) brand mark as a data URI, or None when the asset is missing (header then shows text only)."""
    for path in _LOGO_CANDIDATES:
        try:
            if os.path.isfile(path) and os.path.getsize(path) < 64 * 1024:
                with open(path, "rb") as fh:
                    return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            continue
    return None


# ---- rendered output -------------------------------------------------------------------------------------------
@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str

    def as_tuple(self) -> Tuple[str, str, str]:
        return self.subject, self.text, self.html


@dataclass
class Block:
    """One section of an email, in both formats."""
    html: str
    text: str


Value = Union[str, None]


# ---- components -------------------------------------------------------------------------------------------------
def tag(name: str, style: str, content: str = "", **attrs: Any) -> str:
    """Build one element with an inline style. Attribute values are escaped; ``content`` is trusted HTML."""
    extra = "".join(' %s="%s"' % (k.rstrip("_").replace("_", "-"), esc(v)) for k, v in attrs.items() if v is not None)
    return '<%s style="%s"%s>%s</%s>' % (name, style, extra, content, name)


def ptable(rows_html: str, style: str = "", **attrs: Any) -> str:
    """Presentation table (width 100%, no spacing)."""
    extra = "".join(' %s="%s"' % (k.rstrip("_").replace("_", "-"), esc(v)) for k, v in attrs.items() if v is not None)
    st = ' style="%s"' % style if style else ""
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"%s%s>%s</table>'
            % (st, extra, rows_html))


def _box_style(bg: str, border: str, left: Optional[str] = None, radius: int = 8) -> str:
    s = "border-collapse:separate;background-color:%s;border:1px solid %s;border-radius:%dpx;" % (bg, border, radius)
    if left:
        s += "border-left:4px solid %s;" % left
    return s


def status_banner(tone: str, headline: str, description: Optional[str] = None) -> Block:
    fg, bg, border, _ = TONES.get(tone, TONES["neutral"])
    inner = tag("div", "font-size:16px;line-height:22px;font-weight:700;color:%s;" % fg, esc(headline))
    if description:
        inner += tag("div", "font-size:14px;line-height:21px;color:%s;margin-top:4px;" % C["body"], esc(description))
    html = ptable("<tr>" + tag("td", "padding:14px 16px;", inner) + "</tr>", _box_style(bg, border, left=fg))
    text = "STATUS: " + plain(headline) + ("\n" + plain(description) if description else "")
    return Block(html, text)


def metric_grid(items: Sequence[Tuple[str, Value, Optional[str]]], columns: int = 2) -> Block:
    """Metric cards. Items whose value is None are omitted (never shown as 0)."""
    shown = [(label, value, hint) for label, value, hint in items if value not in (None, "")]
    if not shown:
        return Block("", "")
    width = "%d%%" % int(100 / columns)
    label_st = ("font-size:11px;line-height:15px;font-weight:600;letter-spacing:0.4px;text-transform:uppercase;color:%s;"
                % C["subtle"])
    value_st = "font-size:20px;line-height:26px;font-weight:700;color:%s;margin-top:2px;word-break:break-word;" % C["text"]
    hint_st = "font-size:12px;line-height:16px;color:%s;margin-top:2px;" % C["subtle"]
    rows = []
    for i in range(0, len(shown), columns):
        cells = []
        for label, value, hint in shown[i:i + columns]:
            card = tag("div", label_st, esc(label)) + tag("div", value_st, esc(value))
            if hint:
                card += tag("div", hint_st, esc(hint))
            box = ptable("<tr>" + tag("td", "padding:12px 14px;", card) + "</tr>", _box_style(C["surface"], C["border"]))
            cells.append(tag("td", "padding:4px;", box, width=width, valign="top"))
        while len(cells) < columns:
            cells.append(tag("td", "padding:4px;", "&nbsp;", width=width))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    w = max(len(l) for l, _, _ in shown)
    text = "\n".join("  %s  %s%s" % (plain(l).ljust(w), plain(v), "  (%s)" % plain(h) if h else "") for l, v, h in shown)
    return Block(ptable("".join(rows)), text)


def kv_table(rows: Iterable[Tuple[str, Value]]) -> Block:
    """Label/value rows; rows whose value is None are omitted."""
    shown = [(l, v) for l, v in rows if v not in (None, "")]
    if not shown:
        return Block("", "")
    trs = []
    for i, (label, value) in enumerate(shown):
        border = "border-top:1px solid %s;" % C["divider"] if i else ""
        trs.append("<tr>"
                   + tag("td", "padding:8px 12px 8px 0;font-size:13px;line-height:19px;color:%s;%s" % (C["muted"], border),
                         esc(label), width="38%", valign="top")
                   + tag("td", "padding:8px 0;font-size:14px;line-height:20px;color:%s;word-break:break-word;%s" % (C["text"], border),
                         esc(value), valign="top")
                   + "</tr>")
    w = max(len(l) for l, _ in shown)
    return Block(ptable("".join(trs)), "\n".join("  %s  %s" % (plain(l).ljust(w), plain(v)) for l, v in shown))


def pill(text_value: str, tone: str) -> str:
    fg, bg, border, _ = TONES.get(tone, TONES["neutral"])
    st = ("display:inline-block;padding:2px 9px;border-radius:999px;border:1px solid %s;background-color:%s;color:%s;"
          "font-size:12px;line-height:18px;font-weight:600;white-space:nowrap;" % (border, bg, fg))
    return tag("span", st, esc(text_value))


@dataclass
class ListRow:
    """A stacked row that stays readable at 320 px: title line + status pill, then detail lines."""
    title: str
    status: Optional[str] = None
    tone: str = "neutral"
    eyebrow: Optional[str] = None          # e.g. 'Cycle 18 · 17:00 IST'
    lines: List[str] = field(default_factory=list)
    note: Optional[str] = None             # e.g. failure reason (shown in the tone colour)


def stacked_list(rows: Sequence[ListRow]) -> Block:
    if not rows:
        return Block("", "")
    out, text = [], []
    eyebrow_st = "font-size:12px;line-height:16px;color:%s;font-weight:600;" % C["subtle"]
    title_st = "font-size:14px;line-height:20px;font-weight:600;color:%s;word-break:break-word;" % C["text"]
    line_st = "font-size:13px;line-height:19px;color:%s;" % C["muted"]
    for i, r in enumerate(rows):
        border = "border-top:1px solid %s;" % C["divider"] if i else ""
        fg = TONES.get(r.tone, TONES["neutral"])[0]
        left = (tag("div", eyebrow_st, esc(r.eyebrow)) if r.eyebrow else "") + tag("div", title_st, esc(r.title))
        head = ptable("<tr>" + tag("td", "", left, valign="top")
                      + tag("td", "padding-left:10px;white-space:nowrap;", pill(r.status, r.tone) if r.status else "",
                            valign="top", align="right", width="1%") + "</tr>")
        detail = "".join(tag("div", line_st, esc(l)) for l in r.lines if l)
        if r.note:
            detail += tag("div", "font-size:13px;line-height:19px;color:%s;margin-top:2px;word-break:break-word;" % fg, esc(r.note))
        out.append("<tr>" + tag("td", "padding:11px 0;" + border, head + detail) + "</tr>")
        h = " | ".join(x for x in (r.eyebrow, r.status) if x)
        text.append("  - %s\n    %s" % (plain(h), plain(r.title))
                    + "".join("\n    " + plain(l) for l in r.lines if l)
                    + ("\n    " + plain(r.note) if r.note else ""))
    return Block(ptable("".join(out)), "\n".join(text))


def callout(tone: str, title: str, body: Optional[str] = None, items: Sequence[str] = ()) -> Block:
    fg, bg, border, _ = TONES.get(tone, TONES["neutral"])
    inner = tag("div", "font-size:13px;line-height:18px;font-weight:700;color:%s;" % fg, esc(title))
    if body:
        inner += tag("div", "font-size:14px;line-height:21px;color:%s;margin-top:4px;" % C["body"], esc(body))
    for it in items:
        inner += tag("div", "font-size:13px;line-height:19px;color:%s;margin-top:4px;word-break:break-word;" % C["body"],
                     "&#8226;&nbsp;" + esc(it))
    html = ptable("<tr>" + tag("td", "padding:12px 14px;", inner) + "</tr>", _box_style(bg, border))
    text = plain(title).upper() + ("\n" + plain(body) if body else "") + "".join("\n  - " + plain(i) for i in items)
    return Block(html, text)


def tech_details(text_value: Optional[str], limit: int = 900) -> Block:
    """Separated technical detail (already redacted by the caller); clipped, monospace, wraps."""
    if not text_value:
        return Block("", "")
    v = str(text_value).strip()
    if len(v) > limit:
        # Keep the start (what failed) and the end (the final exception line of a traceback).
        head, tail = v[: limit // 3], v[-(2 * limit) // 3:]
        omitted = len(v) - len(head) - len(tail)
        v = head.rstrip() + "\n… (%d characters omitted) …\n" % omitted + tail.lstrip()
    st = ("background-color:%s;border:1px solid %s;border-radius:6px;padding:10px 12px;font-family:%s;font-size:12px;"
          "line-height:18px;color:%s;white-space:pre-wrap;word-break:break-word;" % (C["surface"], C["border"], MONO, C["body"]))
    return Block(tag("div", st, esc(v)), "\n".join("  " + plain(l) for l in v.splitlines()))


def paragraph(text_value: Optional[str]) -> Block:
    if not text_value:
        return Block("", "")
    return Block(tag("p", "margin:0;font-size:14px;line-height:22px;color:%s;" % C["body"], esc(text_value)), plain(text_value))


def image(data_uri: Optional[str], alt: str, caption: Optional[str] = None, width: int = 240) -> Block:
    if not data_uri:
        return Block("", "")
    img = ('<img src="%s" width="%d" alt="%s" style="display:block;margin:0 auto;width:%dpx;max-width:100%%;height:auto;'
           'border:0;border-radius:8px;" />' % (esc(data_uri), width, esc(alt), width))
    if caption:
        img += tag("div", "font-size:12px;line-height:17px;color:%s;margin-top:6px;" % C["subtle"], esc(caption))
    return Block(tag("div", "text-align:center;", img), "")


@dataclass
class Section:
    title: Optional[str]
    blocks: List[Block]


def section(title: Optional[str], *blocks: Block) -> Optional[Section]:
    kept = [b for b in blocks if b and (b.html or b.text)]
    return Section(title, kept) if kept else None


# ---- document ---------------------------------------------------------------------------------------------------
_HEAD_STYLE = (
    "body{margin:0;padding:0;}"
    "table,td{mso-table-lspace:0pt;mso-table-rspace:0pt;}"
    "img{-ms-interpolation-mode:bicubic;}"
    "@media only screen and (max-width:620px){"
    ".cd-outer{padding:0 !important;}"
    ".cd-pad{padding-left:18px !important;padding-right:18px !important;}"
    ".cd-title{font-size:20px !important;line-height:27px !important;}"
    ".cd-card{border-radius:0 !important;border-left:0 !important;border-right:0 !important;}"
    ".cd-head{border-radius:0 !important;}"
    "}"
)


def document(*, subject: str, eyebrow: str, title: str, subtitle: Optional[str], preheader: Optional[str],
             sections: Sequence[Optional[Section]], generated_at: Optional[datetime] = None,
             footer_note: Optional[str] = None, dashboard_url: Optional[str] = DASHBOARD_URL) -> RenderedEmail:
    """Assemble the full HTML document and its plain-text twin."""
    gen_str = fmt_datetime(generated_at or datetime.now(timezone.utc)) or ""
    secs = [s for s in sections if s]

    logo = logo_data_uri()
    brand_cell = tag("td", "font-family:%s;" % FONT,
                     tag("div", "font-size:16px;line-height:20px;font-weight:700;color:%s;" % C["header_text"],
                         "CrazzyDocs" + tag("span", "color:%s;" % C["brand"], "."))
                     + tag("div", "font-size:12px;line-height:16px;color:%s;" % C["header_muted"], esc(eyebrow)),
                     valign="middle")
    logo_cell = ""
    if logo:
        logo_cell = tag("td", "padding-right:12px;",
                        '<img src="%s" width="40" height="40" alt="CrazzyDocs logo" '
                        'style="display:block;width:40px;height:40px;border:0;border-radius:20px;" />' % esc(logo),
                        width="52", valign="middle")
    header_inner = (ptable("<tr>" + logo_cell + brand_cell + "</tr>")
                    + tag("div", "font-size:22px;line-height:30px;font-weight:700;color:%s;margin-top:16px;word-break:break-word;"
                          % C["header_text"], esc(title), class_="cd-title"))
    if subtitle:
        header_inner += tag("div", "font-size:13px;line-height:19px;color:%s;margin-top:4px;" % C["header_muted"], esc(subtitle))
    header = "<tr>" + tag("td", "background-color:%s;border-radius:11px 11px 0 0;padding:20px 28px 22px 28px;" % C["header"],
                          header_inner, class_="cd-pad cd-head", bgcolor=C["header"]) + "</tr>"

    pre = ""
    if preheader:
        pre = tag("div", "display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:%s;opacity:0;"
                  % C["page"], esc(preheader))

    sec_head_st = ("font-size:12px;line-height:16px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;color:%s;"
                   "padding-bottom:10px;" % C["subtle"])
    body_rows = ["<tr>" + tag("td", "height:20px;line-height:20px;font-size:0;", "&nbsp;") + "</tr>"]
    for i, s in enumerate(secs):
        inner = tag("div", sec_head_st, esc(s.title)) if s.title else ""
        inner += "".join(tag("div", "padding-top:8px;" if j else "", b.html) for j, b in enumerate(s.blocks) if b.html)
        top = "padding-top:26px;" if i else "padding-top:4px;"
        body_rows.append("<tr>" + tag("td", "padding-left:28px;padding-right:28px;" + top, inner, class_="cd-pad") + "</tr>")

    foot = (tag("strong", "color:%s;" % C["muted"], PRODUCT) + " &middot; Automated operational report<br/>"
            + "Generated %s &middot; All times %s (IST)" % (esc(gen_str), TZ_NAME))
    if footer_note:
        foot += "<br/>" + esc(footer_note)
    if dashboard_url:
        foot += ("<br/>Control Center (studio PC): "
                 + tag("a", "color:%s;text-decoration:underline;" % C["link"], esc(dashboard_url), href=dashboard_url))
    body_rows.append("<tr>" + tag("td", "padding:28px 28px 24px 28px;",
                                  tag("div", "border-top:1px solid %s;padding-top:14px;font-size:12px;line-height:18px;color:%s;"
                                      % (C["border"], C["subtle"]), foot), class_="cd-pad") + "</tr>")

    card = ptable(header + "".join(body_rows),
                  "max-width:640px;width:100%%;border-collapse:separate;background-color:%s;border:1px solid %s;border-radius:12px;"
                  % (C["card"], C["border"]), class_="cd-card")
    outer = ptable("<tr>" + tag("td", "padding:24px 12px;",
                                "<!--[if mso]><table role=\"presentation\" width=\"640\" cellpadding=\"0\" cellspacing=\"0\" "
                                "border=\"0\"><tr><td><![endif]-->" + card + "<!--[if mso]></td></tr></table><![endif]-->",
                                align="center", class_="cd-outer") + "</tr>",
                   "background-color:%s;" % C["page"], bgcolor=C["page"])
    html = ("<!DOCTYPE html>\n"
            '<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:o="urn:schemas-microsoft-com:office:office">\n<head>\n'
            '<meta charset="utf-8" />\n<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
            '<meta http-equiv="X-UA-Compatible" content="IE=edge" />\n<meta name="x-apple-disable-message-reformatting" />\n'
            '<meta name="color-scheme" content="light" />\n<meta name="supported-color-schemes" content="light" />\n'
            "<title>%s</title>\n<style type=\"text/css\">%s</style>\n</head>\n"
            '<body bgcolor="%s" style="margin:0;padding:0;background-color:%s;font-family:%s;color:%s;-webkit-text-size-adjust:100%%;">\n'
            "%s\n%s\n</body>\n</html>"
            % (esc(subject), _HEAD_STYLE, C["page"], C["page"], FONT, C["text"], pre, outer))

    # ---- plain text ----
    rule = "=" * 64
    lines = [rule, "%s | %s" % (BRAND.upper(), plain(eyebrow).upper()), plain(title)]
    if subtitle:
        lines.append(plain(subtitle))
    lines.append(rule)
    for s in secs:
        lines.append("")
        if s.title:
            lines += [plain(s.title).upper(), "-" * min(64, max(8, len(s.title)))]
        lines += [b.text for b in s.blocks if b.text]
    lines += ["", "-" * 64, "%s - automated operational report" % PRODUCT,
              "Generated %s. All times %s (IST)." % (gen_str, TZ_NAME)]
    if footer_note:
        lines.append(plain(footer_note))
    if dashboard_url:
        lines.append("Control Center (studio PC): %s" % dashboard_url)
    return RenderedEmail(subject=subject, text="\n".join(lines) + "\n", html=html)
