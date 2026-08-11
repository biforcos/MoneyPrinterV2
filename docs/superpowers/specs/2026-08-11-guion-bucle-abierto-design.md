# Guion en bucle abierto (retención)

**Fecha:** 2026-08-11
**Estado:** aprobado

## Objetivo

Atacar el abandono a mitad de vídeo (~18 s vistos de 24-43 s): la respuesta a la pregunta que abre el vídeo debe reservarse para la última frase, manteniendo la tensión en medio. Técnica clásica de retención ("open loop").

## Diseño

Cambio solo de prompt en `generate_script` (`src/classes/YouTube.py`, tras el bloque "THE FIRST SENTENCE IS THE MOST IMPORTANT"): nuevo bloque

```
HOLD THE REVEAL: the first sentence opens a question or mystery the viewer
needs answered. The middle sentences add context, stakes and escalation
WITHOUT resolving it. Only the LAST sentence delivers the actual answer or
payoff — if the viewer leaves early, they never get it. EXAMPLE 2 above
follows exactly this shape.
```

- Coherente con el bloque existente "THE LAST SENTENCE must connect back": la revelación final responde a la apertura, así que el cierre circular se mantiene.
- Aplica a TODOS los vídeos por igual (ES/EN, corto/normal, loop/CTA): no contamina ningún A/B en marcha — mueve la línea base de todos los grupos a la vez.
- Sin cambios de código fuera del literal del prompt; el formato diálogo hereda la regla ("all the other rules still apply").

## Verificación

- `py_compile` de `src/classes/YouTube.py`.
- Inspección del primer guion del nightly de esta noche: la última frase debe contener la revelación.
- Efecto real: histórico de retención (vídeos desde el 12-ago vs anteriores).
