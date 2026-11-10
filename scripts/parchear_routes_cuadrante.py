from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
routes_path = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
init_path = ROOT / 'blueprints' / 'rrhh' / '__init__.py'

if routes_path.exists():
    s = routes_path.read_text(encoding='utf-8')
    original = s
    s = s.replace(
        'turnos_where = ["t.empresa_id = ?", "date(t.fecha) BETWEEN date(?) AND date(?)"]',
        'turnos_where = ["t.empresa_id = ?", "date(t.fecha) BETWEEN date(?) AND date(?)", "COALESCE(t.estado, \\\'planificado\\\') != \\\'anulado\\\'"]',
    )
    s = s.replace(
        'AND date(t.fecha) BETWEEN date(?) AND date(?)\n          {filtro_depto}',
        "AND date(t.fecha) BETWEEN date(?) AND date(?)\n          AND COALESCE(t.estado, 'planificado') != 'anulado'\n          {filtro_depto}",
    )
    # Evita contar turnos anulados en cobertura si el bloque existe
    s = s.replace(
        'for t in turnos:\n        key = (date.fromisoformat(t["fecha"]).weekday(), t["puesto_id"] or 0, t["departamento_id"] or 0, t["subdepartamento_id"] or 0)\n        asignados[key] = asignados.get(key, 0) + 1',
        'for t in turnos:\n        if (t["estado"] if "estado" in t.keys() else "planificado") == "anulado":\n            continue\n        key = (date.fromisoformat(t["fecha"]).weekday(), t["puesto_id"] or 0, t["departamento_id"] or 0, t["subdepartamento_id"] or 0)\n        asignados[key] = asignados.get(key, 0) + 1',
    )
    if s != original:
        routes_path.write_text(s, encoding='utf-8')
        print('routes.py parcheado: turnos anulados filtrados.')
    else:
        print('routes.py no necesitó cambios o el patrón no coincidió.') 
else:
    print('No se encontró blueprints/rrhh/routes.py')

if init_path.exists():
    s = init_path.read_text(encoding='utf-8')
    if 'cuadrante_extras' not in s:
        s = s.rstrip() + '\nfrom . import cuadrante_extras\n'
        init_path.write_text(s, encoding='utf-8')
        print('__init__.py actualizado con cuadrante_extras.')
    else:
        print('__init__.py ya importa cuadrante_extras.')
else:
    print('No se encontró blueprints/rrhh/__init__.py')
