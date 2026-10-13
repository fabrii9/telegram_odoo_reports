# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class TelegramReportLog(models.Model):
    _name = "telegram.report.log"
    _description = "Registro del bot de Telegram"
    _order = "id desc"

    bot_id = fields.Many2one("telegram.report.bot", string="Bot", required=True, ondelete="cascade", index=True)
    kind = fields.Selection([
        ("command", "Comando"),
        ("schedule", "Envío programado"),
        ("rejected", "Chat rechazado"),
        ("error", "Error"),
    ], string="Tipo", required=True)
    state = fields.Selection([("ok", "OK"), ("error", "Error")], string="Estado", default="ok")
    chat_id = fields.Char(string="Chat ID")
    command = fields.Char(string="Comando")
    message = fields.Text(string="Detalle")

    @api.autovacuum
    def _gc_old_logs(self):
        limit = fields.Datetime.now() - timedelta(days=60)
        self.search([("create_date", "<", limit)]).unlink()
