# Exponer el Home Monitor a internet (con HTTPS y contraseña)

Esta guía monta el mismo patrón que ya usas con Home Assistant: tu DDNS
(`antediluvian.tplinkdns.com`) apuntando a la Raspberry, pero con dos
añadidos importantes que tu dashboard no tenía de fábrica:

- **HTTPS real** (certificado de Let's Encrypt, gratis y renovable solo)
- **Usuario y contraseña** (sin esto, cualquiera con la URL vería tu panel)

Arquitectura final:

```
Internet → Router (puertos 80/443) → Raspberry:Nginx (HTTPS + login)
                                              │
                                              └──> Flask en 127.0.0.1:8088
                                                   (no accesible directamente
                                                    desde fuera de la Raspberry)
```

Sigue los pasos en orden. Se hace una sola vez; luego todo es automático.

---

## Paso 0. Antes de empezar

Asegúrate de que ya tienes funcionando el monitor en local, como en la guía
del README principal (`python3 app.py` y accesible en tu red interna). Si
aún no lo has probado así, hazlo primero — es mucho más fácil depurar
problemas de red/IPs antes de meter Nginx y certificados en la ecuación.

## Paso 1. Reenvío de puertos en el router de la oficina

En el panel de administración del router, busca **Port Forwarding / Reenvío
de puertos** (o NAT) y crea dos reglas:

| Puerto externo | Puerto interno | IP destino           | Protocolo |
|-----------------|----------------|------------------------|-----------|
| 80               | 80             | IP local de tu Raspberry | TCP       |
| 443              | 443            | IP local de tu Raspberry | TCP       |

Te conviene que la Raspberry tenga **IP fija** en tu red local (reserva de
DHCP en el propio router, o configurada a mano en la Raspberry) para que
estas reglas no se rompan si cambia de IP.

## Paso 2. Instalar Nginx y Certbot en la Raspberry

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx apache2-utils
```

(`apache2-utils` trae el comando `htpasswd`, que usaremos para la contraseña)

## Paso 3. Copiar la configuración de Nginx

Desde la carpeta del proyecto en la Raspberry:

```bash
sudo cp deploy/nginx-home-monitor.conf /etc/nginx/sites-available/home-monitor
sudo ln -s /etc/nginx/sites-available/home-monitor /etc/nginx/sites-enabled/
sudo mkdir -p /var/www/certbot
```

De momento Nginx va a fallar al arrancar porque el bloque HTTPS pide un
certificado que todavía no existe — es normal, lo arreglamos en el paso 5.

## Paso 4. Crear el usuario y contraseña del panel

```bash
sudo htpasswd -c /etc/nginx/.htpasswd tu_usuario
```

Te pedirá la contraseña dos veces. Puedes añadir más usuarios después con
el mismo comando pero sin el `-c` (que borra y crea de nuevo el archivo):

```bash
sudo htpasswd /etc/nginx/.htpasswd otro_usuario
```

## Paso 5. Pedir el certificado HTTPS real

Primero, comenta temporalmente el bloque `server { listen 443 ... }` del
archivo de Nginx (o simplemente prueba el siguiente comando, que usa el
plugin de Nginx y se encarga de la configuración HTTPS por ti):

```bash
sudo certbot --nginx -d antediluvian.tplinkdns.com
```

Certbot te preguntará un email (para avisos de caducidad) y aceptará los
términos. Si todo va bien, modificará automáticamente tu configuración de
Nginx para apuntar a los certificados nuevos — coincide con las rutas que
ya dejamos preparadas en `nginx-home-monitor.conf`.

Verifica que todo carga bien:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Los certificados de Let's Encrypt caducan a los 90 días, pero Certbot
instala una tarea automática de renovación. Puedes comprobarla con:

```bash
sudo certbot renew --dry-run
```

## Paso 6. Arrancar Flask como servicio (si no lo hiciste ya)

```bash
sudo cp deploy/home-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable home-monitor
sudo systemctl start home-monitor
sudo systemctl status home-monitor
```

## Paso 7. Probarlo desde el móvil

Con datos móviles (no en la wifi de la oficina, para probar de verdad el
acceso externo), abre:

```
https://antediluvian.tplinkdns.com
```

Te debería pedir usuario y contraseña (los del Paso 4), y después ver el
panel con HTTPS válido (icono de candado, sin avisos del navegador).

---

## Notas de seguridad

- **Cambia la contraseña periódicamente** con el mismo comando `htpasswd`
  del Paso 4.
- Si alguna vez sospechas que la contraseña se ha filtrado, cámbiala
  inmediatamente — no hay límite de intentos por defecto en esta
  configuración. Si quieres protección extra contra ataques de fuerza
  bruta, se puede añadir `fail2ban` vigilando los logs de Nginx (dime si
  quieres que te lo prepare también).
- Verifica que **solo** los puertos 80 y 443 están redirigidos al puerto
  externo — nunca expongas el 8088 directamente.
- Si quieres revocar el acceso a alguien, borra su línea de
  `/etc/nginx/.htpasswd` o regenera el archivo con `-c`.
