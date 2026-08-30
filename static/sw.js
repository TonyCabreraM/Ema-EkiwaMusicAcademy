// Service Worker para notificaciones push Web Push API
self.addEventListener('install', function(event) {
    console.log('Service Worker instalado');
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    console.log('Service Worker activado');
    event.waitUntil(clients.claim());
});

// Escuchar PUSH REAL del servidor (Web Push API)
self.addEventListener('push', function(event) {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch(e) {
        data = { title: 'Nueva notificación', body: '' };
    }

    const title = data.title || 'Júbilo';
    const options = {
        body: data.body || '',
        icon: '/static/favicon.png',
        badge: '/static/favicon.png',
        vibrate: [200, 100, 200],
        data: {
            url: data.url || '/notifications'
        },
        actions: [
            { action: 'view', title: 'Ver' },
            { action: 'close', title: 'Cerrar' }
        ],
        tag: data.tag || 'jubilo-notification',
        renotify: true
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// Escuchar mensajes desde la página principal (fallback local)
self.addEventListener('message', function(event) {
    if (event.data && event.data.type === 'SHOW_NOTIFICATION') {
        const title = event.data.title || 'Nueva notificación';
        const options = {
            body: event.data.body || '',
            icon: '/static/favicon.png',
            badge: '/static/favicon.png',
            vibrate: [200, 100, 200],
            data: {
                url: event.data.url || '/notifications'
            },
            actions: [
                { action: 'view', title: 'Ver' },
                { action: 'close', title: 'Cerrar' }
            ]
        };

        event.waitUntil(
            self.registration.showNotification(title, options)
        );
    }
});

// Manejar clic en la notificación
self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    if (event.action === 'close') {
        return;
    }

    const urlToOpen = event.notification.data.url || '/notifications';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.includes(urlToOpen) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(urlToOpen);
            }
        })
    );
});
