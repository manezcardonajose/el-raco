# helpers/email_helper.py
from flask_mail import Message

def enviar_email(destinatario, asunto, cuerpo, html=True, mail_instance=None):
    """
    Envía un email usando la configuración de Flask-Mail
    Necesita que se pase la instancia de Mail como parámetro
    """
    if not mail_instance:
        return False, "No se proporcionó instancia de Mail"
    
    try:
        msg = Message(
            subject=asunto,
            recipients=[destinatario] if isinstance(destinatario, str) else destinatario,
            html=cuerpo if html else None,
            body=cuerpo if not html else None
        )
        mail_instance.send(msg)
        return True, "Email enviado correctamente"
    except Exception as e:
        return False, str(e)