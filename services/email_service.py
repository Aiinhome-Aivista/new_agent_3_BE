import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_html_email(recipient_email, subject, html_content):
        """
        Sends an HTML email using SMTP configuration.
        Returns:
            bool: True if email sent successfully, False otherwise.
        """
        server = None
        try:
            # Check for missing configuration
            if not Config.SMTP_SERVER or not Config.SMTP_PORT:
                logger.error("SMTP Connection Failed: SMTP_SERVER or SMTP_PORT is not configured.")
                return False
            if not Config.SMTP_USERNAME or not Config.SMTP_PASSWORD:
                logger.error("SMTP Authentication Failed: SMTP_USERNAME or SMTP_PASSWORD is not set.")
                return False
            if not Config.SENDER_EMAIL:
                logger.error("SMTP Connection Failed: SENDER_EMAIL is not set.")
                return False

            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = Config.SENDER_EMAIL
            msg['To'] = recipient_email

            # Record the MIME type text/html.
            part = MIMEText(html_content, 'html')
            msg.attach(part)

            # Establish SMTP Connection
            logger.info(f"Connecting to SMTP server {Config.SMTP_SERVER}:{Config.SMTP_PORT}...")
            server = smtplib.SMTP(Config.SMTP_SERVER, Config.SMTP_PORT, timeout=10)
            
            # Start TLS
            server.ehlo()
            server.starttls()
            server.ehlo()
            logger.info("SMTP Connected successfully.")

            # SMTP Authentication
            logger.info(f"Authenticating SMTP user {Config.SMTP_USERNAME}...")
            server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
            
            # Send Email
            server.sendmail(Config.SENDER_EMAIL, recipient_email, msg.as_string())
            logger.info(f"Email Sent Successfully to {recipient_email}")
            return True

        except smtplib.SMTPAuthenticationError as auth_err:
            logger.error(f"SMTP Authentication Failed: {auth_err}")
            return False
        except (smtplib.SMTPConnectError, ConnectionError) as conn_err:
            logger.error(f"SMTP Connection Failed: {conn_err}")
            return False
        except Exception as e:
            logger.error(f"Email Failed for {recipient_email}: {e}")
            return False
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass
