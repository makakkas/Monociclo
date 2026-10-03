# Monociclo

**Hecho por makakas · para uso personal.**

Un tomate en un monociclo que te acompaña mientras estudias o trabajas. HUD oscuro,
coral y menta, botones claros y algún regaño con humor. El personaje salta y levanta
los brazos al empezar o completar un bloque; al pausar pone cejas enfadadas y
mirada fija. Durante el descanso se queda dormido.

## Empezar

Abre **Monociclo.exe**. No necesitas Python ni un navegador adicional.
El primer arranque puede tardar unos segundos porque el EXE extrae su navegador
incluido. Mantén `whitelist.json` junto al ejecutable, en una carpeta donde puedas
escribir. Si no existe, se crea con valores predeterminados.

1. Pulsa **Mis permisos** antes de empezar.
2. En **Aplicaciones**, marca lo que puedes usar. Hay apps habituales, apps con
   ventanas abiertas, buscador y un botón para elegir cualquier `.exe`.
3. En **Páginas web**, añade tus enlaces y elige **Sólo esta página** o **Sitio completo**.
4. En **Mi Pomodoro**, ajusta los minutos de trabajo y descanso.
5. Guarda y pulsa **¡A enfocarse!**. Para usar tus enlaces, abre **Web de enfoque**.

Guardar reinicia el temporizador y el contador. Durante una sesión en marcha no
se pueden cambiar los permisos; primero pausa o termina. Cancelar cierra el editor
sin modificar tu configuración.

## El tomate y sus controles

- **¡A enfocarse! / Reanudar**: inicia o continúa el tiempo y el guardián.
- **Pausar**: conserva el tiempo restante, libera el PC y el tomate te mira serio.
- **Terminar**: libera el PC y reinicia la sesión.
- **×**: cierra Monociclo y su navegador.
- **Esc con el HUD seleccionado**: termina la sesión.
- Arrastra el encabezado para mover el HUD.

Los ciclos trabajo → descanso → trabajo se repiten automáticamente. Cada trabajo
completado suma un tomate. El navegador de enfoque conserva su lista de URLs
también en descanso; puedes usar las otras aplicaciones libremente en descanso
o pausa.

## Qué significa permitir una URL

**Sólo esta página** permite protocolo, dominio, puerto, ruta y parámetros exactos.
Las anclas `#seccion` se permiten dentro de la misma página. **Sitio completo**
permite todas las rutas del mismo origen: protocolo, dominio y puerto; no permite
otros subdominios.

El navegador bloquea otras páginas, redirecciones hacia ellas, documentos en
marcos no permitidos, ventanas emergentes, descargas, acceso a archivos locales y
permisos de cámara/micrófono. No tiene barra para navegar libremente. Sus cookies
y sesiones son temporales: los datos del navegador no se conservan al cerrarlo.

Imágenes, estilos, scripts y solicitudes de API pueden venir de otros servidores
para que la página funcione. El filtro controla la navegación a documentos, no
todo el tráfico de red ni lo que una app web muestra dentro de una URL permitida.
Un inicio de sesión, un vídeo o una redirección puede necesitar URLs adicionales:
autoriza esos enlaces o el sitio completo si te conviene. Algunos proveedores no
admiten iniciar sesión en navegadores integrados.

Con **Usar navegación controlada** activado, los navegadores externos conocidos
(Edge, Chrome, Firefox, Thorium y otros) se excluyen de los permisos efectivos,
aunque estuvieran en un JSON antiguo. Para permitir un navegador externo completo,
desactiva esa opción y márcalo en Aplicaciones; en ese caso sus pestañas no se filtran.

## Ejecutar el código y compilar

Python 3.10+ en Windows. El código sigue en **focus_guard.py**, en un solo archivo.
El HUD usa Tkinter y el navegador usa PySide6/QtWebEngine en un proceso separado.
Los comentarios son cotidianos y la organización es deliberadamente casera,
con el editor de permisos al final del archivo.

Desde PowerShell, en esta carpeta:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe focus_guard.py
```

Dependencias:

```powershell
py -m pip install pywin32 psutil PySide6 pyinstaller
```

Compilación exacta de un único ejecutable sin consola:

```powershell
py -m PyInstaller --clean --onefile --windowed --name Monociclo focus_guard.py
```

Si usas `.venv`, reemplaza `py` por `.\.venv\Scripts\python.exe`.
Resultado: **dist\Monociclo.exe**. QtWebEngine se incluye, por eso el EXE pesa
más que la versión inicial. Tkinter viene con Python oficial para Windows.

También puedes ejecutar **compilar.ps1** desde PowerShell. Crea un entorno virtual,
instala las dependencias y compila con un PATH temporal reducido para evitar mezclar
DLL de otras herramientas con Qt. En este caso deja Monociclo.exe en esta carpeta.

## JSON, si prefieres editarlo a mano

El JSON anterior sigue siendo compatible. Mis permisos guarda los campos nuevos;
no necesitas borrar tus aplicaciones existentes.

```json
{
  "work_minutes": 25,
  "break_minutes": 5,
  "whitelist": ["code.exe", "notepad.exe"],
  "strict_web": true,
  "urls": [
    {"url": "https://docs.python.org/3/library/tkinter.html", "scope": "exact"},
    {"url": "https://docs.python.org/3/", "scope": "site"}
  ]
}
```

Los minutos deben ser mayores que cero y hasta 240; admiten decimales. Los nombres
de ejecutables no distinguen mayúsculas/minúsculas y no admiten rutas ni comodines.
El editor guarda mediante reemplazo atómico para evitar archivos a medias. Si
editas el JSON externamente, reinicia Monociclo para cargar tus cambios.

## Alcance y comprobaciones

Es una herramienta de autocontrol para uso personal: puedes pausarla, cerrarla o
modificar sus reglas. Windows puede impedir identificar o minimizar procesos
protegidos o con más privilegios. Algunas apps no procesan inmediatamente la
petición de minimización. El HUD no cubre el escritorio seguro de UAC.

Los permisos son por nombre de ejecutable. No filtran actividades en segundo
plano ni las webs de otras apps permitidas que incorporen un navegador. La lista
de navegadores externos es explícita: uno diferente o renombrado se tratará como
cualquier ejecutable que hayas permitido. El guardián revisa el primer plano cada
500 ms y minimiza ventanas; no cierra tus aplicaciones.

Se prueban ciclos, pausas, permisos, guardado, límites de URL, redirecciones,
ventanas emergentes y cambios de URL por JavaScript. Las pruebas de bloqueo usan
ventanas y un servidor local propios, sin activar bloqueo sobre las apps del usuario.

Esta versión pasó 21 pruebas en Windows 10 con Python 3.12 x64. También se abrió
el EXE para verificar HUD sin consola, topmost, creación del JSON junto al EXE y
cierre limpio. El navegador empaquetado cargó una página permitida y bloqueó
enlaces, marcos, ventanas emergentes y cambios de dirección por JavaScript.
La captura de pantalla automatizada no respondió; el layout se comprobó mediante
medidas de widgets y pruebas de los controles, sin una revisión final por captura.

Fuentes: [Qt WebEngine](https://doc.qt.io/qt-6.11/qwebenginepage.html),
[perfiles temporales](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEngineProfile.html),
[PyInstaller](https://pyinstaller.org/en/stable/usage.html),
[ShowWindowAsync](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindowasync).
