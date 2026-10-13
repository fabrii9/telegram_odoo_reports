# -*- coding: utf-8 -*-
import html
import logging
import secrets
from datetime import datetime

import pytz
import requests

from odoo import _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import UserError

from .remote_odoo import RemoteOdoo, RemoteOdooError

_logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4000  # el límite de Telegram es 4096

HELP_TEXT = (
    "Comandos:\n"
    "/cheques [YYYY-MM-DD]\n"
    "/liquidez [YYYY-MM-DD]\n"
    "/facturacion [YYYY-MM-DD]\n"
    "/facturacion_detalle [YYYY-MM-DD]\n"
    "/facturacion_listado [YYYY-MM-DD] [LIMITE]\n"
    "/informe [YYYY-MM-DD]\n"
    "/semana [YYYY-MM-DD]"
)

# comando -> texto para el mensaje de error
COMMANDS = {
    "cheques": "cheques",
    "liquidez": "liquidez",
    "facturacion": "facturación",
    "facturacion_detalle": "detalle de facturación",
    "facturacion_listado": "listado de facturación",
    "informe": "informe",
    "semana": "informe semanal",
}

# Reportes que se envían con parse_mode HTML (llevan un bloque monoespaciado <pre>)
HTML_REPORTS = {"semana", "informe_semanal"}


def _parse_date(token):
    try:
        datetime.strptime(token, "%Y-%m-%d")
        return token
    except (ValueError, TypeError):
        return None


def _split_text(text, size=TELEGRAM_MAX_LEN):
    """Parte un texto largo en mensajes <= size, cortando por líneas."""
    chunks, current = [], ""
    for line in (text or "").split("\n"):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = line if not current else current + "\n" + line
    if current:
        chunks.append(current)
    return chunks or [""]


