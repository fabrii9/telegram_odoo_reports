# -*- coding: utf-8 -*-
import hmac
import json
import logging

from odoo import http
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class TelegramReportController(http.Controller):

    @http.route("/telegram_report/webhook/<int:bot_id>", type="http", auth="public",
                methods=["POST"], csrf=False)
    def webhook(self, bot_id, **kwargs):
        bot = request.env["telegram.report.bot"].sudo().browse(bot_id).exists()
        secret = request.httprequest.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        expected = (bot.webhook_secret if bot else "") or ""
        if not bot or not bot.active or bot.mode != "webhook" or not expected \
                or not hmac.compare_digest(secret.encode("utf-8"), expected.encode("utf-8")):
            return Response("forbidden", status=403)
        try:
            update = json.loads(request.httprequest.get_data(as_text=True))
        except ValueError:
            return Response("bad request", status=400)
        try:
            bot._handle_update(update)
        except Exception:  # noqa: BLE001 - siempre 200 para que Telegram no reintente en bucle
            _logger.exception("Telegram webhook: fallo procesando update del bot %s", bot_id)
        return Response("ok", status=200)
