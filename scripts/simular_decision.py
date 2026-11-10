"""Ejecuta el simulador estrategico sin arrancar la ERP.

Uso:
    python scripts/simular_decision.py
"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blueprints.asistente.decision_core import calcular_escenario_decision  # noqa: E402


def main():
    escenario = {
        "nombre": "Demo standalone restaurante",
        "tipo": "restaurante",
        "decision_tipo": "oferta",
        "fuente_datos": "simulado",
        "habitaciones": 80,
        "ocupacion_pct": 70,
        "adr": 28,
        "dias": 30,
        "pax_medio": 1.4,
        "coste_variable_pax_dia": 9,
        "coste_fijo_mes": 16000,
        "coste_personal_mes": 32000,
        "coste_personal_origen": "manual",
        "comision_pct": 6,
        "inversion": 4000,
        "satisfaccion_objetivo": 88,
        "rotacion_riesgo_pct": 12,
        "empleados_no_presentan": 1,
        "retrasos_pct": 4,
        "cambios_equipo": 2,
        "aceptacion_sistema_pct": 86,
        "cobertura_ops_pct": 78,
    }
    print(json.dumps(calcular_escenario_decision(escenario), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
