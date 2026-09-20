"""Orchestrator: poll Gmail, fulfil document requests, reply with a ZIP.

Loop per message: parse -> scrape -> zip -> reply -> mark read. A message is only
marked read once the reply has actually been sent, so a crash mid-request leaves
it to be retried rather than silently dropped.
"""
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

import mailer
from packager import make_zip, verify_zip
from parser import ParseError, parse_request
from reply import format_error, format_reply
from scraper import MatterNotFound, fetch

load_dotenv()

POLL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
MAX_DOCUMENTS = int(os.environ.get("MAX_DOCUMENTS", "10"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def handle(service, msg_id, agent_address):
    """Process one message. Returns True if it was fully handled."""
    msg = mailer.get_message(service, msg_id)
    sender = msg["sender"]
    log(f"message {msg_id} from {sender}: {msg['subject']!r}")

    # Never act on our own mail, or we would answer our own replies forever.
    if sender.lower() == agent_address.lower():
        log("  skipping: message is from the agent itself")
        return True

    # Bounces and no-reply notifications must not be answered.
    if mailer.is_automated(sender):
        log("  skipping: automated sender")
        return True

    text = f"{msg['subject']}\n\n{msg['body']}"
    spam = mailer.is_spam(msg)

    try:
        matter, category = parse_request(text)
    except ParseError as e:
        log(f"  unparseable: {e}")
        # Genuine spam that is not a request gets silently dropped; replying to
        # it would both be noise and put the agent's account at risk.
        if spam:
            log("  in spam and not a valid request -- ignoring without reply")
            return True
        subject, body = format_error(str(e), msg["subject"])
        mailer.send_reply(service, sender, subject, body,
                          thread_id=msg["thread_id"], in_reply_to=msg["message_id"])
        log(f"  replied with guidance to {sender}")
        return True

    # A real request that Gmail misfiled: rescue it so the reply threads properly.
    if spam:
        log("  valid request found in spam -- moving to inbox")
        try:
            mailer.unmark_spam(service, msg_id)
        except Exception as e:
            log(f"  could not unmark spam: {e}")

    log(f"  parsed: {matter} / {category}")
    workdir = Path(tempfile.mkdtemp(prefix=f"{matter}_{category.replace(' ', '_')}_"))
    try:
        try:
            info, files, failed = fetch(matter, category, workdir,
                                        MAX_DOCUMENTS, HEADLESS, log)
        except (MatterNotFound, ValueError) as e:
            log(f"  lookup failed: {e}")
            subject, body = format_error(str(e), msg["subject"])
            mailer.send_reply(service, sender, subject, body,
                              thread_id=msg["thread_id"], in_reply_to=msg["message_id"])
            return True

        zip_path = None
        if files:
            zip_path = workdir / f"{info['matter_no']}_{category.replace(' ', '_')}.zip"
            make_zip(files, zip_path)
            verify_zip(zip_path, files)
            log(f"  zipped {len(files)} file(s) -> {zip_path.stat().st_size:,} bytes")

        subject, body = format_reply(info, category, len(files), failed,
                                     zip_path.name if zip_path else None, MAX_DOCUMENTS)
        mailer.send_reply(service, sender, subject, body, attachment=zip_path,
                          thread_id=msg["thread_id"], in_reply_to=msg["message_id"])
        log(f"  replied to {sender} with {len(files)} document(s)")
        return True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    service = mailer.get_service()
    agent_address = mailer.get_address(service)
    once = "--once" in sys.argv
    log(f"agent ready as {agent_address} (poll {POLL_SECONDS}s, max {MAX_DOCUMENTS} docs)")

    while True:
        try:
            for msg_id in mailer.list_requests(service):
                try:
                    if handle(service, msg_id, agent_address):
                        mailer.mark_read(service, msg_id)
                except Exception:
                    # Leave it unread so the next pass retries it.
                    log(f"  ERROR handling {msg_id}:\n{traceback.format_exc()}")
        except Exception:
            log(f"poll failed:\n{traceback.format_exc()}")

        if once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
