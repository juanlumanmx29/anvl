# ANVL — Session Monitor & Handoff Tool for Claude Code

## Qué es ANVL

ANVL es una herramienta CLI en Python que monitorea sesiones de Claude Code, detecta cuando una sesión se ha inflado (waste alto), y genera un archivo `handoff.md` estructurado para continuar el trabajo en una sesión nueva sin perder contexto.

El nombre viene de "anvil" (yunque) — donde se forjan sesiones nuevas a partir del metal de las anteriores.

---

## Problema que resuelve

Claude Code envía todo el historial de la conversación en cada turno. En el turno 1 envías 5k tokens, en el turno 50 estás enviando 170k tokens para recibir 500 de respuesta. Esto quema la quota del plan exponencialmente.

ANVL detecta esta inflación y te ayuda a hacer un corte limpio: genera un resumen de lo trabajado y lo pendiente en un `.md` que arrastras a una nueva sesión fresca.

---

## Stack técnico

- **Lenguaje:** Python 3.11+
- **Sin dependencias externas pesadas** — solo stdlib + `rich` para la terminal
- **Plataforma:** Windows (PowerShell), con soporte Linux/Mac
- **Paths de Claude Code en Windows:** `%USERPROFILE%\.claude\`

---

## Arquitectura

```
anvl/
├── anvl/
│   ├── __init__.py
│   ├── cli.py              # Entry point CLI (argparse)
│   ├── parser.py            # Parser de archivos JSONL de sesión
│   ├── analyzer.py          # Cálculo de waste factor y métricas
│   ├── handoff.py           # Generador de handoff.md
│   ├── monitor.py           # Monitor en terminal (rich live display)
│   ├── web/
│   │   ├── server.py        # Servidor localhost para dashboard
│   │   └── dashboard.html   # Dashboard HTML (single file, vanilla JS)
│   └── hooks.py             # Generador de hooks para Claude Code
├── setup.py
├── pyproject.toml
├── README.md
└── tests/
```

---

## Estructura de datos de sesión (JSONL)

Ubicación: `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`

Cada línea es un JSON independiente. Tipos relevantes:

```
{"type": "ai-title", "sessionId": "...", "aiTitle": "..."}
{"type": "user", "message": {"role": "user", "content": [...]}, ...}
{"type": "assistant", "message": {"role": "assistant", "content": [...], "usage": {...}}, ...}
```

### Campos de usage (dentro de mensajes assistant)

```json
{
  "input_tokens": 3,
  "cache_creation_input_tokens": 9713,
  "cache_read_input_tokens": 11317,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 0,
    "ephemeral_1h_input_tokens": 9713
  },
  "output_tokens": 22
}
```

**Interpretación:**
- `input_tokens` — tokens nuevos no cacheados (generalmente bajo)
- `cache_creation_input_tokens` — contexto nuevo que se cachea para el siguiente turno
- `cache_read_input_tokens` — contexto antiguo releído del cache. **Este es el número que crece turno a turno y es el indicador principal de waste**
- `output_tokens` — tokens generados por Claude en la respuesta

**Waste factor** = `(input_tokens + cache_creation_input_tokens + cache_read_input_tokens) / output_tokens`

Una sesión sana tiene `cache_read_input_tokens` < 30k. Una sesión inflada supera 100k+.

---

## Comandos CLI

### `anvl status`
Muestra el estado de la sesión activa del proyecto actual:
- Tokens totales enviados/recibidos
- Waste factor actual
- Número de turnos
- Tendencia (creciendo/estable)
- Semáforo: verde (<3x waste), amarillo (3x-7x), rojo (>7x)

### `anvl monitor`
Monitor en vivo en la terminal usando `rich`. Actualiza cada vez que el JSONL cambia (file watcher).

Muestra:
- Barra de progreso del waste factor
- Gráfico ASCII de tokens por turno (cache_read creciendo)
- Turno actual y timestamp
- Alerta visual cuando supera el umbral

### `anvl dashboard`
Levanta un servidor HTTP en `localhost:3000` con un dashboard visual.

Muestra:
- Gráfico de línea: tokens por turno (input, cache_read, cache_creation, output)
- Waste factor en tiempo real con gauge visual
- Lista de sesiones del proyecto con métricas resumen
- Historial de sesiones anteriores para comparar
- Auto-refresh vía polling o SSE

### `anvl handoff`
Genera `handoff.md` en la raíz del proyecto con la siguiente estructura:

```markdown
# ANVL Handoff — [Título de sesión]
> Generado: 2026-04-07 16:30 | Sesión: abc123 | Turnos: 47 | Waste: 8.3x

## Resumen de sesión
[Extraído del ai-title y los primeros mensajes del usuario]

## Trabajo completado
- [Extraído de los mensajes: archivos creados/editados, comandos ejecutados]

## Archivos tocados
| Archivo | Acción |
|---------|--------|
| src/components/Dashboard.tsx | Editado (3 veces) |
| src/api/routes.py | Creado |

## Último estado
[Últimos 2-3 intercambios resumidos — qué se estaba haciendo al momento del corte]

## Pendiente / Siguiente paso
[Si es inferible del contexto, qué faltaba por hacer]

