import argparse
import asyncio
import os

from dotenv import load_dotenv
from solari_sandbox import SandboxClient

from auditor.crawl import crawl_website
from auditor.emailer import send_outreach_email
from auditor.reporter import find_contact_email, write_report
from auditor.rules import evaluate_all

load_dotenv()


def _build_email_body(base_url, findings):
    lines = [
        f"I ran a quick technical scan of {base_url} and found a few issues that may be slowing the site down or hurting the user experience.",
        "",
        "Top findings:",
    ]
    for finding in findings[:5]:
        lines.append(f"- {finding['message']}")
    lines.extend([
        "",
        "I can help fix these if you are interested. Just reply to this email and we can discuss next steps.",
        "",
        "If this isn't the right contact, please let me know and I won’t write again.",
    ])
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="Audit a small-business website for performance issues.")
    parser.add_argument("url", help="Website URL to audit")
    parser.add_argument("--max-pages", type=int, default=10, help="Max pages to crawl")
    parser.add_argument("--owner-email", default=None, help="Override discovered contact email")
    parser.add_argument("--dry-run", action="store_true", help="Audit and log but do not send email")
    parser.add_argument("--stealth", action="store_true", help="Use stealth mode (recommended for real sites)")
    args = parser.parse_args()

    if not os.environ.get("SOLARI_API_KEY"):
        raise SystemExit("Set SOLARI_API_KEY in .env or environment.")

    print(f"Auditing {args.url} ...")
    pages, base_origin = await crawl_website(args.url, max_pages=args.max_pages, stealth=args.stealth)
    print(f"Crawled {len(pages)} page(s).")

    findings = evaluate_all(pages, base_origin)
    print(f"Found {len(findings)} issue(s).")

    contact_email = find_contact_email(pages, override=args.owner_email)
    if contact_email:
        print(f"Contact email: {contact_email}")
    else:
        print("No contact email found on the site.")

    email_sent = False
    if not args.dry_run and contact_email:
        if not os.environ.get("GMAIL_APP_PASSWORD") or not os.environ.get("GMAIL_USER"):
            raise SystemExit("Set GMAIL_USER and GMAIL_APP_PASSWORD in .env to send email.")

        from_email = os.environ["GMAIL_USER"]
        smtp_password = os.environ["GMAIL_APP_PASSWORD"]
        subject = f"Website performance findings for {base_origin}"
        body = _build_email_body(base_origin, findings)

        print("Starting sandbox to send email ...")
        async with SandboxClient(
            api_key=os.environ["SOLARI_API_KEY"], base_url="https://api.getsolari.com"
        ) as client:
            sandbox = await client.create(template="base", timeout_ms=5 * 60_000)
            try:
                await sandbox.connect()
                email_sent = await send_outreach_email(
                    sandbox,
                    from_email,
                    contact_email,
                    subject,
                    body,
                    from_email,
                    smtp_password,
                )
                print(f"Email sent: {email_sent}")
            finally:
                await sandbox.kill()
    elif args.dry_run:
        print("Dry run: no email sent.")

    out_dir = write_report(base_origin, pages, findings, contact_email, email_sent)
    print(f"Report written to: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
