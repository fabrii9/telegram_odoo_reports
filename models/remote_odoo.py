# -*- coding: utf-8 -*-
"""Cliente XML-RPC del Odoo remoto (port de odoo_service.py del bot original).

Es una clase Python común (no un modelo de Odoo): no guarda nada, solo consulta.
"""
import logging
import xmlrpc.client
from datetime import datetime

import pytz

_logger = logging.getLogger(__name__)


class RemoteOdooError(Exception):
    """Error de conexión / autenticación / consulta contra el Odoo remoto."""


def fmt_money(n):
    s = format(n or 0.0, ",.2f")
    return s.replace(",", "x").replace(".", ",").replace("x", ".")


class _TimeoutMixin(object):
    def __init__(self, timeout, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutTransport(_TimeoutMixin, xmlrpc.client.Transport):
    pass


class _TimeoutSafeTransport(_TimeoutMixin, xmlrpc.client.SafeTransport):
    pass


def _proxy(url, timeout):
    transport_cls = _TimeoutSafeTransport if url.lower().startswith("https") else _TimeoutTransport
    return xmlrpc.client.ServerProxy(url, transport=transport_cls(timeout), allow_none=True)


class RemoteOdoo(object):
    def __init__(self, url, db, user, password, patt_cheq="cheq", patt_ret="retenciones",
                 patt_prod_cheque="cheque", tz="America/Argentina/Buenos_Aires", timeout=30):
        self.url = (url or "").strip().rstrip("/")
        self.db = (db or "").strip()
        self.user = (user or "").strip()
        self.pwd = password or ""
        self.patt_cheq = patt_cheq or "cheq"
        self.patt_ret = patt_ret or "retenciones"
        self.patt_prod_cheque = patt_prod_cheque or "cheque"
        self.tz = tz or "America/Argentina/Buenos_Aires"
        self.timeout = timeout
        self.uid = None
        self.models = None

    # ---------------- conexión ----------------
    def connect(self):
        if self.uid:
            return self.uid
        if not (self.url and self.db and self.user and self.pwd):
            raise RemoteOdooError("Faltan datos de conexión del Odoo remoto (URL, base, usuario o clave).")
        try:
            common = _proxy("%s/xmlrpc/2/common" % self.url, self.timeout)
            uid = common.authenticate(self.db, self.user, self.pwd, {})
        except Exception as e:  # red, SSL, protocolo, base inexistente
            raise RemoteOdooError("No se pudo conectar a %s: %s" % (self.url, e))
        if not uid:
            raise RemoteOdooError("Autenticación rechazada por el Odoo remoto. Revisá base, usuario y clave/API key.")
        self.uid = uid
        self.models = _proxy("%s/xmlrpc/2/object" % self.url, self.timeout)
        return uid

    def server_version(self):
        try:
            return _proxy("%s/xmlrpc/2/common" % self.url, self.timeout).version().get("server_version")
        except Exception as e:
            raise RemoteOdooError("No se pudo leer la versión del servidor: %s" % e)

    def _execute_kw(self, model, method, args=None, kwargs=None):
        self.connect()
        try:
            return self.models.execute_kw(self.db, self.uid, self.pwd, model, method, args or [], kwargs or {})
        except RemoteOdooError:
            raise
        except Exception as e:
            raise RemoteOdooError("Error consultando %s.%s en el Odoo remoto: %s" % (model, method, e))

    def _search_read_all(self, model, domain, fields, order="id desc", limit=100000):
        return self._execute_kw(model, "search_read", [domain], {"fields": fields, "limit": limit, "order": order})

    def today(self):
        return datetime.now(pytz.timezone(self.tz)).strftime("%Y-%m-%d")

    # ---------------- consultas base ----------------
    def total_cheques(self, fecha=None):
        """Pagos de clientes en diarios de cheques (account.payment)."""
        fecha = fecha or self.today()
        dom = [
            ("partner_type", "=", "customer"),
            ("is_internal_transfer", "=", False),
            ("journal_id", "ilike", self.patt_cheq),
            ("date", "=", fecha),
        ]
        recs = self._search_read_all("account.payment", dom, ["amount"])
        return round(sum(r.get("amount") or 0.0 for r in recs), 2)

    def total_liquidez(self, fecha=None):
        """Liquidez neta: suma de amount_signed de pagos de clientes, sin diarios de cheques ni retenciones."""
        fecha = fecha or self.today()
        dom = [
            ("partner_type", "=", "customer"),
            ("is_internal_transfer", "=", False),
            ("journal_id", "not ilike", self.patt_cheq),
            ("journal_id", "not ilike", self.patt_ret),
            ("date", "=", fecha),
        ]
        recs = self._search_read_all("account.payment", dom, ["amount_signed"])
        return round(sum(r.get("amount_signed") or 0.0 for r in recs), 2)

    # ---------------- facturación por líneas ----------------
    def _dom_fact_lines(self, fecha):
        return [
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "in", ["out_invoice", "out_refund"]),
            ("display_type", "not in", ["line_section", "line_note"]),
            ("product_id", "not ilike", self.patt_prod_cheque),
            ("move_id.invoice_date", "=", fecha),
        ]

    @staticmethod
    def _move_id(value):
        return value[0] if isinstance(value, (list, tuple)) and value else value

    @staticmethod
    def _tipo(value):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return value[1] or "Sin tipo"
        return "Sin tipo"

    def facturacion_stats(self, fecha=None):
        """(total_subtotal, cantidad_facturas, cantidad_lineas). Notas de crédito en negativo."""
        fecha = fecha or self.today()
        recs = self._search_read_all("account.move.line", self._dom_fact_lines(fecha),
                                     ["price_subtotal", "move_id", "is_refund"])
        total, moves = 0.0, set()
        for r in recs:
            mv = self._move_id(r.get("move_id"))
            if mv:
                moves.add(mv)
            monto = float(r.get("price_subtotal") or 0.0)
            total += -monto if r.get("is_refund") else monto
        return round(total, 2), len(moves), len(recs)

    def facturacion_por_tipo(self, fecha=None):
        """[(tipo, total, cantidad_docs)] ordenado por total desc."""
        fecha = fecha or self.today()
        recs = self._search_read_all(
            "account.move.line", self._dom_fact_lines(fecha),
            ["price_subtotal", "l10n_latam_document_type_id", "move_id", "is_refund"])
        totales, docs = {}, {}
        for r in recs:
            tipo = self._tipo(r.get("l10n_latam_document_type_id"))
            mv = self._move_id(r.get("move_id"))
            monto = float(r.get("price_subtotal") or 0.0)
            if r.get("is_refund"):
                monto = -monto
            totales[tipo] = totales.get(tipo, 0.0) + monto
            docs.setdefault(tipo, set())
            if mv:
                docs[tipo].add(mv)
        res = [(t, round(v, 2), len(docs.get(t, ()))) for t, v in totales.items()]
        res.sort(key=lambda x: x[1], reverse=True)
        return res

    def facturacion_documentos_por_tipo(self, fecha=None):
        """{tipo: [(nombre_doc, total)]} con NC en negativo."""
        fecha = fecha or self.today()
        recs = self._search_read_all(
            "account.move.line", self._dom_fact_lines(fecha),
            ["price_subtotal", "move_id", "l10n_latam_document_type_id", "is_refund"])
        sum_por_move, tipo_por_move = {}, {}
        for r in recs:
            mv = self._move_id(r.get("move_id"))
            if not mv:
                continue
            monto = float(r.get("price_subtotal") or 0.0)
            if r.get("is_refund"):
                monto = -monto
            sum_por_move[mv] = sum_por_move.get(mv, 0.0) + monto
            tipo_por_move[mv] = self._tipo(r.get("l10n_latam_document_type_id"))
        if not sum_por_move:
            return {}
        mv_recs = self._execute_kw("account.move", "read", [list(sum_por_move)], {"fields": ["id", "name"]})
        names = {m["id"]: (m.get("name") or "MOVE %s" % m["id"]) for m in mv_recs}
        detalle = {}
        for mv, total in sum_por_move.items():
            detalle.setdefault(tipo_por_move.get(mv, "Sin tipo"), []).append(
                (names.get(mv, "MOVE %s" % mv), round(total, 2)))
        for tipo in detalle:
            detalle[tipo].sort(key=lambda x: x[1], reverse=True)
        return detalle

    # ---------------- mensajes ----------------
    def msg_cheques(self, fecha=None):
        fecha = fecha or self.today()
        return "Cheques %s: %s" % (fecha, fmt_money(self.total_cheques(fecha)))

    def msg_liquidez(self, fecha=None):
        fecha = fecha or self.today()
        return "Liquidez %s: %s" % (fecha, fmt_money(self.total_liquidez(fecha)))

    def msg_facturacion(self, fecha=None, incluir_conteo=True):
        fecha = fecha or self.today()
        total, n_invoices, _lines = self.facturacion_stats(fecha)
        base = "Facturación %s: %s" % (fecha, fmt_money(total))
        if incluir_conteo:
            base += "  |  Facturas: %s" % n_invoices
        return base

    def msg_facturacion_por_tipo(self, fecha=None):
        fecha = fecha or self.today()
        breakdown = self.facturacion_por_tipo(fecha)
        total, n_invoices, _lines = self.facturacion_stats(fecha)
        lineas = ["Facturación por tipo %s:" % fecha]
        for tipo, monto, cant in breakdown:
            lineas.append("- %s: %s  (docs: %s)" % (tipo, fmt_money(monto), cant))
        lineas.append("\nTotal: %s" % fmt_money(total))
        lineas.append("Facturas: %s" % n_invoices)
        return "\n".join(lineas)

    def msg_facturacion_listado_por_tipo(self, fecha=None, limite_por_tipo=20):
        fecha = fecha or self.today()
        detalle = self.facturacion_documentos_por_tipo(fecha)
        if not detalle:
            return "Sin documentos para %s." % fecha
        lineas = ["Documentos por tipo %s:" % fecha]
        for tipo in sorted(detalle):
            lineas.append("\n%s:" % tipo)
            for name, total in detalle[tipo][:limite_por_tipo]:
                lineas.append("  · %s: %s" % (name, fmt_money(total)))
            if len(detalle[tipo]) > limite_por_tipo:
                lineas.append("  … (+%s más)" % (len(detalle[tipo]) - limite_por_tipo))
        return "\n".join(lineas)

    def msg_informe(self, fecha=None):
        fecha = fecha or self.today()
        fact_total, _n, _l = self.facturacion_stats(fecha)
        return ("Informe %s\nFacturación: %s\nLiquidez: %s\nCheques: %s" % (
            fecha, fmt_money(fact_total), fmt_money(self.total_liquidez(fecha)),
            fmt_money(self.total_cheques(fecha))))
