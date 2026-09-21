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
from packager import make_zip_within, verify_zip
from parser import AmbiguousRequest, ParseError, parse_request
from reply import format_ambiguous, format_error, format_reply
from scraper import MatterNotFound, fetch

load_dotenv()

POLL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
MAX_DOCUMENTS = int(os.environ.get("MAX_DOCUMENTS", "10"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"

# A crashed browser or a site timeout is usually transient, so the message is
# left unread and retried. After this many failures it is answered with an
# apology instead, so a permanently failing request cannot loop forever.
MAX_ATTEMPTS = 3


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
    except AmbiguousRequest as e:
        log(f"  ambiguous: matters={e.matters} categories={e.categories}")
        if spam:
            log("  in spam and ambiguous -- ignoring without reply")
            return True
        subject, body = format_ambiguous(e.matters, e.categories, msg["subject"])
        mailer.send_reply(service, sender, subject, body,
                          thread_id=msg["thread_id"], in_reply_to=msg["message_id"])
        log(f"  asked {sender} to clarify")
        return True
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

        # Some matters hold filings of tens of MB each, and email cannot carry
        # them. Send as many as fit rather than refusing the whole archive.
        zip_path, omitted = None, []
        if files:
            target = workdir / f"{info['matter_no']}_{category.replace(' ', '_')}.zip"
            zip_path, included, omitted = make_zip_within(
                files, target, mailer.MAX_ATTACHMENT_BYTES)
            if zip_path:
                verify_zip(zip_path, included)
                log(f"  zipped {len(included)} file(s) -> "
                    f"{zip_path.stat().st_size:,} bytes")
            if omitted:
                log(f"  {len(omitted)} file(s) too large to attach")

        subject, body = format_reply(info, category, files, failed,
                                     zip_path.name if zip_path else None,
                                     MAX_DOCUMENTS, omitted)
        mailer.send_reply(service, sender, subject, body, attachment=zip_path,
                          thread_id=msg["thread_id"], in_reply_to=msg["message_id"])
        log(f"  replied to {sender} with {len(files) - len(omitted)} attached"
            + (f", {len(omitted)} omitted" if omitted else ""))
        return True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def notify_failure(service, msg_id, error):
    """Tell the requester their request could not be completed."""
    msg = mailer.get_message(service, msg_id)
    if mailer.is_automated(msg["sender"]) or mailer.is_spam(msg):
        return
    subject, body = format_error(
        f"the lookup failed repeatedly ({error}). This is usually a temporary "
        "problem with the board's website -- please try again shortly",
        msg["subject"])
    mailer.send_reply(service, msg["sender"], subject, body,
                      thread_id=msg["thread_id"], in_reply_to=msg["message_id"])


def connect(retries=3, delay=5):
    """Authorise and identify ourselves, tolerating a transient hiccup.

    These are the only unguarded network calls in the run: everything inside the
    polling loop is caught and logged. A momentary Google error here would
    otherwise fail the whole job, which on a scheduled deployment shows up as a
    red run for no real reason.
    """
    for attempt in range(1, retries + 1):
        try:
            service = mailer.get_service()
            return service, mailer.get_address(service)
        except Exception as e:
            if attempt == retries:
                log(f"could not reach Gmail after {retries} attempts: "
                    f"{type(e).__name__}: {e}")
                raise
            log(f"startup attempt {attempt}/{retries} failed "
                f"({type(e).__name__}); retrying in {delay}s")
            time.sleep(delay)


def main():
    service, agent_address = connect()
    once = "--once" in sys.argv
    log(f"agent ready as {agent_address} (poll {POLL_SECONDS}s, max {MAX_DOCUMENTS} docs)")

    attempts = {}
    while True:
        try:
            for msg_id in mailer.list_requests(service):
                try:
                    if handle(service, msg_id, agent_address):
                        mailer.mark_read(service, msg_id)
                        attempts.pop(msg_id, None)
                except Exception as e:
                    # Leave it unread so the next pass retries it.
                    n = attempts[msg_id] = attempts.get(msg_id, 0) + 1
                    log(f"  ERROR handling {msg_id} (attempt {n}/{MAX_ATTEMPTS}):\n"
                        f"{traceback.format_exc()}")
                    if n >= MAX_ATTEMPTS:
                        log(f"  giving up on {msg_id}; notifying sender")
                        try:
                            notify_failure(service, msg_id, f"{type(e).__name__}")
                            mailer.mark_read(service, msg_id)
                            attempts.pop(msg_id, None)
                        except Exception:
                            log(f"  could not notify sender:\n{traceback.format_exc()}")
        except Exception:
            log(f"poll failed:\n{traceback.format_exc()}")

        if once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
