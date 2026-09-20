"""Gmail API access for the agent: read incoming requests, reply with a ZIP.

Uses desktop OAuth. credentials.json is the client secret downloaded from Google
Cloud; token.json is created on first authorisation and refreshed automatically.
Neither file is ever committed.
"""
import base64
import html
import os
import re
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Automated senders must never get a reply -- answering a bounce or a no-reply
# notification is at best noise and at worst a mail loop.
NOREPLY_RE = re.compile(
    r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce|notifications?@)",
    re.IGNORECASE)


def is_automated(address):
    return bool(NOREPLY_RE.search(address or ""))

CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.environ.get("GMAIL_TOKEN_FILE", "token.json")

# Gmail rejects messages over 25MB, and base64 inflates an attachment by ~33%,
# so the raw file has to stay meaningfully under that.
MAX_ATTACHMENT_BYTES = 17 * 1024 * 1024


def get_service(credentials_file=None, token_file=None):
    """Authorise (opening a browser on first run) and return a Gmail client."""
    credentials_file = credentials_file or CREDENTIALS_FILE
    token_file = token_file or TOKEN_FILE
    creds = None

    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # On a server there is no browser to complete the consent flow, and
            # run_local_server would block until the job timed out.
            if os.environ.get("NON_INTERACTIVE"):
                raise RuntimeError(
                    "no usable token and NON_INTERACTIVE is set -- the stored "
                    "refresh token is missing or revoked. Re-run "
                    "'python agent/mailer.py auth' locally and update the "
                    "GMAIL_TOKEN secret.")
            if not Path(credentials_file).exists():
                raise FileNotFoundError(
                    f"{credentials_file} not found -- download the desktop OAuth "
                    "client secret from Google Cloud Console and save it there")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        try:
            Path(token_file).write_text(creds.to_json(), encoding="utf-8")
        except OSError:
            pass  # read-only filesystem; the refresh token in the secret still works

    return build("gmail", "v1", credentials=creds)


def get_address(service):
    """The agent's own email address."""
    return service.users().getProfile(userId="me").execute()["emailAddress"]


def _decode(data):
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _extract_body(payload):
    """Depth-first search for text/plain, falling back to de-tagged text/html."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_decode(data))
            elif mime == "text/html":
                rich.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    if rich:
        text = html.unescape(re.sub(r"<[^>]+>", " ", "\n".join(rich)))
        return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return ""


def list_requests(service, query="is:unread", max_results=10, include_spam=True):
    """Unread messages addressed to the agent, newest first.

    Spam is included by default: a brand-new Gmail account frequently files
    legitimate first-contact mail as spam, which would otherwise make the agent
    look dead to anyone emailing it for the first time. Callers are expected to
    treat spam conservatively -- see is_spam().
    """
    res = service.users().messages().list(
        userId="me", q=query, maxResults=max_results,
        includeSpamTrash=include_spam).execute()
    return [m["id"] for m in res.get("messages", [])]


def is_spam(msg):
    return "SPAM" in (msg.get("labels") or [])


def unmark_spam(service, msg_id):
    """Rescue a legitimate request Gmail filed as spam, so the reply threads."""
    service.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["SPAM"], "addLabelIds": ["INBOX"]}).execute()


def get_message(service, msg_id):
    """Fetch a message and flatten it into the fields the agent needs."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    return {
        "id": msg_id,
        "thread_id": msg.get("threadId"),
        "from": headers.get("from", ""),
        "sender": parseaddr(headers.get("from", ""))[1],
        "subject": headers.get("subject", ""),
        "message_id": headers.get("message-id", ""),
        "labels": msg.get("labelIds", []),
        "body": _extract_body(msg["payload"]),
    }


def build_message(to, subject, body, attachment=None, in_reply_to=None):
    """Compose the reply. Separate from sending so it can be tested offline."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    # Keep the reply in the requester's original thread.
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    if attachment:
        path = Path(attachment)
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"attachment {path.name} is {size:,} bytes, over the "
                f"{MAX_ATTACHMENT_BYTES:,} byte limit")
        msg.add_attachment(path.read_bytes(), maintype="application",
                           subtype="zip", filename=path.name)
    return msg


def send_reply(service, to, subject, body, attachment=None, thread_id=None,
               in_reply_to=None):
    """Send a plain-text reply, optionally with one file attached."""
    msg = build_message(to, subject, body, attachment, in_reply_to)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return service.users().messages().send(userId="me", body=payload).execute()


def mark_read(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}).execute()


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "auth"
    svc = get_service()
    addr = get_address(svc)

    if cmd == "auth":
        print(f"authorised as: {addr}")
    elif cmd == "list":
        ids = list_requests(svc)
        print(f"{len(ids)} unread message(s)")
        for i in ids:
            m = get_message(svc, i)
            print(f"  from={m['sender']!r} subject={m['subject']!r}")
            print(f"    body: {m['body'][:120]!r}")
    elif cmd == "send":
        to = sys.argv[2] if len(sys.argv) > 2 else addr
        att = sys.argv[3] if len(sys.argv) > 3 else None
        send_reply(svc, to, "NSUARB agent test",
                   "Test message from the NSUARB document agent.", attachment=att)
        print(f"sent to {to}" + (f" with {att}" if att else ""))
    else:
        print(f"unknown command: {cmd} (use auth | list | send)")
