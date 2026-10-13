# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class TelegramReportChat(models.Model):
    _name = "telegram.report.chat"
    _description = "Chat autorizado del bot de Telegram"
    _order = "bot_id, name"

    bot_id = fields.Many2one("telegram.report.bot", string="Bot", required=True, ondelete="cascade")
    name = fields.Char(string="Descripción", required=True, help="Ej: Grupo Gerencia")
    chat_id = fields.Char(string="Chat ID", required=True,
                          help="ID numérico del chat. Los grupos suelen empezar con '-'. "
                               "Si un chat no autorizado escribe un comando, el bot le responde con su ID.")
    can_query = fields.Boolean(string="Puede consultar", default=True,
                               help="El bot responde a los comandos de este chat.")
    receive_scheduled = fields.Boolean(string="Recibe reportes", default=True,
                                       help="Recibe los envíos programados.")

    _sql_constraints = [
        ("bot_chat_unique", "unique(bot_id, chat_id)", "Ese chat ya está cargado en este bot."),
    ]

    @api.constrains("chat_id")
    def _check_chat_id(self):
        for rec in self:
            if not re.fullmatch(r"-?\d+", (rec.chat_id or "").strip()):
                raise ValidationError(_("El Chat ID debe ser numérico (ej: -1001234567890)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("chat_id"):
                vals["chat_id"] = vals["chat_id"].strip()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("chat_id"):
            vals["chat_id"] = vals["chat_id"].strip()
        return super().write(vals)
