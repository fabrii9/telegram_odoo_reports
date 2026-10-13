# telegram_odoo_reports (Odoo 16)

Bot de Telegram configurable por interfaz que **consulta un Odoo remoto** (ej. Odoo 18) por XML-RPC y responde reportes de cheques, liquidez y facturación. Reemplaza al script `tele.py` + `report_cron.py` + crontab.

El módulo se instala en el Odoo 16; los datos salen del Odoo remoto configurado.

## Comandos del bot

Igual que el bot original (la fecha `YYYY-MM-DD` es opcional; sin fecha usa "hoy" en la zona horaria del bot):

| Comando | Qué responde |
|---|---|
| `/informe [fecha]` | Facturación + liquidez + cheques |
| `/facturacion [fecha]` | Total facturado (líneas, NC en negativo) y cantidad de facturas |
| `/facturacion_detalle [fecha]` | Facturación por tipo de documento |
| `/facturacion_listado [fecha] [límite]` | Documentos por tipo (límite por tipo, default 20) |
| `/liquidez [fecha]` | Pagos de clientes sin diarios de cheques ni retenciones |
| `/cheques [fecha]` | Pagos de clientes en diarios de cheques |
| `/help` | Lista de comandos |

## Instalación

1. Copiar la carpeta `telegram_odoo_reports` al `addons_path` del Odoo 16 y reiniciar.
2. Dependencias Python: `requests` y `pytz` (ya vienen con Odoo).
3. Apps → actualizar lista → instalar **Telegram Reportes Odoo Remoto**.

## Configuración (menú *Telegram Reportes → Bots*)

1. **Telegram**: pegar el token de @BotFather y usar **Probar Telegram**.
2. **Odoo remoto**: URL, base, usuario y clave (idealmente una *API key* de un usuario con permisos de solo lectura contables) y **Probar Odoo remoto**.
3. **Chats**: cargar los chats autorizados. Para descubrir el ID de un grupo, escribir `/help` desde ese chat: el bot contesta con el ID.
4. **Envíos programados**: hora, reporte y días de la semana. Los del bot original eran L–V 6:30, 16:10 y 23:58, y S–D 23:58 (informe).
5. Recepción de mensajes: **polling** por defecto (cron cada minuto, no necesita URL pública). Opcionalmente **webhook** (requiere `web.base.url` con HTTPS público).

## Diferencias respecto al bot original

* Solo responde a **chats autorizados** (el original respondía a cualquiera que encontrara el bot).
* El token **no se escribe en los logs** (se enmascara en cualquier error).
* Los envíos programados los hace un cron de Odoo (cada 5 min) con tolerancia configurable; no se mandan reportes viejos tras una caída y no se duplican.
* Mensajes largos se parten automáticamente (límite de Telegram 4096).
* Mensajes viejos pendientes se descartan en la primera lectura, para no ejecutar comandos antiguos.
* Latencia de respuesta con polling: hasta ~1 minuto (el original respondía al instante). Con webhook es inmediato.

## Seguridad

* Solo usuarios del grupo *Ajustes / Administración* (`base.group_system`) acceden a la configuración.
* El token y la clave remota se guardan en la base (campos de contraseña en la vista). Usar una API key de solo lectura.
* Si el token del bot anterior estuvo expuesto en logs, revocarlo en @BotFather y usar uno nuevo.

## Pruebas

`dev_tests/` tiene pruebas que no requieren Odoo (usan un servidor XML-RPC falso y un stub mínimo de `odoo`):

```
python3 dev_tests/test_remote_odoo.py models/remote_odoo.py
python3 dev_tests/test_bot_logic.py .
```

> Estado: verificado con las pruebas anteriores, compilación y validación de XML. **Aún no se instaló en un Odoo 16 real**; la primera instalación puede requerir ajustes de vistas.
