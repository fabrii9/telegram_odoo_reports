# -*- coding: utf-8 -*-
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

WEEKDAY_FIELDS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # 0 = lunes, como datetime.weekday()


class TelegramReportSchedule(models.Model):
    _name = "telegram.report.schedule"
    _description = "Envío programado de reporte por Telegram"
    _order = "bot_id, hour"

    bot_id = fields.Many2one("telegram.report.bot", string="Bot", required=True, ondelete="cascade")
    active = fields.Boolean(default=True)
    name = fields.Char(string="Descripción")
    hour = fields.Float(string="Hora", required=True, default=6.5,
                        help="Hora local (zona horaria del bot). Ej: 6:30, 16:10, 23:58.")
    report = fields.Selection([
        ("informe", "Informe (facturación + liquidez + cheques)"),
        ("facturacion", "Facturación"),
        ("facturacion_detalle", "Facturación por tipo"),
        ("liquidez", "Liquidez"),
        ("cheques", "Cheques"),
    ], string="Reporte", required=True, default="informe")
    mon = fields.Boolean(string="Lun", default=True)
    tue = fields.Boolean(string="Mar", default=True)
    wed = fields.Boolean(string="Mié", default=True)
    thu = fields.Boolean(string="Jue", default=True)
    fri = fields.Boolean(string="Vie", default=True)
    sat = fields.Boolean(string="Sáb")
    sun = fields.Boolean(string="Dom")
    grace_minutes = fields.Integer(
        string="Tolerancia (min)", default=60,
        help="Si el servidor estuvo caído y ya pasaron más minutos que esta tolerancia desde la hora "
             "programada, ese envío se omite en lugar de mandarse tarde.")
    last_run = fields.Datetime(string="Último envío", readonly=True, copy=False)

    @api.constrains("hour")
    def _check_hour(self):
        for rec in self:
            if not 0 <= rec.hour < 24:
                raise ValidationError(_("La hora debe estar entre 00:00 y 23:59."))

    def _runs_on(self, weekday):
        self.ensure_one()
        return bool(getattr(self, WEEKDAY_FIELDS[weekday]))

    def _run_if_due(self):
        """Envía el reporte si ya pasó la hora de hoy, está dentro de la tolerancia y no se envió aún."""
        self.ensure_one()
        tz = pytz.timezone(self.bot_id.tz)
        now_utc = fields.Datetime.now()
        now_local = pytz.utc.localize(now_utc).astimezone(tz)
        if not self._runs_on(now_local.weekday()):
            return False
        total_min = min(int(round(self.hour * 60)), 1439)
        target_local = now_local.replace(hour=total_min // 60, minute=total_min % 60, second=0, microsecond=0)
        if now_local < target_local or now_local - target_local > timedelta(minutes=self.grace_minutes):
            return False
        target_utc = target_local.astimezone(pytz.utc).replace(tzinfo=None)
        if self.last_run and self.last_run >= target_utc:
            return False
        # Se marca y confirma ANTES de enviar: ante un corte, es preferible omitir antes que duplicar.
        self.last_run = now_utc
        self.env.cr.commit()
        return self.bot_id._send_scheduled(self)
