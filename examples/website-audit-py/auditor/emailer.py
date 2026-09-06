EMAIL_SCRIPT = '''import json
import os
import smtplib
import sys
from email.message import EmailMessage

from_email = os.environ["OUTREACH_FROM_EMAIL"]
to_email = os.environ["OUTREACH_TO_EMAIL"]
subject = os.environ["OUTREACH_SUBJECT"]
body = os.environ["OUTREACH_BODY"]
host = os.environ["SMTP_HOST"]
port = int(os.environ["SMTP_PORT"])
user = os.environ["SMTP_USER"]
password = os.environ["SMTP_PASSWORD"]

msg = EmailMessage()
msg["From"] = from_email
msg["To"] = to_email
msg["Subject"] = subject
msg.set_content(body)

try:
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    print("EMAIL_SENT")
except Exception as exc:
    print("EMAIL_FAILED", exc)
    sys.exit(1)
'''


async def send_outreach_email(
    sandbox, from_email, to_email, subject, body, smtp_user, smtp_password
):
    await sandbox.files.write("/tmp/send_email.py", EMAIL_SCRIPT)
    env = {
        "OUTREACH_FROM_EMAIL": from_email,
        "OUTREACH_TO_EMAIL": to_email,
        "OUTREACH_SUBJECT": subject,
        "OUTREACH_BODY": body,
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": smtp_user,
        "SMTP_PASSWORD": smtp_password,
    }
    out = await sandbox.commands.run("python3", args=["/tmp/send_email.py"], env=env)
    return out.exit_code == 0 and "EMAIL_SENT" in out.stdout
