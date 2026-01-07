Este es el contexto completo del proyecto. Léelo como contrato. No programes aún.

1. Descripción general del proyecto
Existe una cuenta automatizada en X (Twitter) llamada @Efemerides_Imp
URL: https://x.com/Efemerides_Imp
Es un bot automático que publica efemérides históricas rigurosas relacionadas exclusivamente con:
España
Sus reinos históricos
El Imperio español
El contenido consiste en:
Un tuit titular diario (efeméride exacta del día)
Un hilo explicativo de entre 1 y 5 tuits, desarrollando:
contexto histórico
consecuencias
relevancia del hecho
El bot está desarrollado en Python, se ejecuta mediante GitHub Actions, publica en horarios óptimos y gestiona correctamente errores de la API de X (incluidos rate limits).
2. Principio editorial irrenunciable
Cada publicación debe ser una efeméride exacta del día.
Esto significa:
Si hoy es dd/mm, el evento histórico DEBE haber ocurrido exactamente ese mismo día y mes, independientemente del año.
El año debe ser correcto y verificable.
Si no existe certeza histórica absoluta → NO SE PUBLICA NADA.
La prioridad del proyecto es:
rigor histórico > frecuencia > engagement
3. Problema crítico detectado
Se ha comprobado que algunos tweets publicados en el pasado no cumplen el criterio de efeméride exacta, por ejemplo:
El evento ocurrió en otro día del mismo mes.
El evento ocurrió en otro mes.
El evento tiene fecha incierta o varias fechas posibles.
El texto menciona una fecha errónea.
El tweet actúa como efeméride diaria sin que el dd/mm del evento coincida con el día de publicación.
Ejemplo real detectado:
Error histórico en la datación de Granada 1492, que actuó como detonante para endurecer todo el sistema.
Este tipo de errores rompe el concepto de efeméride y daña la credibilidad del proyecto.
4. Objetivos del sistema
Objetivo principal
Garantizar que TODAS las publicaciones sean efemérides exactas del día, sin excepciones.
Objetivos secundarios
Auditar retrospectivamente los tweets ya publicados para detectar errores de fecha.
Prevenir errores futuros mediante validación automática estricta previa a la publicación.
5. Definición estricta de “efeméride válida”
Un evento es publicable solo si:
Existe una fecha histórica exacta (día + mes).
El día y mes coinciden con la fecha de publicación.
La fecha está bien documentada en fuentes históricas fiables.
No hay ambigüedad ni versiones contradictorias relevantes.
Casos NO publicables
Eventos con fecha aproximada.
Eventos con varias fechas sin consenso claro.
Eventos prolongados (guerras, reinados, expediciones).
Eventos solo fechables por año.
Fechas inferidas o “tradicionales” sin base sólida.
Regla de oro:
Ante la mínima duda → silencio editorial.
6. Arquitectura lógica del sistema (alto nivel)
6.1 Generación inicial de efemérides
OpenAI genera 20–40 eventos candidatos para el día exacto (dd/mm).
Prompt extremadamente restrictivo:
Solo hechos reales y documentados.
Prohibido inventar fechas.
Prohibido aproximar fechas.
Si hay duda → excluir el evento.
❌ No se usan webs externas
❌ No se usa scraping
❌ No se consulta Wikipedia directamente
✅ OpenAI es la única fuente de generación
6.2 Doble verificación histórica (autocontrol)
Se ejecuta una segunda llamada a OpenAI, aún más estricta:
Revisa uno por uno los eventos generados.
Valida:
fecha exacta
coherencia histórica
ausencia de ambigüedades
Elimina cualquier evento que no tenga alta certeza histórica.
Si tras esta verificación:
No queda ningún evento válido → no se publica nada ese día.
6.3 Sistema de scoring propio
Entre los eventos válidos, se priorizan aquellos que:
Priorizan
España o el Imperio español como actor principal.
Hechos:
militares
políticos
diplomáticos
de Estado
Siglos clave: XV–XIX
Penalizan
Cultura pop
Premios, entretenimiento, efemérides triviales
Conflictos extranjeros donde España no sea protagonista
6.4 Anti-repetición
Antes de publicar:
Se consulta el historial reciente de la cuenta en X.
Si un evento del mismo día ya fue publicado en años anteriores → se descarta.
Se busca un evento alternativo o se opta por no publicar.
6.5 Anti-contradicciones internas
Antes de publicar:
Se analiza el tuit titular + hilo completo.
Si hay contradicciones internas (fechas, cifras, hechos):
El sistema corrige automáticamente el mínimo texto necesario.
Se garantiza coherencia total entre:
titular
hilo
fecha
7. Auditoría de tweets ya publicados
El sistema debe permitir auditoría retrospectiva mediante:
API de X (si disponible)
Exportaciones de tweets (JSON / CSV)
Texto proporcionado manualmente
Para cada tweet auditado:
Identificar el evento histórico.
Asumir que pretende ser efeméride diaria.
Obtener la fecha real del evento.
Comparar con la fecha de publicación.
Determinar si el dd/mm coincide.
8. Fuentes históricas autorizadas para validación
Orden de preferencia:
Wikidata
(propiedades de fecha según tipo de evento)
Wikipedia
(artículo principal del evento)
Condición obligatoria:
La fuente debe proporcionar día y mes.
Si solo hay año → evento no válido como efeméride diaria.
9. Output esperado de la auditoría
Para cada tweet erróneo:
ID o enlace del tweet
Texto completo
Fecha de publicación
Fecha real del evento
Motivo del error:
día incorrecto
mes incorrecto
ambigüedad histórica
fecha incierta
Recomendación:
corregible / no corregible
fecha correcta si existe
Formato:
JSON
CSV
o estructura tabular
10. Gestión de errores de la API de X
Si la API devuelve 429 (rate limit):
El hilo completo se guarda en pending_tweet.json.
En la siguiente ejecución:
Se publica primero el contenido pendiente.
Nunca se pierde contenido generado.
Nunca se duplican publicaciones.
11. Restricciones técnicas absolutas
❌ No scraping
❌ No uso de webs externas
❌ No creatividad histórica
❌ No fechas aproximadas
❌ No “rellenar huecos”
✅ OpenAI genera
✅ OpenAI verifica
✅ OpenAI se autocensura
✅ Si no hay efeméride segura → no se publica nada
12. Filosofía del proyecto
Este proyecto se rige por una máxima clara:
Más vale no publicar que publicar algo incorrecto.
La credibilidad histórica es el activo principal del bot.
