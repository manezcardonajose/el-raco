"""Inicializacion explicita de los esquemas que antes se creaban al abrir pantallas."""

from __future__ import annotations


def inicializar_esquemas_modulos(conn) -> list[str]:
    from blueprints.animacion.routes import _ensure_animacion_schema
    from blueprints.appcc.routes import ensure_alertas_caducidad_schema, ensure_appcc_plan_schema
    from blueprints.asistente.routes import _ensure_asistente_schema, _ensure_ia_reportes_schema
    from blueprints.auth.routes import _ensure_auth_schema
    from blueprints.buffet_ia.routes import ensure_buffet_ia_schema
    from blueprints.contabilidad.routes import _ensure_schema as ensure_contabilidad
    from blueprints.compras.routes import _asegurar_estructura_compras
    from blueprints.produccion.routes import _asegurar_planificacion_schema
    from blueprints.departamentos.routes import _ensure_departamentos_schema
    from blueprints.experiencia.routes import _ensure_experiencia_schema
    from blueprints.inventario.routes import _ensure_schema as ensure_inventario
    from blueprints.mantenimiento.routes import _ensure_mantenimiento_schema
    from blueprints.menus.routes import _ensure_menu_centros_schema
    from blueprints.pisos.routes import (
        _ensure_appcc_limpieza_schema,
        _ensure_incidencias_min_schema,
        _ensure_mantenimiento_min_schema,
        _ensure_pisos_schema,
    )
    from blueprints.puntos_venta.routes import _ensure_puntos_venta_centros_schema
    from blueprints.recepcion.routes import _ensure_recepcion_schema
    from blueprints.rrhh.routes import (
        _ensure_ausencias_extra_schema,
        _ensure_control_tiempo_schema,
        _ensure_cuadrantes_schema,
        _ensure_editable_schema,
        _ensure_prenomina_schema,
        _ensure_seleccion_schema,
        ensure_rrhh_schema,
    )
    from blueprints.tpv.routes import _ensure_tpv_schema
    from helpers.centro_ia_helper import ensure_centro_ia_schema
    from helpers.etiquetas_helper import ensure_etiquetas_schema
    from helpers.impresion_helper import ensure_print_queue_schema
    from helpers.operativa_helper import (
        ensure_equipos_unificados_schema,
        ensure_incidencias_context_schema,
        ensure_mantenimiento_context_schema,
    )

    tareas = [
        ("auth", _ensure_auth_schema),
        ("recepcion", _ensure_recepcion_schema),
        ("compras", lambda db: _asegurar_estructura_compras(db.cursor())),
        ("produccion", _asegurar_planificacion_schema),
        ("rrhh", ensure_rrhh_schema),
        ("rrhh_seleccion", _ensure_seleccion_schema),
        ("rrhh_prenomina", _ensure_prenomina_schema),
        ("rrhh_cuadrantes", _ensure_cuadrantes_schema),
        ("rrhh_editable", _ensure_editable_schema),
        ("rrhh_tiempo", _ensure_control_tiempo_schema),
        ("rrhh_ausencias", _ensure_ausencias_extra_schema),
        ("appcc", ensure_appcc_plan_schema),
        ("appcc_alertas", ensure_alertas_caducidad_schema),
        ("contabilidad", ensure_contabilidad),
        ("tpv", _ensure_tpv_schema),
        ("pisos", _ensure_pisos_schema),
        ("pisos_appcc", _ensure_appcc_limpieza_schema),
        ("pisos_incidencias", _ensure_incidencias_min_schema),
        ("pisos_mantenimiento", _ensure_mantenimiento_min_schema),
        ("mantenimiento", _ensure_mantenimiento_schema),
        ("inventario", ensure_inventario),
        ("departamentos", _ensure_departamentos_schema),
        ("puntos_venta", _ensure_puntos_venta_centros_schema),
        ("menus", _ensure_menu_centros_schema),
        ("experiencia", _ensure_experiencia_schema),
        ("animacion", _ensure_animacion_schema),
        ("asistente", _ensure_asistente_schema),
        ("ia_reportes", _ensure_ia_reportes_schema),
        ("buffet_ia", ensure_buffet_ia_schema),
        ("centro_ia", ensure_centro_ia_schema),
        ("equipos", ensure_equipos_unificados_schema),
        ("incidencias", ensure_incidencias_context_schema),
        ("mantenimiento_contexto", ensure_mantenimiento_context_schema),
        ("impresion", ensure_print_queue_schema),
        ("etiquetas", ensure_etiquetas_schema),
    ]
    completadas = []
    for nombre, funcion in tareas:
        funcion(conn)
        completadas.append(nombre)
    conn.commit()
    return completadas
