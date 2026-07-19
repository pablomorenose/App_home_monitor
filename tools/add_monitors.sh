#!/bin/bash
# Pobla monitores de sistema y Docker en Home Monitor.
# Compatible con la app 2.0 (login username+password, /api/monitors, CSRF).
#
# Uso:
#   ./add_monitors.sh                         # pide credenciales por prompt
#   ./add_monitors.sh --url http://localhost:8088
#   BASE_URL=http://... ADMINUSERNAME=admin ACCESS_PASSWORD=... ./add_monitors.sh
#
# Variables de entorno opcionales:
#   BASE_URL         URL base de la app (defecto: http://localhost:8088)
#   ADMINUSERNAME    usuario admin (defecto: admin)
#   ACCESS_PASSWORD  contraseña (si no viene por env ni argv, se pide por prompt)
#
# Notas:
#   - Idempotente: si el monitor ya existe, lo omite (no falla ni lo sobrescribe).
#   - Las cookies se guardan en un fichero temporal que se borra siempre (trap).
#   - No pasa la contraseña por argv salvo que uses ACCESS_PASSWORD (env).

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8088}"
ADMINUSERNAME="${ADMINUSERNAME:-admin}"

# ─── Parseo de argumentos ───
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) BASE_URL="$2"; shift 2 ;;
    --user) ADMINUSERNAME="$2"; shift 2 ;;
    --password) ACCESS_PASSWORD="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Argumento desconocido: $1" >&2; exit 2 ;;
  esac
done

# ─── Contraseña: env o prompt seguro (nunca argv directo) ───
if [[ -z "${ACCESS_PASSWORD:-}" ]]; then
  read -rsp "Contraseña para '$ADMINUSERNAME': " ACCESS_PASSWORD >&2; echo >&2
fi
if [[ -z "$ACCESS_PASSWORD" ]]; then
  echo "ERROR: contraseña vacía." >&2; exit 1
fi

# ─── Cookies en tmp con cleanup garantizado ───
COOKIE_FILE="$(mktemp -t hm_cookies.XXXXXX)"
trap 'rm -f "$COOKIE_FILE"' EXIT

# Quita contraseña del entorno cuanto antes (no la necesitamos más abajo).
PW="$ACCESS_PASSWORD"; unset ACCESS_PASSWORD

# ─── Helpers ───
# Login: envía username y password como campos form separados (url-encoded).
# Sin -L: el POST /login responde 302 -> / ; NO seguimos la redirección porque
# curl reenviaría el POST a / (que solo acepta GET → 405). Nos basta con la cookie.
http_login() {  # $1=url $2=username $3=password  -> imprime "HTTP_CODE"
  curl -s -o /dev/null -c "$COOKIE_FILE" -b "$COOKIE_FILE" -X POST "$1" \
    --data-urlencode "username=$2" \
    --data-urlencode "password=$3" \
    -w "%{http_code}"
}
http_get_json() {   # $1=url  -> stdout (json)
  curl -fsSL -b "$COOKIE_FILE" "$1"
}
http_api() {        # $1=method $2=url $3=json_body  -> imprime "HTTP_CODE\nBODY"
  curl -s -b "$COOKIE_FILE" -X "$1" "$2" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $CSRF_TOKEN" \
    ${3:+--data "$3"} \
    -w "\n%{http_code}"
}
split_code() { sed -n '$p'; }
split_body() { sed '$d'; }

# ─── 1. Login (username + password) ───
echo "→ Login en $BASE_URL como '$ADMINUSERNAME'…" >&2
LOGIN_CODE=$(http_login "$BASE_URL/login" "$ADMINUSERNAME" "$PW")
# Login válido = 302 (redirect a /). Otro código = fallo (401 creds, 429 rate-limit…).
if [[ "$LOGIN_CODE" != "302" ]]; then
  echo "ERROR: login fallido (HTTP $LOGIN_CODE). Revisa usuario/contraseña." >&2
  exit 1
fi
unset PW  # ya no la necesitamos

# ─── 2. CSRF token ───
CSRF_TOKEN="$(http_get_json "$BASE_URL/api/csrf-token" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])' 2>/dev/null || true)"
if [[ -z "$CSRF_TOKEN" ]]; then
  echo "ERROR: no se pudo obtener el token CSRF." >&2; exit 1
fi

# ─── 3. Lista de monitores existentes (para idempotencia) ───
MONITORS_JSON="$(http_get_json "$BASE_URL/api/monitors" 2>/dev/null || echo '{"monitors":[]}')"
EXISTING="$(python3 -c 'import sys,json;d=json.load(sys.stdin);print("\n".join(m["id"] for m in d.get("monitors",[])))' <<<"$MONITORS_JSON" 2>/dev/null || true)"

add_if_absent() {  # $1=id $2=name $3=type  $4=json-extra-fields
  local id="$1" name="$2" type="$3" extra="${4:-}"
  if grep -qx "$id" <<<"$EXISTING"; then
    echo "  • $id ya existe — omitido"
    return 0
  fi
  local payload
  payload=$(python3 -c '
import json,sys
d={"id":sys.argv[1],"name":sys.argv[2],"type":sys.argv[3]}
if sys.argv[4]: d.update(json.loads(sys.argv[4]))
print(json.dumps(d))
' "$id" "$name" "$type" "$extra")
  local resp code
  resp=$(http_api POST "$BASE_URL/api/monitors" "$payload")
  code=$(echo "$resp" | split_code)
  if [[ "$code" == "201" || "$code" == "200" ]]; then
    echo "  ✓ $id creado"
  else
    echo "  ✗ $id FALLÓ (HTTP $code):" >&2
    echo "$resp" | split_body >&2
    return 1
  fi
}

# ─── 4. Crear monitores (idempotente) ───
echo "→ Creando monitores…"
add_if_absent raspberry_pi "Raspberry Pi" system ''
add_if_absent docker_portainer "Docker · Portainer" docker ''

# ─── 5. Verificación final ───
echo "→ Verificación…"
VERIFY_JSON="$(http_get_json "$BASE_URL/api/monitors" 2>/dev/null || echo '{"monitors":[]}')"
python3 << PYEOF
import json
d=json.loads('''$VERIFY_JSON''').get("monitors",[])
for want in ("raspberry_pi","docker_portainer"):
    found=next((m for m in d if m["id"]==want),None)
    if not found:
        print(f"  X {want} NO encontrado")
    else:
        t=found.get("type","?")
        print(f"  OK {want} presente ({t})")
PYEOF
echo "¡Listo!"
