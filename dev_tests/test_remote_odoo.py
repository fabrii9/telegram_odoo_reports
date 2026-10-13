import sys, threading, importlib.util
from xmlrpc.server import SimpleXMLRPCServer
spec = importlib.util.spec_from_file_location("remote_odoo", sys.argv[1]); ro = importlib.util.module_from_spec(spec); spec.loader.exec_module(ro)

calls = []
def execute_kw(db, uid, pwd, model, method, args, kw):
    calls.append((model, method, args))
    if model == "account.payment":
        dom = args[0]
        if ["journal_id", "ilike", "cheq"] in dom:
            return [{"amount": 1000.5}, {"amount": 250.25}]
        return [{"amount_signed": 5000.0}, {"amount_signed": -200.0}]
    if model == "account.move.line":
        mult = 1
        rng = {}
        for d in args[0]:
            if d[0] == "move_id.invoice_date" and d[1] == "=":
                mult = int(d[2][-2:])   # el monto depende del día del mes: 05 -> x5
            elif d[0] == "move_id.invoice_date":
                rng[d[1]] = int(d[2][-2:])
        if rng:   # consulta de semana: por cada día, una factura (1500*d) y una NC (300*d)
            out = []
            for day in range(rng[">="], rng["<="] + 1):
                out.append({"price_subtotal": 1500.0 * day, "move_id": [day * 10 + 1, "F"], "is_refund": False})
                out.append({"price_subtotal": 300.0 * day, "move_id": [day * 10 + 2, "NC"], "is_refund": True})
            return out
        return [
            {"price_subtotal": 1000.0 * mult, "move_id": [1, "FA-1"], "is_refund": False, "l10n_latam_document_type_id": [1, "Factura A"]},
            {"price_subtotal": 500.0 * mult,  "move_id": [1, "FA-1"], "is_refund": False, "l10n_latam_document_type_id": [1, "Factura A"]},
            {"price_subtotal": 300.0 * mult,  "move_id": [2, "NC-1"], "is_refund": True,  "l10n_latam_document_type_id": [3, "Nota de Crédito A"]},
            {"price_subtotal": 70.0 * mult,   "move_id": [3, "FB-1"], "is_refund": False, "l10n_latam_document_type_id": False},
        ]
    if model == "account.move" and method == "read":
        if args[0] and args[0][0] > 10:   # lectura de invoice_date de la consulta semanal
            return [{"id": i, "invoice_date": "2026-10-%02d" % (i // 10)} for i in args[0]]
        return [{"id": 1, "name": "FA-1"}, {"id": 2, "name": "NC-1"}, {"id": 3, "name": "FB-1"}]
    raise Exception("modelo inesperado " + model)

class Common:
    def authenticate(self, db, user, pwd, ctx): return 7 if (db, user, pwd) == ("odoo", "u", "ok") else False
    def version(self): return {"server_version": "18.0"}

from xmlrpc.server import SimpleXMLRPCRequestHandler
class H(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")
srv = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=H, logRequests=False, allow_none=True)
srv.register_function(Common().authenticate, "authenticate") if False else None
class Dispatcher(SimpleXMLRPCServer): pass
srv.register_instance(None)
import xmlrpc.server
srv._dispatch = lambda method, params: {
    "authenticate": Common().authenticate, "version": Common().version, "execute_kw": execute_kw}[method](*params)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

url = "http://127.0.0.1:%d/" % port
r = ro.RemoteOdoo(url, "odoo", "u", "ok")
print(r.msg_informe("2026-10-13")); print()
print(r.msg_facturacion("2026-10-13")); print()
print(r.msg_facturacion_por_tipo("2026-10-13")); print()
print(r.msg_facturacion_listado_por_tipo("2026-10-13", 1)); print()
print(r.msg_cheques("2026-10-13"), "|", r.msg_liquidez("2026-10-13"))
print("version:", r.server_version())
# errores
for bad in (ro.RemoteOdoo(url, "odoo", "u", "mal"), ro.RemoteOdoo("http://127.0.0.1:1/", "odoo", "u", "ok", timeout=2), ro.RemoteOdoo("", "", "", "")):
    try: bad.connect()
    except ro.RemoteOdooError as e: print("OK error ->", str(e)[:90])
assert r.facturacion_stats("2026-10-13") == (1270.0 * 13, 3, 4), r.facturacion_stats("2026-10-13")
assert ro.fmt_money(1234567.891) == "1.234.567,89"

# ---- semana (L a V) ----
r.today = lambda: "2026-10-13"          # martes
def nombres(rows): return [x[0] for x in rows]
SEM = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
rows = r.facturacion_semana("2026-10-09")           # viernes de una semana ya cerrada
assert nombres(rows) == SEM and [x[1] for x in rows] == ["05/10", "06/10", "07/10", "08/10", "09/10"]
assert [x[2] for x in rows] == [1200.0 * d for d in (5, 6, 7, 8, 9)], rows
assert r.facturacion_semana("2026-10-10") == rows and r.facturacion_semana("2026-10-11") == rows   # sáb/dom -> misma semana
assert r.facturacion_semana("2026-10-05") == rows                                                  # lunes
cur = r.facturacion_semana("2026-10-13")             # semana en curso (hoy martes): mié-vie todavía no ocurrieron
assert nombres(cur) == SEM and [x[2] is None for x in cur] == [False, False, True, True, True], cur
assert cur[1][2] == 1200.0 * 13 and cur[0][2] == 1200.0 * 12
calls.clear(); r.uid = r.uid  # ya autenticado
informe, bloque = r.informe_semanal_parts("2026-10-09")
assert len(calls) == 4, calls          # líneas de la semana + fechas de facturas + liquidez + cheques
assert "Facturación: 10.800,00" in informe, informe   # reutiliza el total de la semana (1200*9)
print(informe); print(); print("Facturación 5 días:"); print(bloque); print()
assert bloque.splitlines()[0].startswith("Lunes     05/10") and bloque.splitlines()[-1].lstrip().startswith("Total")
print(ro.format_semana(cur)); print()
assert "—" in ro.format_semana(cur)

# ---- reintento ante 429 ----
import xmlrpc.client as xc
ro.time.sleep = lambda s: None
class Flaky:
    def __init__(s, n): s.n = n; s.calls = 0
    def execute_kw(s, *a):
        s.calls += 1
        if s.calls <= s.n: raise xc.ProtocolError("x", 429, "Too Many Requests", {})
        return [{"amount": 1.0}]
r2 = ro.RemoteOdoo(url, "odoo", "u", "ok"); r2.connect()
r2.models = Flaky(2); assert r2.total_cheques("2026-10-13") == 1.0 and r2.models.calls == 3     # 2 x 429 y luego OK
r2.models = Flaky(99)
try: r2.total_cheques("2026-10-13"); raise SystemExit("debía fallar")
except ro.RemoteOdooError as e: print("429 persistente ->", str(e)[:100]); assert r2.models.calls == 4
print("ASSERTS OK")
