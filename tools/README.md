# tools/

Utilidades **opcionales** de administración. **No son necesarias para que la app funcione.**

La app corre sola en el contenedor `home-monitor`: arranca solo, hace los checks cada 15 s
y los monitores iniciales se cargan automáticamente desde `config.py` al primer arranque.

Estos scripts solo se usan para tareas manuales puntuales (instalación limpia,
provisioning, recuperación de un monitor borrado por error, etc.).

---

## add_monitors.sh

Crea los monitores `raspberry_pi` (tipo `system`) y `docker_portainer` (tipo `docker`)
mediante la API (`/api/monitors`), de forma **idempotente** (si ya existen, los omite).

### Cuándo NO usarlo

- En una app ya desplegada con monitores ya cargados: **no hace falta nunca**.
- Es redundante con `config.py`, que ya crea estos dos monitores al arrancar.

### Cuándo usarlo

- Has borrado un monitor por error y quieres re-crearlo sin ir a mano por la web.
- Estás montando la app en otra máquina/database nueva y prefieres un script a 10 clics.

### Uso

```bash
# desde la propia Raspberry (por SSH)
cd ~/repos/App_home_monitor/tools
./add_monitors.sh                                   # prompt interactivo (password oculta)
ACCESS_PASSWORD=<tu_pass> ./add_monitors.sh         # contraseña por env

# desde tu Mac, apuntando a la Raspberry por Tailscale
./add_monitors.sh --url http://100.112.43.5:8088 --user admin
```

Flags: `--url`, `--user`, `--password` · Env: `BASE_URL`, `ADMINUSERNAME`, `ACCESS_PASSWORD`

### Notas de seguridad

- La contraseña **nunca** se pasa como argumento posicional (sería visible en `ps`).
- Se lee de `ACCESS_PASSWORD` o se pide con `read -rsp` (sin eco).
- Las cookies de sesión van a `mktemp` y se borran siempre (`trap EXIT`).
- Se elimina del entorno tras el login.
