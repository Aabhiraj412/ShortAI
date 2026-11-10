"""
Secure Local SMTP Email Alert Configuration Wizard for CrazzyDocs.
Phase 5, Stages 3.6, 3.9F & 3.10 Standard.

Prompts operator locally for email credentials using masked input (getpass),
stores secrets strictly in .env.local (strictly gitignored), and performs a live test email dispatch.
Never echoes passwords or logs credentials to files or terminals.
"""

import argparse
import getpass
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Dict, Optional, Tuple

WORKSPACE_ROOT = r"D:\Agents\CrazzyDocs"
ENV_LOCAL_PATH = os.path.join(WORKSPACE_ROOT, ".env.local")
GITIGNORE_PATH = os.path.join(WORKSPACE_ROOT, ".gitignore")


def ensure_gitignored():
    """Ensures .env.local is in .gitignore so secrets are never committed."""
    if os.path.exists(GITIGNORE_PATH):
        with open(GITIGNORE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        if ".env.local" not in content:
            with open(GITIGNORE_PATH, "a", encoding="utf-8") as f:
                f.write("\n# Local secret credentials (never commit)\n.env.local\n")
    else:
        with open(GITIGNORE_PATH, "w", encoding="utf-8") as f:
            f.write("# Local secret credentials (never commit)\n.env.local\n")


def mask_secret(text: str) -> str:
    """Masks secret values in error messages."""
    if not text:
        return ""
    # Mask any password or token patterns
    return re.sub(r"(?i)(password|pass|secret|key|token)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)


def test_smtp_delivery(
    host: str,
    port: int,
    username: str,
    password: str,
    recipient: str,
    sender: str,
    security: str = "STARTTLS",
    timeout: float = 12.0,
) -> Tuple[bool, str]:
    """
    Attempts live SMTP transmission of a test message.
    Returns (success: bool, message: str).
    """
    test_subject = "[CrazzyDocs Verification] Notification System Operational"
    test_body = (
        f"CrazzyDocs Autonomous Production Alerting System\n\n"
        f"Status: Operational\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        f"Host: {host}:{port}\n"
        f"Security: {security}\n"
        f"Sender: {sender}\n"
        f"Recipient: {recipient}\n\n"
        f"Email notifications are now active for production failure, recovery, and daily dashboard alerts."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = test_subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(test_body, "plain"))

    try:
        if security.upper() == "SSL" or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as server:
                server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                if security.upper() == "STARTTLS" or port == 587:
                    server.starttls()
                    server.ehlo()
                server.login(username, password)
                server.send_message(msg)

        return True, "LIVE EMAIL DELIVERY VERIFIED"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Authentication Failed: {mask_secret(str(e))} (Verify Gmail App Password)"
    except smtplib.SMTPConnectError as e:
        return False, f"Connection Failed: {mask_secret(str(e))} (Verify SMTP Host and Port)"
    except Exception as e:
        return False, f"SMTP Error: {mask_secret(str(e))}"


def save_configuration(
    host: str,
    port: int,
    username: str,
    password: str,
    recipient: str,
    sender: Optional[str] = None,
    security: str = "STARTTLS",
):
    """Writes credentials strictly to .env.local after verifying gitignore."""
    ensure_gitignored()
    sender_addr = sender or username
    with open(ENV_LOCAL_PATH, "w", encoding="utf-8") as f:
        f.write(f"# CrazzyDocs Local Alert Secrets - Generated {datetime.now().isoformat()}\n")
        f.write(f"CRAZZYDOCS_SMTP_HOST={host}\n")
        f.write(f"CRAZZYDOCS_SMTP_PORT={port}\n")
        f.write(f"CRAZZYDOCS_SMTP_SECURITY={security}\n")
        f.write(f"CRAZZYDOCS_SMTP_USER={username}\n")
        f.write(f"CRAZZYDOCS_SMTP_SENDER={sender_addr}\n")
        f.write(f"CRAZZYDOCS_SMTP_PASSWORD={password}\n")
        f.write(f"CRAZZYDOCS_ALERT_RECIPIENT={recipient}\n")

    # Update active environment in-memory
    os.environ["CRAZZYDOCS_SMTP_HOST"] = str(host)
    os.environ["CRAZZYDOCS_SMTP_PORT"] = str(port)
    os.environ["CRAZZYDOCS_SMTP_SECURITY"] = security
    os.environ["CRAZZYDOCS_SMTP_USER"] = username
    os.environ["CRAZZYDOCS_SMTP_SENDER"] = sender_addr
    os.environ["CRAZZYDOCS_SMTP_PASSWORD"] = password
    os.environ["CRAZZYDOCS_ALERT_RECIPIENT"] = recipient


def configure_and_test():
    """Interactive local configuration wizard."""
    print("=" * 70)
    print("CRAZZYDOCS SECURE EMAIL NOTIFICATION CONFIGURATION WIZARD")
    print("=" * 70)
    print("Credentials are saved strictly to .env.local (gitignored) and are never logged.\n")

    # 1. Recipient Email
    recipient = input("1. Enter Notification Recipient Email (e.g. operator@domain.com): ").strip()
    while not recipient or "@" not in recipient:
        recipient = input("   Invalid email. Please enter a valid recipient address: ").strip()

    # 2. Provider Preset
    print("\n2. Select Email Provider:")
    print("   [1] Gmail (smtp.gmail.com:587 STARTTLS) - Requires 16-character App Password")
    print("   [2] Microsoft Outlook / Office 365 (smtp.office365.com:587 STARTTLS)")
    print("   [3] Custom SMTP Server")
    choice = input("   Enter choice [1-3] (Default: 1): ").strip() or "1"

    if choice == "1":
        host = "smtp.gmail.com"
        port = 587
        security = "STARTTLS"
        print("\n   [NOTE FOR GMAIL]: You must use a Google 'App Password' (16 characters).")
        print("   Generate one at: https://myaccount.google.com/apppasswords")
        print("   Do NOT enter your regular Google account password.\n")
    elif choice == "2":
        host = "smtp.office365.com"
        port = 587
        security = "STARTTLS"
    else:
        host = input("   Enter SMTP Host: ").strip()
        port_str = input("   Enter SMTP Port (Default: 587): ").strip() or "587"
        port = int(port_str)
        security = input("   Enter Security [STARTTLS/SSL/NONE] (Default: STARTTLS): ").strip() or "STARTTLS"

    # 3. Sender / Username
    sender_email = input("3. Enter Sender Email Address: ").strip()
    smtp_username = input(f"4. Enter SMTP Username (Press Enter for '{sender_email}'): ").strip() or sender_email

    # 5. Masked Password Input via getpass (never echoed)
    password = getpass.getpass("5. Enter Gmail App Password (input hidden): ").strip()

    print(f"\nAttempting live verification via {host}:{port} ({security})...")
    ok, msg = test_smtp_delivery(
        host=host,
        port=port,
        username=smtp_username,
        password=password,
        recipient=recipient,
        sender=sender_email,
        security=security,
    )

    if ok:
        print(f"\n[SUCCESS] {msg}")
        save_configuration(
            host=host,
            port=port,
            username=smtp_username,
            password=password,
            recipient=recipient,
            sender=sender_email,
            security=security,
        )
        print(f"Configuration written securely to: {ENV_LOCAL_PATH}")
        print("Email notifications are now verified and active.")
    else:
        print(f"\n[CONFIGURATION FAILED] {msg}")
        print("Credentials were NOT saved. Please generate an App Password and retry.")


def test_existing_configuration() -> Tuple[bool, str]:
    """Tests current credentials stored in .env.local or environment."""
    # Ensure .env.local is loaded
    if os.path.exists(ENV_LOCAL_PATH):
        with open(ENV_LOCAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    host = os.environ.get("CRAZZYDOCS_SMTP_HOST") or os.environ.get("SMTP_HOST")
    port = int(os.environ.get("CRAZZYDOCS_SMTP_PORT") or os.environ.get("SMTP_PORT") or 587)
    username = os.environ.get("CRAZZYDOCS_SMTP_USER") or os.environ.get("SMTP_USER")
    password = os.environ.get("CRAZZYDOCS_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD")
    recipient = os.environ.get("CRAZZYDOCS_ALERT_RECIPIENT") or os.environ.get("NOTIFICATION_RECIPIENT_EMAIL")
    sender = os.environ.get("CRAZZYDOCS_SMTP_SENDER") or username
    security = os.environ.get("CRAZZYDOCS_SMTP_SECURITY", "STARTTLS")

    if not all([host, username, password, recipient]):
        return False, "SMTP credentials missing from environment and .env.local"

    return test_smtp_delivery(
        host=host,
        port=port,
        username=username,
        password=password,
        recipient=recipient,
        sender=sender,
        security=security,
    )


def update_recipient(new_recipient: str) -> bool:
    """Updates only the recipient email in .env.local without touching credentials."""
    if not new_recipient or "@" not in new_recipient:
        raise ValueError("Invalid email format.")

    ensure_gitignored()
    lines = []
    updated = False
    if os.path.exists(ENV_LOCAL_PATH):
        with open(ENV_LOCAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CRAZZYDOCS_ALERT_RECIPIENT="):
                    lines.append(f"CRAZZYDOCS_ALERT_RECIPIENT={new_recipient.strip()}\n")
                    updated = True
                else:
                    lines.append(line)

    if not updated:
        lines.append(f"CRAZZYDOCS_ALERT_RECIPIENT={new_recipient.strip()}\n")

    with open(ENV_LOCAL_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

    os.environ["CRAZZYDOCS_ALERT_RECIPIENT"] = new_recipient.strip()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CrazzyDocs Email Configuration Wizard")
    parser.add_argument("--test-existing", action="store_true", help="Test delivery using existing .env.local")
    args = parser.parse_args()

    if args.test_existing:
        success, message = test_existing_configuration()
        print(f"Result: {message}")
        exit(0 if success else 1)
    else:
        configure_and_test()
