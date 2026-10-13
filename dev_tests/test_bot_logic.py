import sys, types, importlib.util
M = sys.argv[1]
# ---- stub mínimo de odoo
odoo = types.ModuleType("odoo"); odoo.__path__ = []
class _F:
    def __getattr__(self, n): return lambda *a, **k: None
odoo.fields = _F(); odoo._ = lambda s: s
class _API:
    def __getattr__(self, n): return lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
odoo.api = _API()
class _Models: Model = object
odoo.models = _Models
exc = types.ModuleType("odoo.exceptions")
class UserError(Exception): pass
class ValidationError(Exception): pass
exc.UserError = UserError; exc.ValidationError = ValidationError
for name, mod in {"odoo": odoo, "odoo.exceptions": exc, "odoo.addons": types.ModuleType("odoo.addons"),
                  "odoo.addons.base": types.ModuleType("odoo.addons.base"),
                  "odoo.addons.base.models": types.ModuleType("odoo.addons.base.models")}.items():
    sys.modules[name] = mod
rp = types.ModuleType("odoo.addons.base.models.res_partner"); rp._tz_get = lambda self: []
sys.modules["odoo.addons.base.models.res_partner"] = rp
pkg = types.ModuleType("pkg"); pkg.__path__ = [M + "/models"]; sys.modules["pkg"] = pkg
def load(n):
    s = importlib.util.spec_from_file_location("pkg." + n, "%s/models/%s.py" % (M, n)); m = importlib.util.module_from_spec(s)
    sys.modules["pkg." + n] = m; s.loader.exec_module(m); return m
load("remote_odoo"); bot_mod = load("telegram_report_bot"); Bot = bot_mod.TelegramReportBot

# ---- split / parse
assert bot_mod._parse_date("2026-10-13") == "2026-10-13" and bot_mod._parse_date("13/10") is None
long = "\n".join("linea %04d " % i + "x" * 90 for i in range(200))
ch = bot_mod._split_text(long); assert all(len(c) <= 4000 for c in ch) and "\n".join(ch) == long and len(ch) > 1
assert bot_mod._split_text("a" * 9000)[0] == "a" * 4000 and len(bot_mod._split_text("a" * 9000)) == 3
print("split/parse OK:", len(ch), "chunks")

# ---- _handle_update
class Chat:
    def __init__(s, cid, q=True): s.chat_id, s.can_query = cid, q
class Chats(list):
    def filtered(s, f): return Chats(c for c in s if f(c))
class FakeRemote:
    def msg_informe(s, f=None): return "INFORME %s" % f
    def msg_cheques(s, f=None): raise bot_mod.RemoteOdooError("remoto caído")
    def msg_facturacion_listado_por_tipo(s, f=None, limite_por_tipo=20): return "LISTADO %s lim=%s" % (f, limite_por_tipo)
b = Bot.__new__(Bot)
b.token = "123456:SECRETTOKEN"; b.bot_username = "printemps_bot"; b.reply_unauthorized = True
b.chat_ids = Chats([Chat("-100"), Chat("-200", q=False)])
sent, logs = [], []
b.send_text = lambda cid, t: sent.append((cid, t)); b._log = lambda kind, msg, **k: logs.append((kind, k.get("state"), k.get("command")))
b._remote = lambda: FakeRemote(); b.ensure_one = lambda: None
def upd(text, cid): return {"message": {"text": text, "chat": {"id": int(cid)}}}
b._handle_update(upd("/informe 2026-10-01", -100)); assert sent[-1] == ("-100", "INFORME 2026-10-01"), sent
b._handle_update(upd("/informe", -100)); assert sent[-1] == ("-100", "INFORME None")
b._handle_update(upd("/informe@printemps_bot 2026-10-02", -100)); assert sent[-1][1] == "INFORME 2026-10-02"
n = len(sent); b._handle_update(upd("/informe@otro_bot", -100)); assert len(sent) == n   # otro bot: ignora
b._handle_update(upd("/loquesea", -100)); assert len(sent) == n                             # desconocido: ignora
b._handle_update(upd("hola", -100)); assert len(sent) == n                                  # no es comando
b._handle_update(upd("/facturacion_listado 2026-10-03 5", -100)); assert sent[-1][1] == "LISTADO 2026-10-03 lim=5", sent[-1]
b._handle_update(upd("/facturacion_listado 2026-10-03 abc", -100)); assert sent[-1][1] == "LISTADO 2026-10-03 lim=20"
b._handle_update(upd("/cheques", -100)); assert sent[-1][1].startswith("Error consultando cheques: remoto caído"), sent[-1]
b._handle_update(upd("/help", -100)); assert sent[-1][1].startswith("Comandos:")
b._handle_update(upd("/informe", -999)); assert "no está autorizado" in sent[-1][1] and "-999" in sent[-1][1]   # chat desconocido
b._handle_update(upd("/informe", -200)); assert "no está autorizado" in sent[-1][1]                             # chat sin can_query
assert ("rejected", "error", "informe") in logs
assert "SECRETTOKEN" not in b._scrub("fallo en https://api.telegram.org/bot123456:SECRETTOKEN/sendMessage") 
print("handle_update OK; logs:", logs[-3:])

# ---- schedule
from datetime import datetime, timedelta
import types as _t
sch = load("telegram_report_schedule")
S = sch.TelegramReportSchedule
def mk(hour, now_utc, last=None, grace=60, **days):
    s = S.__new__(S)
    s.hour, s.grace_minutes, s.last_run = hour, grace, last
    for d in sch.WEEKDAY_FIELDS: setattr(s, d, days.get(d, d in ("mon", "tue", "wed", "thu", "fri")))
    s.bot_id = _t.SimpleNamespace(tz="America/Argentina/Buenos_Aires", sent=[])
    s.bot_id._send_scheduled = lambda x: s.bot_id.sent.append(1) or True
    s.env = _t.SimpleNamespace(cr=_t.SimpleNamespace(commit=lambda: None))
    s.ensure_one = lambda: None
    sch.fields = _t.SimpleNamespace(Datetime=_t.SimpleNamespace(now=lambda: now_utc))
    return s
# Buenos Aires = UTC-3. Martes 2026-10-13
assert mk(6.5, datetime(2026,10,13,9,35))._run_if_due() is True                      # 06:35 local, hora 06:30 -> envía
s = mk(6.5, datetime(2026,10,13,9,35)); s._run_if_due(); assert s.last_run == datetime(2026,10,13,9,35) and len(s.bot_id.sent) == 1
assert mk(6.5, datetime(2026,10,13,9,25))._run_if_due() is False                     # 06:25: todavía no
assert mk(6.5, datetime(2026,10,13,10,40))._run_if_due() is False                    # 07:40: fuera de tolerancia
assert mk(6.5, datetime(2026,10,13,9,40), last=datetime(2026,10,13,9,31))._run_if_due() is False   # ya enviado hoy
assert mk(6.5, datetime(2026,10,13,9,40), last=datetime(2026,10,12,9,31))._run_if_due() is True    # envío de ayer no cuenta
assert mk(6.5, datetime(2026,10,17,9,35))._run_if_due() is False                     # sábado, no marcado
assert mk(6.5, datetime(2026,10,17,9,35), sat=True)._run_if_due() is True            # sábado marcado
assert mk(23 + 58/60, datetime(2026,10,14,2,59))._run_if_due() is True               # 23:58 local = 02:58 UTC del día siguiente (martes local)
print("schedule OK")
