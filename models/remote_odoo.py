# -*- coding: utf-8 -*-
"""Cliente XML-RPC del Odoo remoto (port de odoo_service.py del bot original).

Es una clase Python común (no un modelo de Odoo): no guarda nada, solo consulta.
"""
import logging
import time
import xmlrpc.client
from datetime import datetime, timedelta

import pytz

_logger = logging.getLogger(__name__)


class RemoteOdooError(Exception):
    """Error de conexión / autenticación / consulta contra el Odoo remoto."""


def fmt_money(n):
    s = format(n or 0.0, ",.2f")
    return s.replace(",", "x").replace(".", ",").replace("x", ".")


DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
BAR_WIDTH = 10


def _bar(value, vmax, width=BAR_WIDTH):
    """Barra de texto proporcional (medios bloques). Valores <= 0 no dibujan barra."""
    if not value or value <= 0 or not vmax or vmax <= 0:
        return ""
    halves = max(1, int(round(value / vmax * width * 2)))
    full, half = divmod(halves, 2)
    return "█" * full + ("▌" if half else "")


def format_semana(rows):
    """rows: [(nombre_dia, 'dd/mm', total o None)] -> bloque de texto monoespaciado con barras.

    total None = día que todavía no ocurrió (se muestra '—').
    """
    vmax = max([r[2] for r in rows if r[2] is not None] or [0])
    montos = [fmt_money(r[2]) if r[2] is not None else "—" for r in rows]
    suma = sum(r[2] for r in rows if r[2] is not None)
    total_txt = fmt_money(suma)
    w = max(len(m) for m in montos + [total_txt])
    name_w = max(len(r[0]) for r in rows)
    lines = []
    for (nombre, fecha, total), monto in zip(rows, montos):
        bar = _bar(total, vmax) if total is not None else ""
        lines.append("%s %s  %s  %s" % (nombre.ljust(name_w), fecha, bar.ljust(BAR_WIDTH), monto.rjust(w)))
    lines.append("%s  %s  %s" % ("Total".ljust(name_w + 6), " " * BAR_WIDTH, total_txt.rjust(w)))
    return "\n".join(lines)


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
    RETRY_CODES = (429, 502, 503, 504)

    def _retry(self, fn, tries=4):
        """Reintenta con espera creciente si el Odoo remoto responde 429 (rate limit) o 5xx transitorio."""
        for attempt in range(tries):
            try:
                return fn()
            except xmlrpc.client.ProtocolError as e:
                if e.errcode in self.RETRY_CODES and attempt < tries - 1:
                    wait = 2 * (attempt + 1)
                    _logger.warning("Odoo remoto respondió %s; reintento en %ss", e.errcode, wait)
                    time.sleep(wait)
                    continue
                raise

    def connect(self):
        if self.uid:
            return self.uid
        if not (self.url and self.db and self.user and self.pwd):
            raise RemoteOdooError("Faltan datos de conexión del Odoo remoto (URL, base, usuario o clave).")
        try:
            common = _proxy("%s/xmlrpc/2/common" % self.url, self.timeout)
            uid = self._retry(lambda: common.authenticate(self.db, self.user, self.pwd, {}))
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
            return self._retry(lambda: self.models.execute_kw(
                self.db, self.uid, self.pwd, model, method, args or [], kwargs or {}))
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
    def _dom_fact_base(self):
        return [
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "in", ["out_invoice", "out_refund"]),
            ("display_type", "not in", ["line_section", "line_note"]),
            ("product_id", "not ilike", self.patt_prod_cheque),
        ]

    def _dom_fact_lines(self, fecha):
        return self._dom_fact_base() + [("move_id.invoice_date", "=", fecha)]

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

    def facturacion_semana(self, fecha=None):
        """Facturación de lunes a viernes de la semana de `fecha` (sáb/dom -> la semana que terminó).

        Devuelve [(nombre_dia, 'dd/mm', total)] siempre con los 5 días; los que todavía no
        ocurrieron (posteriores a hoy) llevan total None. Mismo criterio que /facturacion
        (mismo dominio y NC en negativo), pero con solo 2 consultas para toda la semana
        (líneas del rango + fecha de factura de cada comprobante) para no gatillar el rate limit.
        """
        ref = datetime.strptime(fecha or self.today(), "%Y-%m-%d").date()
        hoy = datetime.strptime(self.today(), "%Y-%m-%d").date()
        lunes = ref - timedelta(days=ref.weekday())
        ultimo = min(lunes + timedelta(days=4), hoy)
        por_dia = {}
        if ultimo >= lunes:
            dom = self._dom_fact_base() + [
                ("move_id.invoice_date", ">=", lunes.strftime("%Y-%m-%d")),
                ("move_id.invoice_date", "<=", ultimo.strftime("%Y-%m-%d")),
            ]
            recs = self._search_read_all("account.move.line", dom, ["price_subtotal", "move_id", "is_refund"])
            por_move = {}
            for r in recs:
                mv = self._move_id(r.get("move_id"))
                if not mv:
                    continue
                monto = float(r.get("price_subtotal") or 0.0)
                por_move[mv] = por_move.get(mv, 0.0) + (-monto if r.get("is_refund") else monto)
            if por_move:
                moves = self._execute_kw("account.move", "read", [list(por_move)], {"fields": ["id", "invoice_date"]})
                for m in moves:
                    if m.get("invoice_date"):
                        por_dia[m["invoice_date"]] = por_dia.get(m["invoice_date"], 0.0) + por_move.get(m["id"], 0.0)
        rows = []
        for i, nombre in enumerate(DIAS_SEMANA):
            dia = lunes + timedelta(days=i)
            total = None if dia > hoy else round(por_dia.get(dia.strftime("%Y-%m-%d"), 0.0), 2)
            rows.append((nombre, dia.strftime("%d/%m"), total))
        return rows

    def informe_semanal_parts(self, fecha=None):
        """(texto del informe del día, bloque 'Facturación 5 días' monoespaciado)."""
        fecha = fecha or self.today()
        rows = self.facturacion_semana(fecha)
        dm = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m")
        fact = next((r[2] for r in rows if r[1] == dm and r[2] is not None), None)  # reutiliza el total del día
        return self.msg_informe(fecha, fact_total=fact), format_semana(rows)

    def msg_informe(self, fecha=None, fact_total=None):
        fecha = fecha or self.today()
        if fact_total is None:
            fact_total, _n, _l = self.facturacion_stats(fecha)
        return ("Informe %s\nFacturación: %s\nLiquidez: %s\nCheques: %s" % (
            fecha, fmt_money(fact_total), fmt_money(self.total_liquidez(fecha)),
            fmt_money(self.total_cheques(fecha))))
