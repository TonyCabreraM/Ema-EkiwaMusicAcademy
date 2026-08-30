function enviarImagenConTexto(imagenPath, texto) {
    const fs = require('fs');
    const path = require('path');
    const imagePath = path.resolve(imagenPath);
    const imageBuffer = fs.readFileSync(imagePath);

    const payload = {
        image: imageBuffer,
        text: texto
    };

    const url = 'https://wa.me/';
    const response = fetch(url, { method: 'POST', body: JSON.stringify(payload) });
    return response.status; 
}

// Testeo
console.log(enviarImagenConTexto('.../card/12345.png', 'Hola Mundo'));