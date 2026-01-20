"""
SMTP Email Sender Module

Sends HTML email alerts via SMTP (Gmail with App Password).
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Any, Optional
from datetime import datetime

from config.settings import settings, OWNER_EMAIL_MAP, MONTH_MAP
from src.processing.alerts import AlertsOutput
from src.processing.objectives import (
    objective_for_month, objective_for_quarter, objective_for_year,
    get_quarter_for_month, quarter_end_dates, EXPECTED_BUS, EXPECTED_TYPOLOGIES,
    get_accounting_period_for_month, get_accounting_period_label, get_months_for_accounting_period
)
import pandas as pd


class EmailSender:
    """
    Sends email alerts via SMTP.

    Supports Gmail with App Password authentication.
    Generates HTML emails for weird proposals and commercial follow-up alerts.
    """

    # Email domain for owner lookup (modify as needed)
    EMAIL_DOMAIN = "@merciraymond.fr"

    # Test mode: redirect all emails to this address
    TEST_EMAIL = "taddeo.carpinelli@merciraymond.fr"

    # Dashboard URL for objectives email link
    # TODO: Update this URL once the dashboard is publicly accessible
    # Example: "https://your-dashboard.streamlit.app" or "https://dashboard.merciraymond.com"
    DASHBOARD_URL = "https://dashboardcommercial-2tczxjdpubas3gpssijsd6.streamlit.app/?tab=globale"  # PLACEHOLDER - Update when dashboard is public

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        test_mode: bool = False
    ):
        """
        Initialize the email sender.

        Args:
            smtp_host: SMTP server hostname. Defaults to settings.
            smtp_port: SMTP server port. Defaults to settings.
            smtp_user: SMTP username. Defaults to settings.
            smtp_password: SMTP password (App Password for Gmail). Defaults to settings.
            test_mode: If True, redirect all emails to TEST_EMAIL address.
        """
        self.smtp_host = smtp_host or settings.smtp_host
        self.smtp_port = smtp_port or settings.smtp_port
        self.smtp_user = smtp_user or settings.smtp_user
        self.smtp_password = smtp_password or settings.smtp_password
        self.test_mode = test_mode

    def _get_email_for_owner(self, owner: str) -> str:
        """
        Convert owner identifier to email address.

        Uses OWNER_EMAIL_MAP from settings if available, otherwise
        constructs email as {owner}@merciraymond.com

        In test mode, always returns TEST_EMAIL.

        Args:
            owner: Owner identifier (e.g., "clemence", "vincent.delavarende")

        Returns:
            Email address
        """
        # Test mode: redirect all emails
        if self.test_mode:
            return self.TEST_EMAIL

        # If already an email, return as-is
        if '@' in owner:
            return owner

        # Check explicit mapping first
        owner_lower = owner.lower()
        if owner_lower in OWNER_EMAIL_MAP:
            return OWNER_EMAIL_MAP[owner_lower]

        # Fallback: construct email from owner identifier
        return f"{owner}{self.EMAIL_DOMAIN}"

    def _format_date_display(self, date_str: Optional[str]) -> str:
        """Format date for display in email (DD/MM/YYYY format)."""
        if not date_str or date_str == 'None' or date_str == '':
            return '-'
        # Try to parse and reformat date from YYYY-MM-DD to DD/MM/YYYY
        try:
            if isinstance(date_str, str) and len(date_str) == 10 and date_str.count('-') == 2:
                # Parse YYYY-MM-DD format
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                return dt.strftime('%d/%m/%Y')
            # If already in another format or not parseable, return as-is
            return date_str
        except (ValueError, TypeError):
            # If parsing fails, return as-is
            return date_str

    def _build_furious_url(self, proposal_id: str) -> str:
        """
        Construct Furious proposal URL from ID.

        Args:
            proposal_id: The proposal ID from Furious

        Returns:
            Full URL to the proposal in Furious, or empty string if no ID
        """
        if not proposal_id:
            return ''
        return f"https://merciraymond.furious-squad.com/compta.php?view=5&cherche={proposal_id}"

    def _format_owner_name(self, owner: str) -> str:
        """
        Format owner identifier into a nice display name.

        Args:
            owner: Owner identifier (e.g., "clemence", "vincent.delavarende")

        Returns:
            Formatted name (e.g., "Clemence", "Vincent Delavarende")
        """
        if owner == 'unassigned':
            return 'Équipe'

        # Replace dots and hyphens with spaces, then capitalize each word
        name = owner.replace('.', ' ').replace('-', ' ')
        name_parts = name.split()
        formatted_name = ' '.join(part.capitalize() for part in name_parts)
        return formatted_name

    def _format_digest_date(self) -> str:
        """
        Format current date for digest header in French format.

        Returns:
            Formatted date string (e.g., "11 décembre 2025")
        """
        now = datetime.now()
        day = now.day
        month = MONTH_MAP.get(now.month, "Mois")
        year = now.year
        return f"{day} {month.lower()} {year}"

    def _generate_combined_html(
        self,
        owner: str,
        weird_items: List[Dict],
        followup_items: List[Dict]
    ) -> str:
        """
        Generate combined HTML email with both weird proposals and follow-ups.

        Args:
            owner: Alert owner
            weird_items: List of weird proposal items
            followup_items: List of follow-up items

        Returns:
            HTML email content
        """
        # Weird proposals section
        weird_rows = ""
        if weird_items:
            for item in weird_items:
                proposal_id = item.get('id', '')
                furious_url = self._build_furious_url(proposal_id)
                url_link = f'<a href="{furious_url}" target="_blank" style="color: #3498db; text-decoration: none;">🔗 Ouvrir</a>' if furious_url else '-'
                assigned_to_raw = item.get("assigned_to")
                assigned_to_display = "-" if (not assigned_to_raw or str(assigned_to_raw).strip() in ("N/A", "None", "nan")) else str(assigned_to_raw)

                weird_rows += f"""
                <tr>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center; font-family: monospace;">{proposal_id}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; font-weight: 500;">{item.get('title', 'Unknown')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{item.get('company_name', 'N/A')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{assigned_to_display}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; font-weight: 600;">{item.get('amount', 0):,.0f} €</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{item.get('statut', 'Unknown')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('date'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('projet_start'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('projet_stop'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center;">{url_link}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; color: #e74c3c; font-size: 0.9em;">{item.get('reason', '')}</td>
                </tr>
                """

        # Follow-up section
        followup_rows = ""
        if followup_items:
            for item in followup_items:
                proposal_id = item.get('id', '')
                furious_url = self._build_furious_url(proposal_id)
                url_link = f'<a href="{furious_url}" target="_blank" style="color: #3498db; text-decoration: none;">🔗 Ouvrir</a>' if furious_url else '-'
                prob = item.get('probability', 0)
                prob_color = "#27ae60" if prob >= 50 else "#f39c12" if prob >= 25 else "#e74c3c"
                assigned_to_raw = item.get("assigned_to")
                assigned_to_display = "-" if (not assigned_to_raw or str(assigned_to_raw).strip() in ("N/A", "None", "nan")) else str(assigned_to_raw)

                followup_rows += f"""
                <tr>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center; font-family: monospace;">{proposal_id}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; font-weight: 500;">{item.get('title', 'Unknown')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{item.get('company_name', 'N/A')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{assigned_to_display}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; font-weight: 600;">{item.get('amount', 0):,.0f} €</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{item.get('statut', 'Unknown')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('date'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('projet_start'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(item.get('projet_stop'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center; color: {prob_color}; font-weight: bold;">{prob}%</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center;">{url_link}</td>
                </tr>
                """

        # Format owner name and date
        owner_name = self._format_owner_name(owner)
        digest_date = self._format_digest_date()

        # Build HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; }}
                .container {{ max-width: 1200px; margin: 20px auto; padding: 30px; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #1a472a; border-bottom: 3px solid #2d5a3f; padding-bottom: 15px; margin-bottom: 20px; }}
                h2 {{ color: #2d5a3f; margin-top: 30px; margin-bottom: 15px; font-size: 1.3em; }}
                .summary-box {{ background: linear-gradient(135deg, #ecf0f1 0%, #d5dbdb 100%); padding: 20px; border-radius: 8px; margin-bottom: 25px; border-left: 4px solid #1a472a; }}
                .summary-box strong {{ color: #1a472a; font-size: 1.1em; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 0.9em; }}
                th {{ background-color: #1a472a; color: white; padding: 12px 8px; text-align: left; font-weight: 600; }}
                td {{ padding: 10px 8px; border: 1px solid #ddd; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                tr:hover {{ background-color: #f0f4f0; }}
                .section {{ margin-bottom: 40px; }}
                .footer {{ margin-top: 40px; padding-top: 20px; border-top: 2px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
                a {{ color: #3498db; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                .no-data {{ text-align: center; padding: 20px; color: #999; font-style: italic; }}
                .greeting {{ font-size: 1.1em; margin-bottom: 20px; color: #2d5a3f; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="margin-bottom: 25px; padding: 20px; background: linear-gradient(135deg, #f0f4f0 0%, #e8f5e9 100%); border-radius: 8px; border-left: 5px solid #1a472a; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                    <p style="margin: 0 0 12px 0; font-size: 1.1em; font-weight: 600; color: #1a472a;">📋 Suivi Commercial</p>
                    <a href="https://www.notion.so/Suivi-Commercial-2ced927802d7809faef6fe444b90d526?source=copy_link" target="_blank" style="display: inline-block; padding: 12px 24px; background-color: #1a472a; color: white; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 1.05em; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">🔗 Voir dans Notion</a>
                </div>
                <h1>📧 Alertes Commerciales</h1>
                <p class="greeting">Bonjour {owner_name}, c'est le Raymbot qui te parle, voici le digest du {digest_date}</p>

                <div class="summary-box">
                    <strong>Résumé:</strong><br>
                    • {len(weird_items)} proposition(s) nécessitent une vérification<br>
                    • {len(followup_items)} proposition(s) nécessitent un suivi<br><br>
                    <strong>Critères de filtrage:</strong><br>
                    • <strong>Vérification:</strong> Propositions avec anomalies (dates manquantes/invalides, probabilité 0%)<br>
                    • <strong>Suivi:</strong> Statut = En attente, fenêtre: 1er du mois précédent → Aujourd'hui + 60 jours<br>
                    &nbsp;&nbsp;&nbsp;&nbsp;CONCEPTION: date proposition dans la fenêtre<br>
                    &nbsp;&nbsp;&nbsp;&nbsp;TRAVAUX/MAINTENANCE: date proposition OU début projet dans la fenêtre
                </div>
        """

        # Weird proposals section
        if weird_items:
            html += f"""
                <div class="section">
                    <h2>🌿 ⚠️ Propositions à Vérifier</h2>
                    <p style="color: #e74c3c; font-weight: 500;">Ces propositions présentent des anomalies de données qui nécessitent votre attention:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Titre</th>
                                <th>Client</th>
                                <th>Assignés</th>
                                <th>Montant</th>
                                <th>Statut</th>
                                <th>Date</th>
                                <th>Début projet</th>
                                <th>Fin projet</th>
                                <th>Lien Furious</th>
                                <th>Problème(s)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {weird_rows}
                        </tbody>
                    </table>
                </div>
            """
        else:
            html += """
                <div class="section">
                    <h2>🌿 ⚠️ Propositions à Vérifier</h2>
                    <div class="no-data">Aucune proposition nécessitant une vérification.</div>
                </div>
            """

        # Follow-up section
        if followup_items:
            html += f"""
                <div class="section">
                    <h2>🌱 📊 Suivi Commercial - À Relancer</h2>
                    <p style="color: #27ae60; font-weight: 500;">Ces propositions nécessitent un suivi dans les prochains jours:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Titre</th>
                                <th>Client</th>
                                <th>Assignés</th>
                                <th>Montant</th>
                                <th>Statut</th>
                                <th>Date</th>
                                <th>Début projet</th>
                                <th>Fin projet</th>
                                <th>Probabilité</th>
                                <th>Lien Furious</th>
                            </tr>
                        </thead>
                        <tbody>
                            {followup_rows}
                        </tbody>
                    </table>
                </div>
            """
        else:
            html += """
                <div class="section">
                    <h2>🌱 📊 Suivi Commercial - À Relancer</h2>
                    <div class="no-data">Aucune proposition nécessitant un suivi immédiat.</div>
                </div>
            """

        html += f"""
                <div class="footer">
                    <p>{datetime.now().strftime('%d/%m/%Y à %H:%M')}</p>
                </div>
            </div>
        </body>
        </html>
        """

        return html

    def _send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        cc_emails: Optional[List[str]] = None
    ) -> bool:
        """
        Send an HTML email via SMTP.

        Args:
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML email body
            cc_emails: Optional list of CC email addresses

        Returns:
            True if sent successfully
        """
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.smtp_user
            msg['To'] = to_email

            # Add CC if provided
            if cc_emails:
                msg['Cc'] = ', '.join(cc_emails)

            # Attach HTML content
            part = MIMEText(html_content, 'html')
            msg.attach(part)

            # Connect and send
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                # Send to all recipients (TO + CC)
                recipients = [to_email]
                if cc_emails:
                    recipients.extend(cc_emails)
                server.sendmail(self.smtp_user, recipients, msg.as_string())

            recipient_list = to_email
            if cc_emails:
                recipient_list += f" (CC: {', '.join(cc_emails)})"
            print(f"  ✓ Email sent to {recipient_list}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            error_msg = f"SMTP Authentication failed. Check SMTP_USER and SMTP_PASSWORD in .env file. Error: {e}"
            print(f"  ✗ {error_msg}")
            return False
        except smtplib.SMTPException as e:
            error_msg = f"SMTP error sending to {to_email}: {e}"
            print(f"  ✗ {error_msg}")
            return False
        except Exception as e:
            error_msg = f"Failed to send email to {to_email}: {type(e).__name__}: {e}"
            print(f"  ✗ {error_msg}")
            return False

    def send_combined_alerts(self, alerts_output: AlertsOutput) -> Dict[str, int]:
        """
        Send combined alerts (weird + follow-up) in a single email per owner.
        In test mode, sends individual emails (one per owner) but all to TEST_EMAIL.

        Args:
            alerts_output: AlertsOutput containing all alerts

        Returns:
            Dictionary with counts of emails sent
        """
        print(f"\n{'='*50}")
        print("Sending Combined Email Alerts")
        if self.test_mode:
            print(f"TEST MODE: Sending individual emails to {self.TEST_EMAIL} (one per owner)")
        print(f"{'='*50}")

        # Get all unique owners
        all_owners = set()
        all_owners.update(alerts_output.weird_proposals.keys())
        all_owners.update(alerts_output.commercial_followup.keys())

        if not all_owners:
            print("No alerts to send")
            return {
                "total_emails_sent": 0,
                "total_recipients": 0
            }

        # Send one email per owner (same logic for test and normal mode)
        # In test mode, just change the recipient email address
        sent_count = 0
        print(f"\nSending combined alerts to {len(all_owners)} recipient(s)...")

        for owner in all_owners:
            weird_items = alerts_output.weird_proposals.get(owner, [])
            followup_items = alerts_output.commercial_followup.get(owner, [])

            # Skip if no items for this owner
            if not weird_items and not followup_items:
                continue

            # In test mode, send to test email; otherwise use owner's email
            if self.test_mode:
                to_email = self.TEST_EMAIL
            else:
                to_email = self._get_email_for_owner(owner)

            # Build subject
            total_items = len(weird_items) + len(followup_items)
            subject_parts = []
            if weird_items:
                subject_parts.append(f"{len(weird_items)} à vérifier")
            if followup_items:
                subject_parts.append(f"{len(followup_items)} à relancer")

            # Add TEST prefix in test mode
            if self.test_mode:
                subject = f"📧 TEST [{owner}]: {' + '.join(subject_parts)}"
            else:
                subject = f"📧 {' + '.join(subject_parts)}"

            # Generate combined HTML
            html = self._generate_combined_html(owner, weird_items, followup_items)

            # Add CC for production emails
            cc_emails = None
            if not self.test_mode:
                cc_emails = ["taddeo.carpinelli@merciraymond.fr", "guillaume@merciraymond.fr"]

            if self._send_email(to_email, subject, html, cc_emails=cc_emails):
                sent_count += 1
                if self.test_mode:
                    print(f"  ✓ Test email sent to {to_email} (owner: {owner})")
                else:
                    print(f"  ✓ Email sent to {to_email}")

        print(f"\n{'='*50}")
        print(f"Total emails sent: {sent_count}")
        if self.test_mode:
            print(f"All emails sent to: {self.TEST_EMAIL}")
        print(f"{'='*50}")

        return {
            "total_emails_sent": sent_count,
            "total_recipients": len(all_owners),
            "weird_items_count": sum(len(items) for items in alerts_output.weird_proposals.values()),
            "followup_items_count": sum(len(items) for items in alerts_output.commercial_followup.values())
        }

    def send_all_alerts(self, alerts_output: AlertsOutput) -> Dict[str, int]:
        """
        Send all alerts from AlertsOutput (combined in one email per owner).

        Args:
            alerts_output: AlertsOutput containing all alerts

        Returns:
            Dictionary with counts of emails sent
        """
        return self.send_combined_alerts(alerts_output)

    def _generate_travaux_projection_html(self, proposals: List[Dict[str, Any]]) -> str:
        """
        Generate HTML email for TRAVAUX projection.

        Args:
            proposals: List of proposal dictionaries

        Returns:
            HTML email content
        """
        # Build table rows
        rows = ""
        if proposals:
            for proposal in proposals:
                proposal_id = proposal.get('id', '')
                furious_url = proposal.get('furious_url', '')
                url_link = f'<a href="{furious_url}" target="_blank" style="color: #3498db; text-decoration: none;">🔗 Ouvrir</a>' if furious_url else '-'
                prob = proposal.get('probability', 0)
                prob_color = "#27ae60" if prob >= 70 else "#f39c12" if prob >= 50 else "#e74c3c"

                rows += f"""
                <tr>
                    <td style="padding: 12px; border: 1px solid #ddd; font-weight: 500;">{proposal.get('title', 'Unknown')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{proposal.get('company_name', 'N/A')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: right; font-weight: 600;">{proposal.get('amount', 0):,.0f} €</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{proposal.get('assigned_to', 'N/A')}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(proposal.get('date'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd;">{self._format_date_display(proposal.get('projet_start'))}</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center; color: {prob_color}; font-weight: bold;">{prob}%</td>
                    <td style="padding: 12px; border: 1px solid #ddd; text-align: center;">{url_link}</td>
                </tr>
                """
        else:
            rows = """
                <tr>
                    <td colspan="8" style="padding: 20px; text-align: center; color: #999; font-style: italic;">
                        Aucune proposition TRAVAUX ne correspond aux critères de projection.
                    </td>
                </tr>
            """

        # Format date
        digest_date = self._format_digest_date()

        # Build HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; }}
                .container {{ max-width: 1400px; margin: 20px auto; padding: 30px; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #f4c430; border-bottom: 3px solid #f4c430; padding-bottom: 15px; margin-bottom: 20px; }}
                .summary-box {{ background: linear-gradient(135deg, #fff9e6 0%, #ffeaa7 100%); padding: 20px; border-radius: 8px; margin-bottom: 25px; border-left: 4px solid #f4c430; }}
                .summary-box strong {{ color: #856404; font-size: 1.1em; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 0.9em; }}
                th {{ background-color: #f4c430; color: #333; padding: 12px 8px; text-align: left; font-weight: 600; }}
                td {{ padding: 10px 8px; border: 1px solid #ddd; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                tr:hover {{ background-color: #fff9e6; }}
                .footer {{ margin-top: 40px; padding-top: 20px; border-top: 2px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
                a {{ color: #3498db; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                .greeting {{ font-size: 1.1em; margin-bottom: 20px; color: #856404; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="margin-bottom: 25px; padding: 20px; background: linear-gradient(135deg, #fff9e6 0%, #ffeaa7 100%); border-radius: 8px; border-left: 5px solid #f4c430; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                    <p style="margin: 0 0 12px 0; font-size: 1.1em; font-weight: 600; color: #856404;">📋 Suivi Commercial</p>
                    <a href="https://www.notion.so/2d5d927802d78002b8cbcee60cc75c29?v=2d5d927802d78014bc0d000c0700ac24&source=copy_link" target="_blank" style="display: inline-block; padding: 12px 24px; background-color: #f4c430; color: #333; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 1.05em; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">🔗 Voir dans Notion</a>
                </div>
                <h1>🔨 Projection Travaux prochains 4 mois</h1>
                <p class="greeting">Bonjour Mathilde, voici les propositions TRAVAUX à fort potentiel pour les prochains mois (le {digest_date})</p>

                <div class="summary-box">
                    <strong>Résumé:</strong><br>
                    • {len(proposals)} proposition(s) TRAVAUX avec probabilité ≥ 50%<br>
                    • Date dans les 30 prochains jours OU début projet dans les 4 prochains mois
                </div>

                <table>
                    <thead>
                        <tr>
                            <th>Nom du projet</th>
                            <th>Client</th>
                            <th>Montant</th>
                            <th>Commercial(s)</th>
                            <th>Date</th>
                            <th>Début projet</th>
                            <th>Probabilité</th>
                            <th>Lien Furious</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>

                <div class="footer">
                    <p>{datetime.now().strftime('%d/%m/%Y à %H:%M')}</p>
                </div>
            </div>
        </body>
        </html>
        """

        return html

    def send_travaux_projection_email(self, proposals: List[Dict[str, Any]]) -> bool:
        """
        Send TRAVAUX projection email to Mathilde with Guillaume and Vincent in CC.

        Args:
            proposals: List of proposal dictionaries

        Returns:
            True if sent successfully
        """
        # Email addresses
        to_email = "mathilde@merciraymond.fr"
        cc_emails = [
            "guillaume@merciraymond.fr",
            "vincent.delavarende@merciraymond.com",
            "taddeo.carpinelli@merciraymond.fr"
        ]

        # In test mode, redirect to test email
        if self.test_mode:
            to_email = self.TEST_EMAIL
            cc_emails = []

        subject = "Projection Travaux prochains 4 mois"

        # Generate HTML
        html = self._generate_travaux_projection_html(proposals)

        # Send email
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.smtp_user
            msg['To'] = to_email
            if cc_emails:
                msg['Cc'] = ', '.join(cc_emails)

            # Attach HTML content
            part = MIMEText(html, 'html')
            msg.attach(part)

            # Connect and send
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                # Send to all recipients (TO + CC)
                recipients = [to_email] + cc_emails
                server.sendmail(self.smtp_user, recipients, msg.as_string())

            recipient_list = to_email
            if cc_emails:
                recipient_list += f" (CC: {', '.join(cc_emails)})"
            print(f"  ✓ TRAVAUX projection email sent to {recipient_list}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            error_msg = f"SMTP Authentication failed. Check SMTP_USER and SMTP_PASSWORD in .env file. Error: {e}"
            print(f"  ✗ {error_msg}")
            return False
        except smtplib.SMTPException as e:
            error_msg = f"SMTP error sending TRAVAUX projection email: {e}"
            print(f"  ✗ {error_msg}")
            return False
        except Exception as e:
            error_msg = f"Failed to send TRAVAUX projection email: {type(e).__name__}: {e}"
            print(f"  ✗ {error_msg}")
            return False

    def _calculate_realized_by_month(
        self,
        df: pd.DataFrame,
        dimension: str,
        key: str,
        month_num: int
    ) -> float:
        """Calculate realized amount for a specific month (helper for email)."""
        if df.empty or 'source_sheet' not in df.columns:
            return 0.0

        # Filter by month
        month_df = pd.DataFrame()
        for sheet in df['source_sheet'].unique():
            # Extract month from sheet name (simple check)
            month_name = MONTH_MAP.get(month_num, "")
            if month_name and month_name in str(sheet):
                month_df = pd.concat([month_df, df[df['source_sheet'] == sheet]], ignore_index=True)

        if month_df.empty:
            return 0.0

        # Use pondéré if available (for Envoyé), otherwise use amount (for Signé)
        amount_col = 'amount_pondere' if 'amount_pondere' in month_df.columns else 'amount'

        # Filter by dimension
        if dimension == "bu":
            if 'cf_bu' not in month_df.columns:
                return 0.0
            filtered = month_df[month_df['cf_bu'] == key]
            return filtered[amount_col].sum() if not filtered.empty else 0.0
        elif dimension == "typologie":
            if 'cf_typologie_de_devis' not in month_df.columns:
                return 0.0
            # Use new allocation logic: amount goes to primary only
            from src.processing.typologie_allocation import allocate_typologie_for_row
            total = 0.0
            for _, row in month_df.iterrows():
                tags, primary = allocate_typologie_for_row(row)
                if primary == key:
                    total += float(row.get(amount_col, 0) or 0)
            return total
        return 0.0

    # =============================================================================
    # OBJECTIVES EMAIL (Production-year based, aligned with dashboard)
    # =============================================================================

    def _extract_month_from_sheet(self, sheet_name: str) -> Optional[int]:
        """
        Extract month number (1-12) from a worksheet name like:
        - "Signé Janvier 2026"
        - "Envoyé Décembre 2025"
        """
        if not sheet_name:
            return None
        s = str(sheet_name)
        # MONTH_MAP is {1:"Janvier", ...}
        for month_num, month_label in MONTH_MAP.items():
            if month_label and str(month_label) in s:
                return int(month_num)
        return None

    def _format_realized_with_carryover(self, total: float, prev_years: float) -> str:
        total = float(total or 0.0)
        prev_years = float(prev_years or 0.0)
        return f"{total:,.0f}€ (dont {prev_years:,.0f}€ années précéd.)"

    def _sum_split_typologie(self, df: pd.DataFrame, amount_col: str, typologie_key: str) -> float:
        """
        Sum amount column for a given typologie using new allocation logic.

        Amount goes to primary typologie only (no splitting).
        """
        if df.empty or amount_col not in df.columns or "cf_typologie_de_devis" not in df.columns:
            return 0.0
        from src.processing.typologie_allocation import allocate_typologie_for_row
        total = 0.0
        for _, row in df.iterrows():
            tags, primary = allocate_typologie_for_row(row)
            if primary == typologie_key:
                total += float(row.get(amount_col, 0) or 0)
        return float(total)

    def _production_amount_with_carryover(
        self,
        df: pd.DataFrame,
        production_year: int,
        amount_col: str,
        dimension: str,
        key: str,
    ) -> tuple[float, float]:
        """
        Sum amount_col for (dimension,key), returning (total, prev_years_part),
        where prev_years_part corresponds to signed_year < production_year.
        """
        if df.empty or amount_col not in df.columns:
            return 0.0, 0.0
        has_signed_year = "signed_year" in df.columns
        prev_df = df[df["signed_year"] < production_year] if has_signed_year else df.iloc[0:0]

        if dimension == "bu":
            if "cf_bu" not in df.columns:
                return 0.0, 0.0
            total = float(df[df["cf_bu"] == key][amount_col].sum() or 0.0)
            prev = float(prev_df[prev_df["cf_bu"] == key][amount_col].sum() or 0.0) if not prev_df.empty else 0.0
            return total, prev

        total = self._sum_split_typologie(df, amount_col, key)
        prev = self._sum_split_typologie(prev_df, amount_col, key) if not prev_df.empty else 0.0
        return total, prev

    def _pure_signature_for_month(
        self,
        df: pd.DataFrame,
        signed_year: int,
        month_num: int,
        dimension: str,
        key: str,
        use_pondere: bool = False,
    ) -> tuple[float, float]:
        """Calculate pure signature amount for a signing month (raw amount, no production split)."""
        if df.empty or "source_sheet" not in df.columns:
            return 0.0, 0.0

        # Filter to signed_year == signed_year
        has_signed_year = "signed_year" in df.columns
        if has_signed_year:
            df = df[df["signed_year"] == signed_year]
            if df.empty:
                return 0.0, 0.0

        # Filter by signing month using source_sheet
        month_df = pd.DataFrame()
        for sheet in df["source_sheet"].unique():
            m = self._extract_month_from_sheet(sheet)
            if m == month_num:
                month_df = pd.concat([month_df, df[df["source_sheet"] == sheet]], ignore_index=True)

        if month_df.empty:
            return 0.0, 0.0

        # Compute brut (raw amount)
        if dimension == "bu":
            month_df = month_df[month_df["cf_bu"] == key] if "cf_bu" in month_df.columns else month_df.iloc[0:0]
            brut = float(month_df["amount"].sum() or 0.0) if "amount" in month_df.columns else 0.0
        else:
            # typologie - use primary allocation
            brut = self._sum_split_typologie(month_df, "amount", key)

        # Compute pondere if requested
        pondere = 0.0
        if use_pondere:
            if "amount_pondere" in month_df.columns:
                if dimension == "bu":
                    pondere = float(month_df["amount_pondere"].sum() or 0.0)
                else:
                    pondere = self._sum_split_typologie(month_df, "amount_pondere", key)
            elif "probability" in month_df.columns and "amount" in month_df.columns:
                # Compute pondere from probability if column missing
                for _, row in month_df.iterrows():
                    prob = float(row.get("probability", 50) or 50) / 100.0
                    if dimension == "bu":
                        if row.get("cf_bu") == key:
                            pondere += float(row.get("amount", 0) or 0) * prob
                    else:
                        from src.processing.typologie_allocation import allocate_typologie_for_row
                        tags, primary = allocate_typologie_for_row(row)
                        if primary == key:
                            pondere += float(row.get("amount", 0) or 0) * prob

        return float(brut), float(pondere)

    def _pure_signature_for_quarter(
        self,
        df: pd.DataFrame,
        signed_year: int,
        quarter: str,
        dimension: str,
        key: str,
        use_pondere: bool = False,
    ) -> tuple[float, float]:
        """Calculate pure signature amount for a signing quarter."""
        quarter_months = {"Q1": [1, 2, 3], "Q2": [4, 5, 6], "Q3": [7, 8, 9], "Q4": [10, 11, 12]}
        if quarter not in quarter_months:
            return 0.0, 0.0

        brut_total = 0.0
        pondere_total = 0.0
        for month_num in quarter_months[quarter]:
            brut, pond = self._pure_signature_for_month(df, signed_year, month_num, dimension, key, use_pondere)
            brut_total += brut
            pondere_total += pond
        return float(brut_total), float(pondere_total)

    def _pure_signature_for_year(
        self,
        df: pd.DataFrame,
        signed_year: int,
        dimension: str,
        key: str,
        use_pondere: bool = False,
    ) -> tuple[float, float]:
        """Calculate pure signature amount for a signing year."""
        if df.empty:
            return 0.0, 0.0

        # Filter to signed_year == signed_year
        has_signed_year = "signed_year" in df.columns
        if has_signed_year:
            df = df[df["signed_year"] == signed_year]
            if df.empty:
                return 0.0, 0.0

        # Compute brut (raw amount)
        if dimension == "bu":
            df = df[df["cf_bu"] == key] if "cf_bu" in df.columns else df.iloc[0:0]
            brut = float(df["amount"].sum() or 0.0) if "amount" in df.columns else 0.0
        else:
            brut = self._sum_split_typologie(df, "amount", key)

        # Compute pondere if requested
        pondere = 0.0
        if use_pondere:
            if "amount_pondere" in df.columns:
                if dimension == "bu":
                    pondere = float(df["amount_pondere"].sum() or 0.0)
                else:
                    pondere = self._sum_split_typologie(df, "amount_pondere", key)
            elif "probability" in df.columns and "amount" in df.columns:
                for _, row in df.iterrows():
                    prob = float(row.get("probability", 50) or 50) / 100.0
                    if dimension == "bu":
                        if row.get("cf_bu") == key:
                            pondere += float(row.get("amount", 0) or 0) * prob
                    else:
                        from src.processing.typologie_allocation import allocate_typologie_for_row
                        tags, primary = allocate_typologie_for_row(row)
                        if primary == key:
                            pondere += float(row.get("amount", 0) or 0) * prob

        return float(brut), float(pondere)

    def _production_period_with_carryover_distribution(
        self,
        df: pd.DataFrame,
        production_year: int,
        period_idx: int,
        dimension: str,
        key: str,
        use_pondere: bool,
    ) -> tuple[float, float]:
        """
        Production-period view aligned with dashboard (production-month based):
        - Uses quarter columns divided by 3 for each month in the accounting period
        - Ensures Jan + Feb + Mar = Q1, etc.
        - For Juil+Août (period 6), naturally becomes 2/3 of Q3
        Returns (total, prev_years_part_for_this_period).
        """
        if df.empty:
            return 0.0, 0.0

        period_months = get_months_for_accounting_period(period_idx)
        if not period_months:
            return 0.0, 0.0

        total = 0.0
        prev_total = 0.0

        for month_num in period_months:
            quarter = get_quarter_for_month(month_num)
            quarter_col = (
                f"Montant Pondéré {quarter}_{production_year}"
                if use_pondere
                else f"Montant Total {quarter}_{production_year}"
            )

            if quarter_col not in df.columns:
                continue

            has_signed_year = "signed_year" in df.columns
            prev_df = df[df["signed_year"] < production_year] if has_signed_year else df.iloc[0:0]

            # Compute total (all signed_years) - divide quarter by 3 for monthly amount
            if dimension == "bu":
                work = df[df["cf_bu"] == key] if "cf_bu" in df.columns else df.iloc[0:0]
                month_total = float(work[quarter_col].sum() or 0.0) / 3.0
                prev_work = prev_df[prev_df["cf_bu"] == key] if not prev_df.empty and "cf_bu" in prev_df.columns else prev_df.iloc[0:0]
                month_prev = float(prev_work[quarter_col].sum() or 0.0) / 3.0
            else:
                # typologie
                month_total = self._sum_split_typologie(df, quarter_col, key) / 3.0
                month_prev = self._sum_split_typologie(prev_df, quarter_col, key) / 3.0

            total += month_total
            prev_total += month_prev

        return float(total), float(prev_total)

    def _load_aggregated_production_data_for_objectives(
        self,
        production_year: int,
        view_type: str,
        pattern: str,
    ) -> pd.DataFrame:
        """
        Load aggregated data across years to capture carryover (like dashboard load_aggregated_production_data).

        years_to_check: production_year-2 .. production_year
        Filters to rows where Montant Total {production_year} > 0 (or pondéré column existence not required).
        Adds signed_year and keeps source_sheet.
        """
        from src.integrations.google_sheets import GoogleSheetsClient

        total_col = f"Montant Total {production_year}"
        years_to_check = [production_year - 2, production_year - 1, production_year]

        sheets_client = GoogleSheetsClient()
        all_parts: List[pd.DataFrame] = []

        for y in years_to_check:
            if y < 2024:
                continue
            try:
                sheet_names = sheets_client.get_worksheets_by_pattern(pattern, view_type=view_type, year=y)
                for sheet_name in sheet_names:
                    df_sheet = sheets_client.read_worksheet(sheet_name, view_type=view_type, year=y)
                    if df_sheet.empty:
                        continue
                    df_sheet = df_sheet.copy()
                    df_sheet["source_sheet"] = sheet_name
                    df_sheet["signed_year"] = y
                    all_parts.append(df_sheet)
            except Exception:
                # If we can't load a year, skip (email should still send with available data)
                continue

        if not all_parts:
            return pd.DataFrame()

        df = pd.concat(all_parts, ignore_index=True)
        # Ensure numeric production columns
        if total_col in df.columns:
            df[total_col] = pd.to_numeric(df[total_col], errors="coerce").fillna(0)
            df = df[df[total_col] > 0].copy()
        return df

    def _calculate_realized_for_quarter(
        self,
        df: pd.DataFrame,
        dimension: str,
        key: str,
        quarter: str
    ) -> float:
        """Calculate realized amount for a quarter."""
        quarter_months = {
            "Q1": [1, 2, 3],
            "Q2": [4, 5, 6],
            "Q3": [7, 8, 9],
            "Q4": [10, 11, 12]
        }
        if quarter not in quarter_months:
            return 0.0
        total = 0.0
        for month_num in quarter_months[quarter]:
            total += self._calculate_realized_by_month(df, dimension, key, month_num)
        return total

    def _calculate_realized_for_year(
        self,
        df: pd.DataFrame,
        dimension: str,
        key: str
    ) -> float:
        """Calculate realized amount for a full year."""
        total = 0.0
        for month_num in range(1, 13):
            total += self._calculate_realized_by_month(df, dimension, key, month_num)
        return total

    def _generate_objectives_management_html(
        self,
        reference_date: datetime,
        year: int,
        df_envoye: pd.DataFrame,
        df_signe: pd.DataFrame
    ) -> str:
        """
        Generate HTML email for objectives management report.

        Args:
            reference_date: Date of the report
            year: Year for objectives
            df_envoye: DataFrame with Envoyé data
            df_signe: DataFrame with Signé data

        Returns:
            HTML email content
        """
        # Align with dashboard: production-year based objectives + carryover + 11-month accounting periods.
        # We load aggregated production data across years to include carryover.
        current_month = reference_date.month
        current_period_idx = get_accounting_period_for_month(current_month)
        current_period_label = get_accounting_period_label(current_period_idx)
        current_quarter = get_quarter_for_month(current_month)
        quarter_end = quarter_end_dates(year)[current_quarter]

        date_str = self._format_digest_date()

        # Build tables for each metric (Envoyé / Signé) using production-year data
        html_tables = ""

        # Order: Signé first, then Envoyé (as requested)
        metric_specs = [
            ("Signé", "signe", "signe", "Signé"),
            ("Envoyé", "envoye", "envoye", "Envoyé"),
        ]

        for metric_name, metric_key, view_type, pattern in metric_specs:
            use_pondere = metric_key == "envoye"

            # Load aggregated production data across years
            metric_df = self._load_aggregated_production_data_for_objectives(year, view_type=view_type, pattern=pattern)

            # Fallback: if we cannot load carryover, use the provided DF (current year only)
            if metric_df.empty:
                metric_df = df_envoye if metric_key == "envoye" else df_signe
                if metric_df is None or metric_df.empty:
                    continue
                metric_df = metric_df.copy()
                # Ensure required columns exist for the email logic
                if "signed_year" not in metric_df.columns:
                    metric_df["signed_year"] = year

            html_tables += f"""
            <div class="section">
                <h2>📊 Objectifs {metric_name} - Production {year}</h2>
                <p style="margin: 0; color: #555;">Basés sur l'année de production (inclut les signatures des années précédentes)</p>
            """

            # Période section (11-month accounting, July+August merged)
            html_tables += f"""
                <h3 style="color: #2d5a3f; margin-top: 20px;">📅 Période ({current_period_label})</h3>
                <table>
                    <thead>
                        <tr>
                            <th>BU</th>
                            <th>Objectif</th>
                            <th>Réalisé</th>
                            <th>Pur</th>
                            <th>Reste</th>
                            <th>%</th>
                        </tr>
                    </thead>
                    <tbody>
            """

            for bu in EXPECTED_BUS:
                realized_total, realized_prev = self._production_period_with_carryover_distribution(
                    metric_df, year, current_period_idx, "bu", bu, use_pondere
                )
                period_months = get_months_for_accounting_period(current_period_idx)
                objective = sum(objective_for_month(year, metric_key, "bu", bu, m) for m in period_months)
                reste = objective - realized_total
                percent = (realized_total / objective * 100) if objective > 0 else 0.0

                # Pure signature for this period
                pure_brut = 0.0
                pure_pondere = 0.0
                for m in period_months:
                    brut, pond = self._pure_signature_for_month(metric_df, year, m, "bu", bu, use_pondere)
                    pure_brut += brut
                    pure_pondere += pond

                if use_pondere:
                    pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                else:
                    pure_display = f"{pure_brut:,.0f}€"

                row_color = "#ffe6e6" if percent < 100 else "#e6ffe6"
                html_tables += f"""
                        <tr style="background-color: {row_color};">
                            <td><strong>{bu}</strong></td>
                            <td>{objective:,.0f}€</td>
                            <td>{self._format_realized_with_carryover(realized_total, realized_prev)}</td>
                            <td>{pure_display}</td>
                            <td>{reste:,.0f}€</td>
                            <td><strong>{percent:.1f}%</strong></td>
                        </tr>
                """

            html_tables += """
                    </tbody>
                </table>
            """

            # Typologie table for period
            html_tables += f"""
                <h3 style="color: #2d5a3f; margin-top: 20px;">🏷️ Période ({current_period_label}) - Typologies</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Typologie</th>
                            <th>Objectif</th>
                            <th>Réalisé</th>
                            <th>Pur</th>
                            <th>Reste</th>
                            <th>%</th>
                        </tr>
                    </thead>
                    <tbody>
            """

            for typ in EXPECTED_TYPOLOGIES:
                realized_total, realized_prev = self._production_period_with_carryover_distribution(
                    metric_df, year, current_period_idx, "typologie", typ, use_pondere
                )
                period_months = get_months_for_accounting_period(current_period_idx)
                objective = sum(objective_for_month(year, metric_key, "typologie", typ, m) for m in period_months)
                reste = objective - realized_total
                percent = (realized_total / objective * 100) if objective > 0 else 0.0

                # Pure signature for this period
                pure_brut = 0.0
                pure_pondere = 0.0
                for m in period_months:
                    brut, pond = self._pure_signature_for_month(metric_df, year, m, "typologie", typ, use_pondere)
                    pure_brut += brut
                    pure_pondere += pond

                if use_pondere:
                    pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                else:
                    pure_display = f"{pure_brut:,.0f}€"

                row_color = "#ffe6e6" if percent < 100 else "#e6ffe6"
                html_tables += f"""
                        <tr style="background-color: {row_color};">
                            <td><strong>{typ}</strong></td>
                            <td>{objective:,.0f}€</td>
                            <td>{self._format_realized_with_carryover(realized_total, realized_prev)}</td>
                            <td>{pure_display}</td>
                            <td>{reste:,.0f}€</td>
                            <td><strong>{percent:.1f}%</strong></td>
                        </tr>
                """

            html_tables += """
                    </tbody>
                </table>
            """

            # Quarter section (production quarter columns)
            quarter_amount_col = f"Montant Pondéré {current_quarter}_{year}" if use_pondere else f"Montant Total {current_quarter}_{year}"
            html_tables += f"""
                <h3 style="color: #2d5a3f; margin-top: 30px;">📊 Trimestre ({current_quarter}) - Fin: {quarter_end.strftime('%d/%m/%Y')}</h3>
                <table>
                    <thead>
                        <tr>
                            <th>BU</th>
                            <th>Objectif</th>
                            <th>Réalisé</th>
                            <th>Pur</th>
                            <th>Reste</th>
                            <th>%</th>
                        </tr>
                    </thead>
                    <tbody>
            """

            for bu in EXPECTED_BUS:
                realized_total, realized_prev = self._production_amount_with_carryover(metric_df, year, quarter_amount_col, "bu", bu)
                objective = objective_for_quarter(year, metric_key, "bu", bu, current_quarter)
                reste = objective - realized_total
                percent = (realized_total / objective * 100) if objective > 0 else 0.0

                # Pure signature for this quarter
                pure_brut, pure_pondere = self._pure_signature_for_quarter(metric_df, year, current_quarter, "bu", bu, use_pondere)
                if use_pondere:
                    pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                else:
                    pure_display = f"{pure_brut:,.0f}€"

                row_color = "#ffe6e6" if percent < 100 else "#e6ffe6"
                html_tables += f"""
                        <tr style="background-color: {row_color};">
                            <td><strong>{bu}</strong></td>
                            <td>{objective:,.0f}€</td>
                            <td>{self._format_realized_with_carryover(realized_total, realized_prev)}</td>
                            <td>{pure_display}</td>
                            <td>{reste:,.0f}€</td>
                            <td><strong>{percent:.1f}%</strong></td>
                        </tr>
                """

            html_tables += """
                    </tbody>
                </table>
            """

            # Year section (production year columns)
            year_amount_col = f"Montant Pondéré {year}" if use_pondere else f"Montant Total {year}"
            html_tables += """
                <h3 style="color: #2d5a3f; margin-top: 30px;">📈 Année</h3>
                <table>
                    <thead>
                        <tr>
                            <th>BU</th>
                            <th>Objectif</th>
                            <th>Réalisé</th>
                            <th>Pur</th>
                            <th>Reste</th>
                            <th>%</th>
                        </tr>
                    </thead>
                    <tbody>
            """

            for bu in EXPECTED_BUS:
                realized_total, realized_prev = self._production_amount_with_carryover(metric_df, year, year_amount_col, "bu", bu)
                objective = objective_for_year(year, metric_key, "bu", bu)
                reste = objective - realized_total
                percent = (realized_total / objective * 100) if objective > 0 else 0.0

                # Pure signature for this year
                pure_brut, pure_pondere = self._pure_signature_for_year(metric_df, year, "bu", bu, use_pondere)
                if use_pondere:
                    pure_display = f"{pure_brut:,.0f}€ / {pure_pondere:,.0f}€"
                else:
                    pure_display = f"{pure_brut:,.0f}€"

                row_color = "#ffe6e6" if percent < 100 else "#e6ffe6"
                html_tables += f"""
                        <tr style="background-color: {row_color};">
                            <td><strong>{bu}</strong></td>
                            <td>{objective:,.0f}€</td>
                            <td>{self._format_realized_with_carryover(realized_total, realized_prev)}</td>
                            <td>{pure_display}</td>
                            <td>{reste:,.0f}€</td>
                            <td><strong>{percent:.1f}%</strong></td>
                        </tr>
                """

            html_tables += """
                    </tbody>
                </table>
            </div>
            """

        # Build full HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; background-color: #f5f5f5; }}
                .container {{ max-width: 1400px; margin: 20px auto; padding: 30px; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #1a472a; border-bottom: 3px solid #2d5a3f; padding-bottom: 15px; margin-bottom: 20px; }}
                h2 {{ color: #2d5a3f; margin-top: 30px; margin-bottom: 15px; font-size: 1.3em; }}
                h3 {{ color: #2d5a3f; margin-top: 20px; margin-bottom: 10px; font-size: 1.1em; }}
                .summary-box {{ background: linear-gradient(135deg, #ecf0f1 0%, #d5dbdb 100%); padding: 20px; border-radius: 8px; margin-bottom: 25px; border-left: 4px solid #1a472a; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 0.9em; }}
                th {{ background-color: #1a472a; color: white; padding: 12px 8px; text-align: left; font-weight: 600; }}
                td {{ padding: 10px 8px; border: 1px solid #ddd; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                .section {{ margin-bottom: 40px; }}
                .footer {{ margin-top: 40px; padding-top: 20px; border-top: 2px solid #ddd; color: #666; font-size: 12px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div style="margin-bottom: 25px; padding: 20px; background: linear-gradient(135deg, #f0f4f0 0%, #e8f5e9 100%); border-radius: 8px; border-left: 5px solid #1a472a; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                    <p style="margin: 0 0 12px 0; font-size: 1.1em; font-weight: 600; color: #1a472a;">📊 Tableau de Bord</p>
                    <a href="{EmailSender.DASHBOARD_URL}" target="_blank" style="display: inline-block; padding: 12px 24px; background-color: #1a472a; color: white; text-decoration: none; border-radius: 5px; font-weight: 600; font-size: 1.05em; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">🔗 Accéder au Dashboard</a>
                </div>
                <h1>🎯 Rapport Objectifs</h1>
                <p style="font-size: 1.1em; margin-bottom: 20px; color: #2d5a3f;">
                    Situation au {date_str}
                </p>

                <div class="summary-box">
                    <strong>Résumé:</strong><br>
                    • Rapport des objectifs pour {year}<br>
                    • Période: {current_period_label}<br>
                    • Trimestre: {current_quarter} (fin: {quarter_end.strftime('%d/%m/%Y')})
                </div>

                {html_tables}

                <div class="footer">
                    <p>{reference_date.strftime('%d/%m/%Y à %H:%M')}</p>
                </div>
            </div>
        </body>
        </html>
        """

        return html

    def send_objectives_management_email(
        self,
        reference_date: datetime,
        year: int,
        df_envoye: pd.DataFrame,
        df_signe: pd.DataFrame
    ) -> bool:
        """
        Send objectives management email to Guillaume (or test email in test mode).

        This email is sent on every pipeline run to provide regular updates on objectives progress.

        Args:
            reference_date: Date of the report
            year: Year for objectives
            df_envoye: DataFrame with Envoyé data
            df_signe: DataFrame with Signé data

        Returns:
            True if sent successfully
        """
        # Email addresses
        to_email = "guillaume@merciraymond.fr"

        # In test mode, redirect to test email
        if self.test_mode:
            to_email = self.TEST_EMAIL

        subject = f"🎯 Rapport Objectifs {year} - {reference_date.strftime('%d/%m/%Y')}"

        # Generate HTML
        html = self._generate_objectives_management_html(reference_date, year, df_envoye, df_signe)

        # Add CC for production emails
        cc_emails = None
        if not self.test_mode:
            cc_emails = ["taddeo.carpinelli@merciraymond.fr"]

        # Send email
        return self._send_email(to_email, subject, html, cc_emails=cc_emails)

    def test_connection(self) -> bool:
        """
        Test SMTP connection.

        Returns:
            True if connection successful
        """
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
            print("✓ SMTP connection successful")
            return True
        except Exception as e:
            print(f"✗ SMTP connection failed: {e}")
            return False