class TelegramReportBot(models.Model):
    _name = "telegram.report.bot"
    _description = "Bot de Telegram - Reportes de Odoo remoto"

    name = fields.Char(string="Nombre", required=True)
    active = fields.Boolean(default=True)

    # --- Telegram ---
    token = fields.Char(string="Token del bot", required=True, copy=False,
                        help="Token que entrega @BotFather. Se guarda en la base; no se escribe en los logs.")
    bot_username = fields.Char(string="Usuario del bot", readonly=True, copy=False)
    mode = fields.Selection(
        [("polling", "Polling (cron cada minuto)"), ("webhook", "Webhook (requiere HTTPS público)")],
        string="Recepción de mensajes", default="polling", required=True, readonly=True, copy=False)
    webhook_secret = fields.Char(string="Secreto del webhook", readonly=True, copy=False,
                                 default=lambda self: secrets.token_urlsafe(24))
    last_update_id = fields.Integer(string="Último update leído", readonly=True, copy=False)
    last_error = fields.Char(string="Último error de recepción", readonly=True, copy=False)
    reply_unauthorized = fields.Boolean(
        string="Avisar a chats no autorizados", default=True,
        help="Si un chat no autorizado envía un comando, el bot responde con el ID del chat "
             "para que puedas darlo de alta en la pestaña Chats.")

    # --- Odoo remoto ---
    remote_url = fields.Char(string="URL del Odoo remoto", help="Ej: https://miempresa.odoo.com")
    remote_db = fields.Char(string="Base de datos")
    remote_user = fields.Char(string="Usuario")
    remote_password = fields.Char(string="Clave / API key", copy=False)

    # --- Parámetros de los reportes ---
    tz = fields.Selection(_tz_get, string="Zona horaria", required=True,
                          default="America/Argentina/Buenos_Aires",
                          help="Define qué es 'hoy' para los reportes y la hora de los envíos programados.")
    pattern_cheq = fields.Char(string="Patrón diarios de cheques", default="cheq",
                               help="Texto que contiene el nombre de los diarios de cheques (ilike).")
    pattern_ret = fields.Char(string="Patrón diarios de retenciones", default="retenciones")
    pattern_prod_cheque = fields.Char(string="Patrón producto cheque", default="cheque",
                                      help="Las líneas de factura con este producto se excluyen de la facturación.")

    chat_ids = fields.One2many("telegram.report.chat", "bot_id", string="Chats")
    schedule_ids = fields.One2many("telegram.report.schedule", "bot_id", string="Envíos programados")
    log_ids = fields.One2many("telegram.report.log", "bot_id", string="Registros")

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _scrub(self, text):
        """Quita el token de cualquier texto antes de loguearlo o mostrarlo."""
        text = str(text or "")
        if self.token:
            text = text.replace(self.token, "***")
        return text

    def _log(self, kind, message, chat_id=None, command=None, state="ok"):
        self.ensure_one()
        self.env["telegram.report.log"].sudo().create({
            "bot_id": self.id,
            "kind": kind,
            "state": state,
            "chat_id": chat_id,
            "command": command,
            "message": self._scrub(message),
        })

    def _notify(self, message, kind="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Telegram Reportes"), "message": message, "type": kind, "sticky": False},
        }

    def _remote(self):
        self.ensure_one()
        return RemoteOdoo(
            self.remote_url, self.remote_db, self.remote_user, self.remote_password,
            patt_cheq=self.pattern_cheq, patt_ret=self.pattern_ret,
            patt_prod_cheque=self.pattern_prod_cheque, tz=self.tz)

    # ------------------------------------------------------------------
    # API de Telegram
    # ------------------------------------------------------------------
    def _api(self, method, payload=None, timeout=20):
        self.ensure_one()
        url = "https://api.telegram.org/bot%s/%s" % (self.token, method)
        try:
            resp = requests.post(url, json=payload or {}, timeout=timeout)
            data = resp.json()
        except Exception as e:
            raise UserError(_("Error de red con Telegram: %s") % self._scrub(e))
        if not data.get("ok"):
            raise UserError(_("Telegram respondió con error: %s") % self._scrub(data.get("description")))
        return data.get("result")

    def send_text(self, chat_id, text, as_html=False):
        self.ensure_one()
        for chunk in _split_text(text):
            payload = {"chat_id": chat_id, "text": chunk}
            if as_html:
                payload["parse_mode"] = "HTML"
            self._api("sendMessage", payload)

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------
    def _build_message(self, cmd, args_text=""):
        """Devuelve el texto de respuesta de un comando (None si no es un comando conocido)."""
        self.ensure_one()
        if cmd in ("start", "help"):
            return HELP_TEXT
        if cmd not in COMMANDS and cmd not in HTML_REPORTS:
            return None
        parts = (args_text or "").split()
        fecha = _parse_date(parts[0]) if parts else None
        limite = 20
        if cmd == "facturacion_listado" and len(parts) >= 2:
            try:
                limite = max(1, int(parts[1]))
            except ValueError:
                limite = 20
        remote = self._remote()
        if cmd == "cheques":
            return remote.msg_cheques(fecha)
        if cmd == "liquidez":
            return remote.msg_liquidez(fecha)
        if cmd == "facturacion":
            return remote.msg_facturacion(fecha, incluir_conteo=True)
        if cmd == "facturacion_detalle":
            return remote.msg_facturacion_por_tipo(fecha)
        if cmd == "facturacion_listado":
            return remote.msg_facturacion_listado_por_tipo(fecha, limite_por_tipo=limite)
        if cmd in HTML_REPORTS:
            informe, bloque = remote.informe_semanal_parts(fecha)
            return "%s\n\nFacturación 5 días:\n<pre>%s</pre>" % (html.escape(informe), html.escape(bloque))
        return remote.msg_informe(fecha)

    def _handle_update(self, update):
        """Procesa un update de Telegram (polling o webhook)."""
        self.ensure_one()
        msg = update.get("message") or update.get("channel_post") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        if not text.startswith("/") or chat.get("id") is None:
            return
        chat_id = str(chat["id"])
        head, _sep, rest = text.partition(" ")
        cmd, _at, target = head[1:].partition("@")
        cmd = cmd.lower()
        if target and self.bot_username and target.lower() != self.bot_username.lower():
            return  # comando dirigido a otro bot del grupo
        if cmd not in COMMANDS and cmd not in ("start", "help"):
            return  # igual que el bot original: ignora comandos desconocidos

        chat_rec = self.chat_ids.filtered(lambda c: c.chat_id == chat_id and c.can_query)
        if not chat_rec:
            self._log("rejected", "Chat no autorizado: %s (%s)" % (
                chat_id, chat.get("title") or chat.get("username") or chat.get("first_name") or ""),
                chat_id=chat_id, command=cmd, state="error")
            if self.reply_unauthorized:
                try:
                    self.send_text(chat_id, _("Este chat no está autorizado. ID del chat: %s") % chat_id)
                except UserError as e:
                    _logger.warning("Telegram bot %s: no se pudo avisar al chat %s: %s", self.id, chat_id, e)
            return

        state = "ok"
        try:
            reply = self._build_message(cmd, rest)
        except (RemoteOdooError, UserError) as e:
            state = "error"
            reply = _("Error consultando %s: %s") % (COMMANDS.get(cmd, cmd), self._scrub(e))
        except Exception as e:  # noqa: BLE001 - el bot nunca debe romperse por un comando
            state = "error"
            _logger.exception("Telegram bot %s: error inesperado en /%s", self.id, cmd)
            reply = _("Error consultando %s: %s") % (COMMANDS.get(cmd, cmd), self._scrub(e))
        if reply is None:
            return
        try:
            self.send_text(chat_id, reply, as_html=(state == "ok" and cmd in HTML_REPORTS))
        except UserError as e:
            state = "error"
            reply = "No se pudo enviar la respuesta: %s" % e
        self._log("command", reply if state == "error" else text, chat_id=chat_id, command=cmd, state=state)

    # ------------------------------------------------------------------
    # Recepción: polling (cron)
    # ------------------------------------------------------------------
    def _poll(self):
        self.ensure_one()
        if not self.last_update_id:
            # Primera lectura: se descartan los mensajes viejos pendientes para no ejecutar comandos antiguos.
            updates = self._api("getUpdates", {"offset": -1, "limit": 1, "timeout": 0})
            self.last_update_id = updates[-1]["update_id"] if updates else 0
            return
        updates = self._api("getUpdates", {
            "offset": self.last_update_id + 1, "limit": 100, "timeout": 0,
            "allowed_updates": ["message", "channel_post"]})
        for upd in updates:
            try:
                with self.env.cr.savepoint():
                    self._handle_update(upd)
            except Exception as e:  # noqa: BLE001
                _logger.exception("Telegram bot %s: fallo procesando update %s", self.id, upd.get("update_id"))
                self._log("error", "Fallo procesando update %s: %s" % (upd.get("update_id"), e), state="error")
            self.last_update_id = max(self.last_update_id, upd["update_id"])
            self.env.cr.commit()  # no reprocesar el update si el cron se corta más adelante

    @api.model
    def _cron_poll(self):
        for bot in self.search([("active", "=", True), ("mode", "=", "polling")]):
            try:
                bot._poll()
                if bot.last_error:
                    bot.last_error = False
            except Exception as e:  # noqa: BLE001
                msg = bot._scrub(e)
                if msg != bot.last_error:  # no llenar el log con el mismo error cada minuto
                    bot.last_error = msg[:250]
                    bot._log("error", "Polling: %s" % msg, state="error")
                self.env.cr.commit()

    # ------------------------------------------------------------------
    # Reportes programados
    # ------------------------------------------------------------------
    def _send_scheduled(self, schedule):
        self.ensure_one()
        chats = self.chat_ids.filtered("receive_scheduled")
        if not chats:
            self._log("error", "El envío '%s' no tiene chats con 'Recibe reportes'." % (schedule.name or schedule.report),
                      state="error")
            return False
        try:
            text = self._build_message(schedule.report, "")
        except Exception as e:  # noqa: BLE001
            self._log("error", "Envío programado '%s': %s" % ((schedule.name or schedule.report), self._scrub(e)),
                      command=schedule.report, state="error")
            return False
        ok = True
        as_html = schedule.report in HTML_REPORTS
        for chat in chats:
            try:
                self.send_text(chat.chat_id, text, as_html=as_html)
                self._log("schedule", text, chat_id=chat.chat_id, command=schedule.report)
            except UserError as e:
                ok = False
                self._log("error", "Envío programado a %s: %s" % (chat.chat_id, e),
                          chat_id=chat.chat_id, command=schedule.report, state="error")
        return ok

    @api.model
    def _cron_scheduled(self):
        for bot in self.search([("active", "=", True)]):
            for schedule in bot.schedule_ids.filtered("active"):
                try:
                    schedule._run_if_due()
                except Exception:  # noqa: BLE001
                    _logger.exception("Telegram bot %s: fallo en envío programado %s", bot.id, schedule.id)

    # ------------------------------------------------------------------
    # Botones
    # ------------------------------------------------------------------
    def action_test_telegram(self):
        self.ensure_one()
        me = self._api("getMe")
        self.bot_username = me.get("username")
        return self._notify(_("Conectado a Telegram como @%s") % me.get("username"))

    def action_test_remote(self):
        self.ensure_one()
        try:
            remote = self._remote()
            uid = remote.connect()
            version = remote.server_version()
        except RemoteOdooError as e:
            raise UserError(str(e))
        return self._notify(_("Conexión OK. Odoo remoto %s, usuario id %s.") % (version, uid))

    def action_send_report_now(self):
        self.ensure_one()
        schedule = self.env["telegram.report.schedule"].new({"bot_id": self.id, "report": "informe"})
        if not self._send_scheduled(schedule):
            raise UserError(_("No se pudo enviar el informe. Revisá la pestaña Registros."))
        return self._notify(_("Informe enviado."))

    def action_use_polling(self):
        self.ensure_one()
        self._api("deleteWebhook", {"drop_pending_updates": False})
        self.write({"mode": "polling", "last_update_id": 0, "last_error": False})
        return self._notify(_("Modo polling activado. El cron leerá los mensajes cada minuto."))

    def action_set_webhook(self):
        self.ensure_one()
        base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        if not base.startswith("https://"):
            raise UserError(_("El webhook requiere que 'web.base.url' sea una URL pública con HTTPS (hoy: %s).") % base)
        if not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(24)
        url = "%s/telegram_report/webhook/%s?db=%s" % (base, self.id, self.env.cr.dbname)
        self._api("setWebhook", {
            "url": url, "secret_token": self.webhook_secret,
            "allowed_updates": ["message", "channel_post"]})
        self.mode = "webhook"
        return self._notify(_("Webhook registrado en Telegram."))

    def action_reset_offset(self):
        self.write({"last_update_id": 0, "last_error": False})
        return self._notify(_("Offset reiniciado: en la próxima lectura se descartan los mensajes pendientes."))
