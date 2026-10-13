# -*- coding: utf-8 -*-
{
    "name": "Telegram Reportes Odoo Remoto",
    "version": "16.0.1.0.0",
    "category": "Tools",
    "summary": "Bot de Telegram que consulta un Odoo remoto (cheques, liquidez, facturación) y envía reportes programados.",
    "description": """
Bot de Telegram configurable por interfaz.

* Se instala en este Odoo (16) pero lee los datos por XML-RPC de otro Odoo (ej. 18) remoto.
* Comandos: /cheques, /liquidez, /facturacion, /facturacion_detalle, /facturacion_listado, /informe.
* Reportes programados (reemplaza el crontab del script original).
* Recepción de mensajes por polling (cron) o webhook.
* Solo responde a los chats autorizados.
""",
    "author": "Printemps",
    "license": "LGPL-3",
    "depends": ["base"],
    "external_dependencies": {"python": ["requests", "pytz"]},
    "data": [
        "security/ir.model.access.csv",
        "views/telegram_report_bot_views.xml",
        "views/telegram_report_log_views.xml",
        "views/menus.xml",
        "data/ir_cron.xml",
    ],
    "application": True,
    "installable": True,
}
