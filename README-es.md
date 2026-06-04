<!-- Selector de idioma -->
[![English](https://img.shields.io/badge/English-lightgrey?style=for-the-badge)](README-en.md)
[![Türkçe](https://img.shields.io/badge/T%C3%BCrk%C3%A7e-lightgrey?style=for-the-badge)](README-tr.md)
[![Deutsch](https://img.shields.io/badge/Deutsch-lightgrey?style=for-the-badge)](README-de.md)
[![Español](https://img.shields.io/badge/Espa%C3%B1ol-6e4f9e?style=for-the-badge)](README-es.md)

<p align="center">
  <img src="docs/dersis.png" alt="Logotipo de DERSİS" width="240">
</p>

<h1 align="center">DERSİS</h1>

<p align="center"><b>Software de horarios de clase inteligente y totalmente sin conexión para colegios y universidades.</b></p>

---

## Índice

- [Descripción general](#descripción-general)
- [Funciones](#funciones)
- [Instalación](#instalación)
- [Ejecutar desde el código fuente](#ejecutar-desde-el-código-fuente)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Replicación y alternativas](#replicación-y-alternativas)
- [Hoja de ruta y posibilidades de mejora](#hoja-de-ruta-y-posibilidades-de-mejora)
- [Guía de uso](#guía-de-uso)
- [Informar de errores](#informar-de-errores)
- [Licencia y uso](#licencia-y-uso)

---

## Descripción general

**DERSİS** (del turco *Ders Programı Hazırlama Sistemi*, «Sistema de Preparación de Horarios
de Clase») es una aplicación de escritorio que crea, optimiza y gestiona **horarios de clase
semanales** para instituciones educativas.

Elaborar un horario a mano es difícil: debe asegurarse, al mismo tiempo, de que ningún
docente esté en dos sitios a la vez, de que ningún aula tenga reserva doble, de que ningún
grupo de estudiantes tenga clases solapadas, de que cada clase quepa en las horas
disponibles y de que nunca se supere la capacidad de las aulas. Además, un *buen* horario
mantiene pequeños los huecos, reparte la carga de forma equilibrada entre los días y respeta
las preferencias. DERSİS hace todo esto por usted, de forma automática, sin dejar de
darle el control.

Funciona **íntegramente en su propio ordenador**. **No requiere inicio de sesión, ni cuenta,
ni conexión a internet** — nunca. Abre la aplicación y empieza a trabajar.

**Para quién es:** oficinas de horarios universitarias, equipos directivos de centros
escolares, coordinadores de departamento y cualquier persona que necesite horarios semanales
sin conflictos.

---

## Funciones

> Todas las funciones siguientes están implementadas en la aplicación. Para conocer su
> ubicación exacta en el código fuente, consulte [`docs/FEATURES.md`](docs/FEATURES.md).

### Motor de programación
- **Prevención automática de conflictos** — protege frente a coincidencias de docentes,
  coincidencias de aula, solapamientos de grupos de estudiantes, clases demasiado largas para
  las horas disponibles y aulas con la capacidad superada. También respeta los días y las
  horas disponibles de cada docente.
- **Optimizador multimotor** — combina tres técnicas: una pasada heurística rápida de
  colocación, una Búsqueda por Vecindario Amplio (LNS) con 7 estrategias adaptativas de
  «destruir y reparar» y el solucionador de restricciones **OR-Tools CP-SAT** de Google para
  la optimización exacta.
- **Puntuación de calidad con 14 parámetros** — equilibra la compacidad del docente, los
  huecos del alumnado, el reparto de la carga diaria, la fragmentación, los cambios de aula,
  las preferencias por franja horaria y más.
- **Orden según la dificultad** — las clases más difíciles de colocar se programan primero.

### Colocación inteligente
- **Colocación automática de una sola clase** en la mejor franja disponible.
- **Programación por lotes** de muchas clases sin colocar a la vez.
- **Reprogramación completa** para optimizar todo el horario desde cero.
- **Arrastrar y soltar** sobre la cuadrícula con **comprobación de conflictos en tiempo real**
  (una colocación válida se resalta en verde y una no válida en rojo).

### IA explicable
- Cada colocación automática viene con un **desglose de pros y contras en lenguaje claro**.
- Cuando se rechaza un movimiento, la aplicación explica **exactamente qué regla se
  incumplió**.
- Las optimizaciones terminan con un **veredicto de calidad y métricas de antes/después**.
- **Negociación de restricciones:** cuando una clase no encaja de ninguna manera, la
  aplicación sugiere relajaciones concretas (o qué clase existente mover) para hacer hueco.

### Aprende de usted
- DERSİS **registra sus movimientos manuales y sus sugerencias aceptadas o rechazadas** y
  adapta poco a poco su puntuación a su forma de programar. Las preferencias aprendidas se
  guardan y se conservan entre sesiones.

### Control y protección
- **Niveles de protección** por clase: movible, protegida de forma flexible, solo el mismo
  día, solo si mejora, bloqueada o totalmente fijada.
- **Objetivos de optimización:** seis controles deslizantes (compacidad del docente,
  compacidad del alumnado, uso de las aulas, equidad, mínima alteración, preferencia por las
  primeras horas) y seis perfiles predefinidos (equilibrado, prioridad al docente, prioridad
  al alumnado, mínimo cambio, eficiente en espacio, apto para la mañana).
- **Análisis del impacto de los cambios:** previsualice cómo afectaría al horario actual un
  cambio en la configuración antes de aplicarlo.

### Vistas y análisis
- **Cuatro maneras de ver** el horario: por aula, por docente, por grupo de estudiantes y una
  matriz completa de «mostrar todo».
- **Panel de análisis** con una puntuación de calidad de 0 a 100 y una nota de la A a la F,
  métricas por docente, por grupo y por aula, gráficos y recomendaciones accionables.

### Importación y exportación
- **Importación desde Excel** de docentes, aulas, ramas y clases, con validación, detección de
  duplicados y agrupación automática de las clases conjuntas.
- **Generador de plantillas de Excel** que produce un libro listo para rellenar con filas de
  ejemplo en el idioma elegido.
- **Exportación** del horario terminado a **Excel** (con colores, varias hojas), **CSV** y
  **PDF**.

### Experiencia y privacidad
- **Interfaz multilingüe** — más de 20 idiomas, elegidos en el primer arranque desde un
  selector con banderas (22 opciones de bandera), incluida la compatibilidad de derecha a
  izquierda para árabe y persa.
- **Tutorial interactivo** — un recorrido guiado tipo «foco» para los nuevos usuarios.
- **Totalmente sin conexión** — sin ningún tipo de llamada de red; todas las funciones están
  desbloqueadas de forma local.
- **Almacenamiento local cifrado** — los horarios se guardan en un formato de archivo `.egu`
  cifrado (AES-256-GCM) dentro de su carpeta `Documents/Dersis/`, con guardado automático.
- **Informe de errores dentro de la app** — un formulario integrado prepara un correo por
  usted (consulte [Informar de errores](#informar-de-errores)); la aplicación en sí nunca
  transmite nada.

---

## Instalación

Esta sección es para quienes solo quieren **usar** DERSİS, sin necesidad de programar.

### En Windows (recomendado)

1. Consiga el archivo de instalación. Se llama algo parecido a
   **`Dersis_Setup_v1.0.0.exe`** (el número de versión puede variar).
2. **Haga doble clic** en el instalador y siga el asistente en pantalla (elija un idioma,
   acepte el acuerdo, escoja la ubicación de instalación y pulse *Instalar*).
3. Al terminar, abra **DERSİS** desde el menú Inicio o el acceso directo del escritorio.
4. La aplicación se abre **directamente en la ventana principal** — no hay registro, inicio de
   sesión ni activación.

> **Dónde se guarda su trabajo:** DERSİS guarda todo dentro de su carpeta personal de
> Documentos, en `Documents\Dersis\` (horarios, ajustes, registros y exportaciones). Sus
> datos nunca salen de su ordenador.

### En otros sistemas

La aplicación está construida con Python y el conjunto de herramientas Qt, y también puede
ejecutarse en Linux (consulte
[Ejecutar desde el código fuente](#ejecutar-desde-el-código-fuente)). El **instalador
preparado es, por ahora, solo para Windows.** La compatibilidad con macOS está
*por confirmar*.

---

## Ejecutar desde el código fuente

Esta sección es para personas con soltura en la línea de comandos que quieran ejecutar o
compilar DERSİS por su cuenta. Necesita **Python 3.10 o posterior**.

### 1. Obtenga el código e instale las dependencias

```bash
# Cree un entorno aislado
python -m venv .venv

# Actívelo
.venv\Scripts\activate        # Windows
source .venv/bin/activate      # Linux / macOS

# Instale las bibliotecas necesarias
pip install -r requirements.txt
```

> En **Linux** también necesita las bibliotecas Qt del sistema de las que depende PyQt6
> (instálelas con el gestor de paquetes de su distribución si la aplicación no arranca).

### 2. Ejecute la aplicación

```bash
python scheduler_gui.py
```

### 3. (Opcional) Compile un instalador para Windows

El método de empaquetado recomendado incluye una copia privada de Python para que el
resultado funcione en cualquier equipo con Windows 10/11 (64 bits) sin configuración
adicional:

```bat
build_embed.bat          :: genera build\Dersis.dist\
iscc installer.iss       :: genera Output\Dersis_Setup_v<versión>.exe
```

`build_embed.bat` descarga el entorno de ejecución integrable oficial de Python, instala
todas las dependencias fijadas en `requirements-lock.txt`, las verifica con `verify_deps.py`,
copia la aplicación y sus recursos y crea los lanzadores. Un segundo método con **Nuitka**
(`build_nuitka.bat`) compila a código nativo. Todos los detalles, las herramientas necesarias
(Inno Setup) y las opciones están en [`BUILD.md`](BUILD.md).

---

## Estructura del proyecto

```
scheduler_gui.py              Punto de entrada — inicia la aplicación
scheduler_app/
  core/         Motor de programación: modelos de datos, reglas de conflicto, el
                optimizador multimotor (heurística + LNS + CP-SAT), puntuación, análisis
                y el motor de explicaciones. Aquí no hay código de interfaz.
  ui/           La interfaz PyQt6: ventana principal, todos los diálogos, el renderizador
                de horarios de arrastrar y soltar, el panel de análisis, el tutorial y las
                tablas de traducción multilingües.
  data_io/      Importación y exportación Excel/CSV/PDF, y el generador de plantillas Excel.
  learning/     Registra sus interacciones y adapta los pesos de puntuación con el tiempo.
  storage/      Formato de archivo .egu cifrado (AES-256-GCM) y gestión de rutas.
  assets/       Iconos de la aplicación.
flags/          Imágenes de banderas de países para el selector de idioma.
docs/           Documentación y el logotipo de la aplicación.
installer/      Recursos de Inno Setup (texto de licencia que muestra el instalador, imágenes).
VERSION         La única fuente de verdad para el número de versión.
build_embed.bat / build_nuitka.bat / installer.iss   Scripts de compilación y empaquetado.
```

Hay un desglose completo, archivo por archivo, en
[`docs/STRUCTURE.md`](docs/STRUCTURE.md), y un mapa arquitectónico detallado en la carpeta
[`dersis-mapped/`](dersis-mapped/).

---

## Replicación y alternativas

Si usted es una persona desarrolladora o una institución que quiere construir algo parecido —o
reproducir esta misma configuración—, aquí tiene de qué está hecho DERSİS y cómo encajan las
piezas.

**Pila tecnológica**

| Aspecto | Lo usado aquí | Alternativas habituales |
|---|---|---|
| Interfaz de escritorio | PyQt6 (Qt 6) | PySide6, Tkinter, interfaz web (Electron / navegador) |
| Optimización exacta | Google OR-Tools CP-SAT | Otros solucionadores CP/MILP (p. ej. CP-Optimizer, Gurobi) |
| Búsqueda heurística | Heurística propia + Búsqueda por Vecindario Amplio | Recocido simulado, algoritmos genéticos, búsqueda tabú |
| Lectura/escritura de hojas | openpyxl + pandas | xlsxwriter, solo el módulo csv |
| Salida en PDF | reportlab | WeasyPrint, fpdf2 |
| Cifrado en reposo | `cryptography` (AES-256-GCM) | SQLCipher, llavero del sistema operativo |
| Empaquetado en Windows | Python integrable + Inno Setup | PyInstaller, Nuitka, MSIX |

**Enfoque arquitectónico para reproducirlo**

1. **Mantenga el motor libre de interfaz.** Todo el paquete `core/` opera con diccionarios
   sencillos de Python, lo que facilita probarlo, serializarlo y ejecutarlo en procesos
   paralelos, con independencia de la interfaz.
2. **Modele por separado las restricciones estrictas y las flexibles.** Las estrictas (sin
   conflictos) se aplican de forma absoluta; los objetivos flexibles (compacidad, equilibrio)
   se convierten en una puntuación ponderada.
3. **Disponga los optimizadores en capas.** Empiece con una heurística rápida, mejórela con
   búsqueda local y, opcionalmente, invoque un solucionador exacto, pasando el resultado de
   cada etapa a la siguiente.
4. **Haga explicables las decisiones.** Generar una razón legible junto a cada elección
   automática es lo que convierte un solucionador «caja negra» en una herramienta en la que
   la gente confía.
5. **Distribuya con un entorno de ejecución incluido.** Enviar una versión privada de Python
   (el método integrable) evita los problemas de «en mi equipo funciona» para usuarios sin
   conocimientos técnicos.

Puede estudiar la estructura para su propio aprendizaje. Tenga en cuenta los
[términos de la licencia](#licencia-y-uso) antes de cualquier reutilización institucional.

---

## Hoja de ruta y posibilidades de mejora

Estas son direcciones **realistas y aún no comprometidas**, recogidas para que pueda valorar
su viabilidad. Los puntos de aquí son posibilidades (*por confirmar*), no promesas.

- **Instaladores nativos para macOS y Linux.** Los scripts de compilación son hoy archivos
  `.bat` de Windows; el código de la app es multiplataforma, así que un empaquetado nativo de
  cada plataforma es viable.
- **Un conjunto de pruebas automatizadas.** El repositorio se distribuye actualmente **sin
  archivos de prueba**; la integración continua solo ejecuta comprobaciones de versión, de
  archivos de compilación y de importación. Añadir pruebas unitarias en torno al motor
  `core/` sería una mejora de gran valor y bajo riesgo.
- **Completar las traducciones del instalador.** La interfaz de la app cubre más de 20
  idiomas, pero el asistente de instalación de Windows se distribuye hoy en 13. Podrían
  añadirse las traducciones restantes del asistente.
- **Sincronización opcional multiusuario / en la nube.** Hoy DERSİS es totalmente sin conexión
  por diseño; un modo de sincronización o base de datos compartida, opcional y de activación
  voluntaria, sería una incorporación amplia pero viable.
- **Interfaz de complementos o de scripting.** Como el motor está libre de interfaz y se basa
  en diccionarios, una API pública o un punto de extensión para restricciones u objetivos
  propios es técnicamente sencillo.
- **Más formatos de exportación / plantillas.** Sobre los exportadores Excel/CSV/PDF actuales
  podrían añadirse diseños de informe adicionales.

---

## Guía de uso

Un recorrido completo del flujo principal. (Los atajos de teclado se muestran entre
paréntesis.)

### 1. Primer arranque
En el primer inicio, elija su **idioma** en el selector con banderas. A continuación, un
**tutorial interactivo** opcional ofrece un recorrido guiado: puede hacerlo o saltarlo y
volver a reproducirlo más tarde desde **Ayuda → Tutorial**.

### 2. Configure su entorno (Editar → Editar configuración)
Defina los elementos básicos sobre los que se construye su horario:
- **Días** — qué días de la semana están activos (p. ej. de lunes a viernes).
- **Franjas horarias** — las horas disponibles cada día (p. ej. 09:00, 10:00, …).
- **Aulas** — el nombre y la capacidad de cada aula.
- **Años y ramas** — sus grupos de estudiantes (p. ej. *1.er año – Informática*).
- **Docentes** — el personal docente, con días y horas disponibles/no disponibles
  opcionales.

### 3. Añada sus clases
- **Añadir una sola clase** (`Ctrl+Shift+A`): indique un nombre (y un código opcional), un
  docente, una duración (cuántas franjas consecutivas), el grupo o los grupos de estudiantes
  de destino, el número de participantes y un tipo de ubicación (presencial, en línea o
  despacho del docente). Opcionalmente puede **fijar** la clase a un día/hora/aula concretos o
  añadir **restricciones** (días, horas o aulas permitidos/excluidos).
- **Añadir en bloque** (`Ctrl+Shift+B`): rellene una tabla tipo hoja de cálculo y programe
  muchas clases de una vez.
- **Importar desde Excel:** genere la plantilla, rellénela e impórtela; DERSİS valida los
  datos e informa de cualquier problema antes de añadir las clases.

### 4. Coloque las clases
- **Arrastre y suelte** cualquier clase sobre la cuadrícula; la app valida el movimiento al
  instante.
- **Colocación automática de una clase** (`Ctrl+P`): la app propone la mejor franja con una
  explicación; acéptela o revise alternativas.
- **Programe por lotes** todas las clases sin colocar en una sola operación.
- **Reprogramación completa** (`Ctrl+R`): vuelva a optimizar todo el horario.

### 5. Revise y ajuste
Cambie entre las vistas **por aula**, **por docente**, **por grupo de estudiantes** y
**mostrar todo**. Las clases tienen colores según el año y llevan distintivos de su nivel de
protección. Cualquier conflicto o aviso se muestra con claridad; haga clic derecho en una
clase para acciones rápidas (colocar, quitar, fijar, proteger, editar, eliminar).

### 6. Optimice según sus prioridades
Abra el diálogo de reprogramación y ajuste los **controles deslizantes de objetivos** o elija
un **perfil**. Ejecútelo y lea después el resumen de resultados: qué se movió, qué (si acaso)
no se pudo colocar y cómo cambió la calidad general.

### 7. Analice la calidad
Abra el **Panel** para ver la puntuación de calidad de 0 a 100 y la nota de la A a la F,
además de pestañas para aulas, docentes, alumnado y carga general, con gráficos y sugerencias
de mejora.

### 8. Exporte y comparta
Exporte el horario terminado a **Excel**, **CSV** o **PDF** desde el menú Archivo o el botón
de exportación de cada vista.

### 9. Guarde y vuelva a cargar
- **Guardar** (`Ctrl+S`) — escribe un guardado automático y un archivo `.egu` cifrado con
  marca de tiempo en `Documents\Dersis\saves\`.
- **Abrir** (`Ctrl+O`), **Nuevo** (`Ctrl+N`), **Deshacer** (`Ctrl+Z`),
  **Rehacer** (`Ctrl+Y`).

---

## Informar de errores

¿Ha encontrado un problema o tiene una sugerencia? Hay dos formas sencillas de informar.

1. **Desde dentro de la app:** use el botón **Informar de error**. Si la aplicación llegara a
   fallar, también aparece un diálogo de fallo seguro. Ambos preparan un correo por usted —
   relleno con la versión de la app, su sistema operativo, la gravedad y los pasos— y lo
   abren en su programa de correo predeterminado. **La app no envía nada por sí sola;** usted
   mantiene el control del mensaje. Si no hay un programa de correo configurado, el texto del
   informe se copia al portapapeles para que pueda pegarlo.

2. **Por correo directamente:** escriba a
   **[dersis.app@gmail.com](mailto:dersis.app@gmail.com)**. Describa qué hizo, qué
   esperaba y qué ocurrió en su lugar, e indique su versión de DERSİS y su sistema operativo.

---

## Licencia y uso

**DERSİS es ahora gratuito para todos los usuarios individuales.** Puede descargarlo,
instalarlo y usarlo para su trabajo personal sin coste alguno.

**Las instituciones necesitan una licencia para el uso institucional.** Las instituciones —
incluidas **universidades, facultades, colegios, departamentos, centros de investigación,
unidades administrativas o cualquier subórgano universitario**— **no pueden incrustar,
integrar, desplegar ni incorporar oficialmente DERSİS en sus propios sistemas institucionales
sin pagar una tarifa de licencia o de integración.**

Si su institución desea **uso institucional, integración, incrustación, despliegue,
personalización o adopción oficial**, póngase en contacto para acordar una licencia:

> **Contacto para licencias institucionales:**
> [dersis.app@gmail.com](mailto:dersis.app@gmail.com)

Consulte el archivo [`LICENSE.md`](LICENSE.md) de nivel superior para ver los términos
completos.

---

<p align="center">
  <a href="README-en.md">English</a> ·
  <a href="README-tr.md">Türkçe</a> ·
  <a href="README-de.md">Deutsch</a> ·
  <a href="README-es.md">Español</a>
</p>
