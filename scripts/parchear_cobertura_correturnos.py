from pathlib import Path
import re

ROOT = Path.cwd()
routes_path = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
tpl_path = ROOT / 'templates' / 'rrhh' / 'cuadrante_real.html'

if not routes_path.exists():
    raise SystemExit(f'No encuentro {routes_path}')
if not tpl_path.exists():
    raise SystemExit(f'No encuentro {tpl_path}')

routes = routes_path.read_text(encoding='utf-8')
tpl = tpl_path.read_text(encoding='utf-8')

# 1) Asegurar que la consulta de turnos trae datos efectivos del trabajador.
routes = routes.replace(
"""SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre,
               ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre""",
"""SELECT t.*, TRIM(e.nombre || ' ' || COALESCE(e.apellidos,'')) empleado_nombre,
               e.departamento_id AS empleado_departamento_id,
               e.subdepartamento_id AS empleado_subdepartamento_id,
               e.categoria_id AS empleado_categoria_id,
               COALESCE(t.departamento_id, e.departamento_id) AS departamento_efectivo_id,
               COALESCE(t.subdepartamento_id, e.subdepartamento_id) AS subdepartamento_efectivo_id,
               ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre"""
)

# 2) Asegurar que necesidades trae categoría del puesto para poder contar por categoría si el turno no tiene puesto_id.
routes = routes.replace(
"""SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre, p.nombre puesto_nombre""",
"""SELECT n.*, ct.nombre centro_nombre, d.nombre departamento_nombre, sd.nombre subdepartamento_nombre,
               p.nombre puesto_nombre, p.categoria_id AS puesto_categoria_id"""
)

# 3) Reemplazar cálculo de cobertura por uno tolerante a correturnos.
pattern = re.compile(r"\n    cobertura = \{\}\n    for n in necesidades:.*?\n    return render_template\(", re.S)
replacement = r'''
    # Cobertura real:
    # - Cuenta turnos activos, nunca anulados ni libres/descanso.
    # - Usa departamento/subdepartamento del turno; si el turno no lo trae, cae al del empleado.
    # - Si la necesidad exige puesto y el turno no tiene puesto, permite contar por categoría laboral del puesto.
    cobertura = {}
    asignados = {}
    asignados_necesidad = {}

    turnos_contables = []
    for t in turnos:
        td = dict(t)
        estado = (td.get("estado") or "planificado").strip().lower()
        tipo = (td.get("tipo") or "real").strip().lower()
        if estado in {"anulado", "cancelado", "borrado"}:
            continue
        if tipo in {"libre", "descanso", "dia_libre", "día libre"}:
            continue
        try:
            td["_weekday"] = date.fromisoformat(td["fecha"]).weekday()
        except Exception:
            continue
        td["_centro_id"] = td.get("centro_id") or 0
        td["_departamento_id"] = td.get("departamento_efectivo_id") or td.get("departamento_id") or td.get("empleado_departamento_id") or 0
        td["_subdepartamento_id"] = td.get("subdepartamento_efectivo_id") or td.get("subdepartamento_id") or td.get("empleado_subdepartamento_id") or 0
        td["_puesto_id"] = td.get("puesto_id") or 0
        td["_empleado_categoria_id"] = td.get("empleado_categoria_id") or 0
        turnos_contables.append(td)

    def _mismo(a, b):
        return str(a or 0) == str(b or 0)

    for n in necesidades:
        nd = dict(n)
        n_id = nd.get("id")
        dia = nd.get("dia_semana")
        centro = nd.get("centro_id") or 0
        depto = nd.get("departamento_id") or 0
        subdepto = nd.get("subdepartamento_id") or 0
        puesto = nd.get("puesto_id") or 0
        puesto_categoria = nd.get("puesto_categoria_id") or 0

        key = (dia, puesto, depto, subdepto)
        cobertura[key] = cobertura.get(key, 0) + (nd.get("personas_necesarias") or 0)

        total = 0
        for t in turnos_contables:
            if t["_weekday"] != dia:
                continue
            if centro and not _mismo(t["_centro_id"], centro):
                continue
            if depto and not _mismo(t["_departamento_id"], depto):
                continue
            if subdepto and not _mismo(t["_subdepartamento_id"], subdepto):
                continue

            # Si hay puesto definido, lo ideal es que coincida el puesto.
            # Si el turno no tiene puesto, aceptamos categoría equivalente para correturnos.
            if puesto:
                if _mismo(t["_puesto_id"], puesto):
                    total += 1
                elif not t["_puesto_id"] and puesto_categoria and _mismo(t["_empleado_categoria_id"], puesto_categoria):
                    total += 1
                else:
                    continue
            else:
                total += 1

        asignados_necesidad[n_id] = total
        asignados[key] = total

    return render_template('''
if not pattern.search(routes):
    raise SystemExit('No he encontrado el bloque de cobertura en routes.py. No se ha tocado nada.')
routes = pattern.sub(replacement, routes, count=1)

# 4) Pasar asignados_necesidad al template si no está.
routes = routes.replace(
"""necesidades=necesidades, cobertura=cobertura, asignados=asignados,""",
"""necesidades=necesidades, cobertura=cobertura, asignados=asignados, asignados_necesidad=asignados_necesidad,"""
)

# 5) Cambiar template para usar asignados_necesidad por ID, no por clave estricta.
tpl = tpl.replace(
"""{% set k = (n.dia_semana, n.puesto_id or 0, n.departamento_id or 0, n.subdepartamento_id or 0) %}\n      {% set cubiertos = asignados.get(k, 0) %}""",
"""{% set k = (n.dia_semana, n.puesto_id or 0, n.departamento_id or 0, n.subdepartamento_id or 0) %}\n      {% set cubiertos = asignados_necesidad.get(n.id, asignados.get(k, 0)) %}"""
)

# 6) Mejorar el formulario de asignación para que no guarde vacío el puesto/subdepartamento si vienen filtrados.
tpl = tpl.replace(
"""<select name=\"puesto_id\" class=\"form-control\" style=\"grid-column:1/3;\"><option value=\"\">Puesto</option>{% for p in puestos %}<option value=\"{{ p.id }}\" {% if puesto_id|string == p.id|string %}selected{% endif %}>{{ p.nombre }}</option>{% endfor %}</select>""",
"""<select name=\"puesto_id\" class=\"form-control\" style=\"grid-column:1/3;\"><option value=\"\">Puesto</option>{% for p in puestos %}<option value=\"{{ p.id }}\" {% if puesto_id|string == p.id|string %}selected{% endif %}>{{ p.nombre }}</option>{% endfor %}</select>"""
)

routes_path.write_text(routes, encoding='utf-8')
tpl_path.write_text(tpl, encoding='utf-8')
print('OK: cobertura de correturnos corregida. Reinicia Flask.')
