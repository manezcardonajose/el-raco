from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / 'blueprints' / 'rrhh' / 'routes.py'
TPL = ROOT / 'templates' / 'rrhh' / 'cuadrante_real.html'

if not ROUTES.exists():
    raise SystemExit(f'No encuentro {ROUTES}')
if not TPL.exists():
    raise SystemExit(f'No encuentro {TPL}')

routes = ROUTES.read_text(encoding='utf-8')
tpl = TPL.read_text(encoding='utf-8')

helper = r'''

def _redirect_cuadrante_real_con_filtros(fecha=None):
    """Vuelve al cuadrante real conservando los filtros seleccionados.
    Evita que al asignar, editar, anular, copiar o marcar libre se pierda la vista actual.
    """
    fecha_destino = (
        fecha
        or request.form.get('filtro_fecha')
        or request.form.get('fecha')
        or request.args.get('fecha')
        or date.today().isoformat()
    )
    return redirect(url_for(
        'rrhh.cuadrante_real',
        fecha=fecha_destino,
        centro_id=request.form.get('filtro_centro_id') or request.form.get('centro_id') or request.args.get('centro_id') or '',
        departamento_id=request.form.get('filtro_departamento_id') or request.form.get('departamento_id') or request.args.get('departamento_id') or '',
        subdepartamento_id=request.form.get('filtro_subdepartamento_id') or request.form.get('subdepartamento_id') or request.args.get('subdepartamento_id') or '',
        puesto_id=request.form.get('filtro_puesto_id') or request.form.get('puesto_id') or request.args.get('puesto_id') or '',
    ))
'''

if '_redirect_cuadrante_real_con_filtros' not in routes:
    marker = '_DIAS_SEMANA = ['
    pos = routes.find(marker)
    if pos == -1:
        # fallback: before cuadrante real section
        marker2 = '# CUADRANTE REAL'
        pos = routes.find(marker2)
    if pos == -1:
        raise SystemExit('No encuentro dónde insertar el helper de filtros en routes.py')
    # Insert helper just before _DIAS_SEMANA or section marker
    routes = routes[:pos] + helper + '\n' + routes[pos:]

# Reemplazos concretos de redirects que suelen perder filtros.
replacements = {
    "return redirect(url_for(\"rrhh.cuadrante_real\", fecha=fecha))": "return _redirect_cuadrante_real_con_filtros(fecha)",
    "return redirect(url_for('rrhh.cuadrante_real', fecha=fecha))": "return _redirect_cuadrante_real_con_filtros(fecha)",
    "return redirect(url_for('rrhh.cuadrante_real', fecha=request.form.get('fecha') or date.today().isoformat()))": "return _redirect_cuadrante_real_con_filtros(request.form.get('fecha'))",
    "return redirect(url_for(\"rrhh.cuadrante_real\", fecha=request.form.get(\"fecha\") or date.today().isoformat()))": "return _redirect_cuadrante_real_con_filtros(request.form.get('fecha'))",
}
for old, new in replacements.items():
    routes = routes.replace(old, new)

# Reemplaza el redirect largo de asignar_cuadrante_real si existe en una línea.
pat_asignar = re.compile(
    r"return redirect\(url_for\(\s*['\"]rrhh\.cuadrante_real['\"]\s*,\s*fecha=fecha\s*,\s*centro_id=.*?puesto_id=.*?\)\)",
    re.DOTALL,
)
routes = pat_asignar.sub("return _redirect_cuadrante_real_con_filtros(fecha)", routes)

# Reemplaza redirects multilínea que usan campos['fecha'] y ya pueden conservar filtros mediante helper.
pat_campos = re.compile(
    r"return redirect\(url_for\(\s*['\"]rrhh\.cuadrante_real['\"]\s*,\s*fecha=campos\[['\"]fecha['\"]\].*?\)\)",
    re.DOTALL,
)
routes = pat_campos.sub("return _redirect_cuadrante_real_con_filtros(campos.get('fecha'))", routes)

# Si existen rutas de libre/copiar con redirect al cuadrante antiguo, las devuelve al real con filtros.
routes = routes.replace("return redirect(url_for('rrhh.cuadrantes', fecha=fecha))", "return _redirect_cuadrante_real_con_filtros(fecha)")
routes = routes.replace('return redirect(url_for("rrhh.cuadrantes", fecha=fecha))', "return _redirect_cuadrante_real_con_filtros(fecha)")

# Inyecta hidden fields en todos los formularios POST del template mediante JS, sin depender de cada botón.
js_marker = "cuadrante-preservar-filtros-js"
js = r'''
<script id="cuadrante-preservar-filtros-js">
(function () {
  function ensureHidden(form, name, value) {
    var input = form.querySelector('input[name="' + name + '"]');
    if (!input) {
      input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      form.appendChild(input);
    }
    input.value = value || '';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var filtros = {
      filtro_fecha: '{{ inicio.isoformat() }}',
      filtro_centro_id: '{{ centro_id or "" }}',
      filtro_departamento_id: '{{ departamento_id or "" }}',
      filtro_subdepartamento_id: '{{ subdepartamento_id or "" }}',
      filtro_puesto_id: '{{ puesto_id or "" }}'
    };

    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
      Object.keys(filtros).forEach(function (key) {
        ensureHidden(form, key, filtros[key]);
      });
    });
  });
})();
</script>
'''
if js_marker not in tpl:
    if '{% endblock %}' in tpl:
        tpl = tpl.replace('{% endblock %}', js + '\n{% endblock %}', 1)
    else:
        tpl += js

# También añade un bloque visible de ayuda para confirmar que el parche está aplicado.
if 'Filtros activos conservados' not in tpl:
    tpl = tpl.replace(
        'Esta pantalla no impone horarios. Cada departamento define su cobertura y asigna horarios reales según su establecimiento.',
        'Esta pantalla no impone horarios. Cada departamento define su cobertura y asigna horarios reales según su establecimiento.<br><small><i class="fas fa-filter"></i> Filtros activos conservados al guardar, copiar, anular o marcar libre.</small>'
    )

ROUTES.write_text(routes, encoding='utf-8')
TPL.write_text(tpl, encoding='utf-8')
print('OK: cuadrante real conserva filtros en asignar, editar, anular, copiar y marcar libre.')
print('Archivos tocados:')
print(' -', ROUTES)
print(' -', TPL)
