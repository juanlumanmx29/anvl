# ANVL

**Monitor de sesiones y herramienta de handoff para Claude Code.**

Developed by **IronDevz**

---

## El problema

Claude Code envía todo el historial de la conversación en cada turno. En el turno 1 envías ~5K tokens, pero en el turno 50 estás enviando ~170K tokens para recibir 500 de respuesta. Esto quema tu cuota exponencialmente.

**ANVL** detecta esta inflación en tiempo real y te ayuda a hacer un corte limpio: genera un resumen de lo trabajado y lo pendiente, para que puedas continuar en una sesión nueva sin perder contexto. Esto te ahorra entre 40-60% de tu cuota diaria.

## Cómo funciona

```
Sesión inflada (170K tokens/turno)
         │
    ANVL detecta waste > 7x
         │
    Genera handoff.md automáticamente
         │
    Abres nueva sesión de Claude Code
         │
    "Lee handoff.md y continúa donde quedé"
         │
Sesión fresca (5K tokens/turno) ✓
```

El **waste factor** es el ratio entre tokens de entrada y tokens de salida. Un valor alto significa que Claude está leyendo mucho contexto para generar poca respuesta:

| Waste Factor | Estado | Acción |
|:---:|:---:|:---|
| < 3x | Verde | Sesión saludable |
| 3-7x | Amarillo | Empezando a inflarse |
| > 7x | Rojo | Haz handoff ahora |

---

## Instalación

### Opción 1: Desde PyPI (recomendado)

```bash
pip install anvl
```

### Opción 2: Desde el código fuente

```bash
git clone https://github.com/jumontes/anvl.git
cd anvl
pip install -e .
```

### Requisitos

- Python 3.11 o superior
- La única dependencia es [rich](https://github.com/Textualize/rich) (se instala automáticamente)

---

## Setup inicial

Después de instalar, corre esto una sola vez:

```bash
anvl init
```

Esto hace dos cosas:
1. Crea el archivo de configuración en `~/.anvl/config.json`
2. Instala un hook en Claude Code que te avisa cuando una sesión se infla

Listo. ANVL ahora te alertará automáticamente cuando necesites rotar la sesión.

---

## Uso

### Ver el estado de tu sesión actual

```bash
anvl status
```

Muestra waste factor, tokens usados, tendencia, y un semáforo verde/amarillo/rojo.

### Ver todas tus sesiones

```bash
anvl sessions              # Últimas 20 sesiones
anvl sessions --active     # Solo las que están corriendo
anvl sessions --today      # Solo las de hoy
anvl sessions --all        # Todas sin límite
```

### Monitor en tiempo real

```bash
anvl monitor               # Se refresca cada 2 segundos
anvl monitor --interval 5  # Cada 5 segundos
```

Panel live en la terminal con gauge de waste y tabla de tokens por turno.

### Dashboard web

```bash
anvl dashboard             # Abre http://localhost:3000
anvl dashboard --port 8080 # Puerto personalizado
```

Dashboard con tema oscuro, gráficos interactivos, y overview global de todas tus sesiones. Incluye:
- Barra de uso de cuota con timer de reset
- Gráfico de tokens por turno (cache read, cache creation, output)
- Gráfico de tendencia del waste factor
- Generación de handoff con un click

### Generar handoff manualmente

```bash
anvl handoff               # Genera handoff.md en el directorio del proyecto
anvl handoff -o ruta.md    # Ruta personalizada
```

El archivo generado contiene:
- Resumen de la sesión y trabajo completado
- Archivos tocados y acciones realizadas
- Últimos 3 turnos resumidos
- Trabajo pendiente detectado automáticamente
- Contexto técnico (rama, tokens, timestamps)

### Reporte multi-sesión

```bash
anvl report                # Tabla comparativa de todas las sesiones del proyecto
```

---

## Alertas automáticas

Si corriste `anvl init`, el hook ya está instalado. Cuando el waste factor sube, verás alertas directamente en Claude Code:

```
🟡 ANVL: Session inflating (8.5x waste, cache: 95,000 tokens)
   Consider running `anvl handoff` soon.
```

```
🔴 ANVL: Conversation critically inflated (45x waste)
   Context is 150,000 tokens and growing.
   Run: anvl handoff
```

Cuando llega a niveles catastróficos (>100x), ANVL genera el handoff automáticamente y te dice exactamente cómo continuar:

```
============================================================
🚨 ANVL: Session critically inflated (168x waste)

💾 Handoff saved: handoff.md

👉 To continue:
   1. Open a new Claude Code conversation
   2. Say: Read handoff.md and continue where I left off

   This saves ~40-60% of your quota per session.
============================================================
```

---

## Configuración

Archivo: `~/.anvl/config.json`

```json
{
  "waste_threshold": 7,
  "dashboard_port": 3000,
  "window_hours": 5,
  "weighted_quota_limit": 105000000,
  "handoff_waste_threshold": 100
}
```

| Campo | Qué hace | Default |
|-------|----------|---------|
| `waste_threshold` | Umbral para las alertas del hook | 7 |
| `dashboard_port` | Puerto del dashboard web | 3000 |
| `window_hours` | Tamaño de la ventana rolling (horas) | 5 |
| `weighted_quota_limit` | Presupuesto de tokens ponderado | 105M |
| `handoff_waste_threshold` | Umbral para auto-handoff | 100 |

---

## Referencia rápida de comandos

| Comando | Qué hace |
|---------|----------|
| `anvl init` | Setup inicial (config + hook) |
| `anvl status` | Estado de la sesión actual |
| `anvl sessions` | Todas las sesiones con stats |
| `anvl monitor` | Monitor live en terminal |
| `anvl dashboard` | Dashboard web con gráficos |
| `anvl report` | Reporte comparativo multi-sesión |
| `anvl handoff` | Generar resumen para rotación |
| `anvl install` | Instalar hook en Claude Code |
| `anvl uninstall` | Remover hook |

---

## Cómo contribuir

1. Fork el repo
2. Crea una rama (`git checkout -b mi-feature`)
3. Corre los tests (`python -m pytest tests/ -v`)
4. Haz commit y push
5. Abre un Pull Request

---

## License

MIT — ver [LICENSE](LICENSE)

---

Developed by **IronDevz**
