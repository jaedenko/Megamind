import email, imaplib, re, smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

# Plain SMTP/IMAP with an app password (Gmail: Google Account > Security > App passwords). No extra service, no DNS.
TAG = re.compile(r"\[Megamind post-(\d+)-([a-z0-9]+)\]")

def owner(cfg):
    return (cfg("MAIL_TO") or cfg("MAIL_USER") or "").strip().lower()

def configured(cfg):
    return bool(cfg("MAIL_USER") and cfg("MAIL_PASSWORD"))

def send(cfg, subject, body, in_reply_to=None):
    user = cfg("MAIL_USER")
    m = EmailMessage(); m["From"], m["To"], m["Subject"] = user, cfg("MAIL_TO") or user, subject
    m["Message-ID"] = mid = make_msgid(domain=user.split("@")[-1])
    if in_reply_to: m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    with smtplib.SMTP_SSL(cfg("SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as s:
        s.login(user, cfg("MAIL_PASSWORD")); s.send_message(m)
    return mid

def reply_text(body):
    """Just what the person typed: drop quoted lines and everything from 'On <date> ... wrote:' / 'Original Message' down."""
    body = re.split(r"(?m)^On [^\n]{0,200}(?:\n[^\n]{0,200})?wrote:\s*$|^-{2,}\s*Original Message|^From: .+\nSent: ", body.replace("\r\n", "\n"))[0]
    return "\n".join(l for l in body.split("\n") if not l.lstrip().startswith(">")).strip()

def _text(msg):
    parts = [p for p in msg.walk() if p.get_content_maintype() == "text"]
    for want in ("text/plain", "text/html"):
        for p in parts:
            if p.get_content_type() == want:
                t = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", "", t) if want == "text/html" else t
    return ""

def poll(cfg, handle):
    """Calls handle(msg) for each unread message whose subject carries a Megamind tag; it returns True once the message is dealt
    with (then it is marked read) or False to see it again next time. Messages are fetched with PEEK so nothing is marked early."""
    with imaplib.IMAP4_SSL(cfg("IMAP_HOST", "imap.gmail.com"), 993, timeout=30) as im:
        im.login(cfg("MAIL_USER"), cfg("MAIL_PASSWORD")); im.select("INBOX")
        for num in im.search(None, "UNSEEN", "SUBJECT", '"[Megamind post-"')[1][0].split():
            msg = email.message_from_bytes(im.fetch(num, "(BODY.PEEK[])")[1][0][1])
            if handle({"message_id": (msg["Message-ID"] or "").strip(), "from": parseaddr(msg["From"] or "")[1].lower(),
                       "subject": str(make_header(decode_header(msg["Subject"] or ""))),
                       "refs": f'{msg["In-Reply-To"] or ""} {msg["References"] or ""}', "text": _text(msg)}):
                im.store(num, "+FLAGS", "\\Seen")
