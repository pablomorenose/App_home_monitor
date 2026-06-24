// Service Worker — HomeM
// Gestiona las notificaciones push en segundo plano

self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));

self.addEventListener('push', e => {
  if (!e.data) return;

  let data;
  try { data = e.data.json(); }
  catch { data = { title: 'HomeM', body: e.data.text() }; }

  const options = {
    body: data.body || '',
    icon: data.icon || '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: 'homem-alert',       // agrupa notificaciones del mismo tipo
    renotify: true,            // vibra aunque ya haya una notificación
    requireInteraction: false,
  };

  e.waitUntil(self.registration.showNotification(data.title || 'HomeM', options));
});

// Al pulsar la notificación, abre la app
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: 'window' }).then(list => {
    for (const client of list) {
      if (client.url.includes(self.location.origin) && 'focus' in client)
        return client.focus();
    }
    if (clients.openWindow) return clients.openWindow('/');
  }));
});
