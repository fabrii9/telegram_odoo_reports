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
        return [
            {"price_subtotal": 1000.0, "move_id": [1, "FA-1"], "is_refund": False, "l10n_latam_document_type_id": [1, "Factura A"]},
            {"price_subtotal": 500.0,  "move_id": [1, "FA-1"], "is_refund": False, "l10n_latam_document_type_id": [1, "Factura A"]},
            {"price_subtotal": 300.0,  "move_id": [2, "NC-1"], "is_refund": True,  "l10n_latam_document_type_id": [3, "Nota de Crédito A"]},
            {"price_subtotal": 70.0,   "move_id": [3, "FB-1"], "is_refund": False, "l10n_latam_document_type_id": False},
        ]
    if model == "account.move" and method == "read":
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
assert r.facturacion_stats("2026-10-13") == (1270.0, 3, 4), r.facturacion_stats("2026-10-13")
assert ro.fmt_money(1234567.891) == "1.234.567,89"
print("ASSERTS OK")