## Contexto técnico
- Branch: main
- CWD: /path/to/project
- Errores recurrentes (si los hubo)
```

### `anvl report`
Reporte de todas las sesiones del proyecto:
- Total de tokens consumidos por sesión
- Waste factor promedio por sesión
- Sesiones más costosas
- Comparativa antes/después de handoffs

### `anvl install`
Registra el hook `PostToolUse` en `~/.claude/settings.json` para monitoreo pasivo. Solo agrega el hook de ANVL sin tocar los existentes.

El hook NO bloquea a Claude. Solo actualiza un estado interno que `anvl monitor` y `anvl dashboard` leen.

### `anvl uninstall`
Remueve el hook de ANVL de settings.json.

---

## Hook PostToolUse (modo pasivo)

Cuando está instalado, ANVL se ejecuta después de cada tool use de Claude Code. Su única función es:

1. Leer el JSONL de la sesión activa
2. Calcular el waste factor del último turno
3. Si supera el umbral (configurable, default 7x):
   - Escribir en stderr un mensaje que Claude Code ve:
   ```
   ⚠️ ANVL: Sesión en 8.3x waste (umbral: 7x).
   Ejecuta `anvl handoff` para generar el resumen y continúa en una sesión nueva.
   ```
4. NO bloquea, NO genera archivos automáticamente. El usuario decide.

---

## Configuración

Archivo `~/.anvl/config.json`:

```json
{
  "waste_threshold": 7,
  "dashboard_port": 3000,
  "handoff_template": "default",
  "auto_detect_project": true
}
```

---

## Generación del handoff.md — Lógica

El handoff.md es el producto clave de ANVL. Para generarlo:

1. **Título**: Tomar del registro `ai-title` del JSONL
2. **Archivos tocados**: Parsear los mensajes assistant buscando tool_use de tipo `Write`, `Edit`, `Read`, `Bash` y extraer los paths de archivos
3. **Trabajo completado**: Extraer de los mensajes del usuario y las respuestas los hitos principales (esto se puede hacer con heurísticas simples: buscar patterns como "listo", "done", "creado", "implementado")
4. **Último estado**: Tomar los últimos 2-3 pares user/assistant
5. **Branch y CWD**: Disponibles en los metadatos del JSONL (`gitBranch`, `cwd`)

**IMPORTANTE**: El handoff.md NO debe usar la API de Claude para resumir. Debe ser puramente parseado del JSONL con heurísticas. Esto lo mantiene rápido, offline, y sin costo de tokens.

---

## Dashboard Web (localhost)

Stack del dashboard:
- **Backend**: `http.server` de Python stdlib + un par de endpoints JSON
- **Frontend**: HTML single file con vanilla JS (no React, no build step)
- **Gráficos**: Chart.js via CDN o un canvas simple
- **Actualización**: Polling cada 2 segundos al backend

### Endpoints:

```
GET /api/session/current    → métricas de la sesión activa
GET /api/session/:id        → métricas de una sesión específica
GET /api/sessions           → lista de sesiones del proyecto
GET /api/history/:id        → tokens por turno para gráficos
```

### Vista del dashboard:

```
┌─────────────────────────────────────────────────┐
│  ANVL Dashboard — SouthFaceCapital              │
├─────────────────────────────────────────────────┤
│                                                  │
│  Sesión activa: cacb8dd1...                     │
│  Waste: ████████░░ 8.3x    Turnos: 47          │
│                                                  │
│  [Gráfico de línea: tokens por turno]           │
│  — cache_read (creciente)                        │
│  — output (estable)                              │
│  — cache_creation (variable)                     │
│                                                  │
│  Historial de sesiones                           │
│  ┌──────────┬────────┬───────┬──────────┐       │
│  │ Sesión   │ Turnos │ Waste │ Tokens   │       │
│  ├──────────┼────────┼───────┼──────────┤       │
│  │ cacb8dd1 │ 47     │ 8.3x  │ 1.2M    │       │
│  │ b4d49d14 │ 23     │ 3.1x  │ 340K    │       │
│  └──────────┴────────┴───────┴──────────┘       │
│                                                  │
│  [Botón: Generar Handoff]                        │
└─────────────────────────────────────────────────┘
```

---

## Plan de implementación (orden sugerido)

### Fase 1 — Core
1. `parser.py` — Leer y parsear JSONL de sesiones
2. `analyzer.py` — Calcular waste factor y métricas por turno
3. `cli.py` — Comando `anvl status` funcionando

### Fase 2 — Handoff
4. `handoff.py` — Generador de handoff.md
5. Comando `anvl handoff` funcionando

### Fase 3 — Monitor terminal
6. `monitor.py` — Display en vivo con `rich`
7. Comando `anvl monitor` funcionando

### Fase 4 — Hook
8. `hooks.py` — Instalación/desinstalación del hook PostToolUse
9. Comandos `anvl install` / `anvl uninstall`

### Fase 5 — Dashboard web
10. `web/server.py` — Servidor HTTP con API JSON
11. `web/dashboard.html` — Frontend del dashboard
12. Comando `anvl dashboard` funcionando

### Fase 6 — Reporting
13. Comando `anvl report` — Análisis histórico

---

## Reglas para Claude Code

- Todo en Python, sin TypeScript/Node
- Usar `rich` para output de terminal (tablas, colores, barras de progreso, live display)
- Sin mock data en ningún momento — siempre leer datos reales del JSONL
- El dashboard web usa vanilla HTML/JS, sin frameworks, sin build step
- Cross-platform: usar `pathlib.Path` y `os.path.expanduser` para paths
- El handoff.md se genera por parsing/heurísticas, nunca llamando a la API de Claude
- Tests con pytest para parser y analyzer
- El hook debe ser no-bloqueante y rápido (<500ms)
