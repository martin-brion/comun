"""Alta del proveedor que falta, por el toolbelt que declara la casa. El plugin no consulta padrones ni crea contactos.

Un solo programa da de alta las razones sociales de todos los circuitos: corre desde la copia de la persona, con su
credencial, por la puerta pública del motor (broker.ejecutar_toolbelt). Esa puerta usa siempre el ERP y la credencial
globales del bot, así que el alta se ofrece solo en el destino que es ese ERP: en otro escribiría en el equivocado.

Contrato del toolbelt: --cuit <número> --pais <AR|UY> [--razon-social=<lo leído>], ensayo por defecto y --apply para
escribir, que agrega el motor. La última línea de su salida es `JSON {"empresa_id", "empresa", "creados", "avisos",
"error"}`. Sale con 0 si está bien, 1 si no quedó nada, 2 por mal uso y 3 si escribió y la relectura no coincide.
"""
import asyncio
import json
import re
from weakref import WeakKeyDictionary

from bot import config

LOCKS = WeakKeyDictionary()
# Un contacto sin validar: no es un proveedor, aunque tenga el CUIT/RUT.
PENDIENTE = '[Alta fiscal pendiente] '
# Los secretos con que corre la puerta del motor.
SECRETOS = ('ODOO_LOGIN', 'ODOO_API_KEY')
NO_FACTURA = 'No se crea ninguna factura sin proveedor.'


def disponible(c):
    """El toolbelt de alta si este destino es el ERP global del bot; si no, None."""
    alta = c.get('alta_proveedor')
    if not alta:
        return None
    d = c.get('endpoint') or {}
    if d and ((d.get('secreto_login'), d.get('secreto_api_key')) != SECRETOS
              or (config.ODOO_DB and d.get('db') != config.ODOO_DB)):
        return None
    return alta


def _leido(texto):
    """El nombre leído, solo para cotejar con el padrón. El motor toma por una ruta un valor con «/», «\\» o «:»."""
    return re.sub(r'\s+', ' ', re.sub(r'[/\\:]+', ' ', str(texto or ''))).strip(' -')[:120]


def _argumentos(pedido):
    argv = ['--cuit', pedido['id_fiscal'], '--pais', pedido['pais']]
    return argv + ([f"--razon-social={pedido['leido']}"] if pedido['leido'] else [])


def _resultado(salida):
    """El dict de la línea JSON de la salida, o None."""
    for linea in reversed(salida.splitlines()):
        if linea.startswith('JSON '):
            try:
                valor = json.loads(linea[5:])
            except ValueError:
                return None
            return valor if isinstance(valor, dict) else None
    return None


async def _correr(ctx, alta, pedido, aplicar, g):
    from bot import broker
    corrida = {}

    def al_correr(rc, salida):
        corrida.update(rc=rc, salida=salida)

    respuesta = await broker.ejecutar_toolbelt(ctx, alta['repo'], alta['script'], _argumentos(pedido), aplicar,
                                               al_correr=al_correr)
    if 'rc' not in corrida:
        raise g.GastoTecnico(f"No pude correr el alta del proveedor: {broker.texto_de(respuesta)[:300]} {NO_FACTURA}",
                             publico=f"No pude correr el alta del proveedor. {NO_FACTURA}")
    return corrida['rc'], corrida['salida'], _resultado(corrida['salida'])


def _falla(rc, salida, res, cuit):
    if rc == 1 and salida == 'timeout':
        return (f"El alta del CUIT/RUT {cuit} no terminó a tiempo y no sé si llegó a Odoo. "
                'Revisá ese CUIT/RUT en Odoo antes de reintentar.')
    if rc == 3:
        return (f"El alta del CUIT/RUT {cuit} escribió en Odoo, pero la relectura no coincide. "
                'No la repitas: revisá el registro.')
    lineas = [x.strip() for x in salida.splitlines() if x.strip() and not x.startswith('JSON ')]
    motivo = (res or {}).get('error') or (lineas[-1] if lineas else f'terminó con el código {rc}')
    return f"No pude dar de alta el CUIT/RUT {cuit}: {str(motivo)[:300]}"


async def proponer(ctx, c, f, comercio, g):
    """La propuesta de un comprobante sin proveedor: ensaya el alta, sin escribir, y la deja pedida para el OK de la carga.
    Frena si ya se sabe que no va a poder."""
    cuit = f.get('cuit')
    if not cuit:
        raise g.GastoError(f"No leí el CUIT/RUT del proveedor: corregí la lectura o excluí la foto. {NO_FACTURA}")
    alta = disponible(c)
    if not alta:
        raise g.GastoError(f"Falta el proveedor CUIT/RUT {cuit} y en este destino no hay alta desde el bot: se da de alta "
                           f"en Odoo y se vuelve a preparar este lote. {NO_FACTURA}")
    pedido = {'id_fiscal': cuit, 'pais': c['padron_proveedores'], 'leido': _leido(comercio)}
    ensayados = c.setdefault('_altas_ensayadas', [])
    if cuit not in ensayados:
        rc, salida, res = await _correr(ctx, alta, pedido, False, g)
        if rc != 0 or res is None:
            raise g.GastoError(f"{_falla(rc, salida, res, cuit)} {NO_FACTURA}")
        if res.get('empresa_id'):
            raise g.GastoError(f"El CUIT/RUT {cuit} ya está en Odoo como {res.get('empresa')} (id {res['empresa_id']}), "
                               f"pero no se puede usar como proveedor de esta compañía: revisalo. {NO_FACTURA}")
        ensayados.append(cuit)
    f['alta_proveedor'] = pedido


async def resolver(ctx, c, plan, g):
    """Antes de la primera factura, con el OK de la carga: da de alta cada proveedor pedido y deja su identidad en el plan
    en memoria (el archivo aprobado no cambia). Devuelve las altas nuevas. Si algo falla, o el padrón deja un aviso, no
    sigue: se puede preparar de nuevo, y un alta repetida encuentra la anterior por CUIT/RUT."""
    pendientes = {}
    for x in plan['comprobantes']:
        f = x.get('fiscal') or {}
        if f.get('alta_proveedor'):
            pendientes.setdefault(f['alta_proveedor']['id_fiscal'], []).append(f)
    if not pendientes:
        return []
    alta = disponible(c)
    if not alta:
        raise g.GastoError('Cambió el alta de proveedores desde la propuesta; prepará otra.')
    altas, avisos = [], []
    locks = LOCKS.setdefault(asyncio.get_running_loop(), {})
    for cuit, filas in pendientes.items():
        async with locks.setdefault(f"{c['erp']}|{c['company_id']}|{cuit}", asyncio.Lock()):
            rc, salida, res = await _correr(ctx, alta, filas[0]['alta_proveedor'], True, g)
        ident = (res or {}).get('empresa_id')
        if rc != 0 or type(ident) is not int or ident <= 0:
            pie = 'No cargué ninguna factura. Para cargar las demás, excluí esa foto y prepará de nuevo.'
            detalle = f"{_falla(rc, salida, res, cuit)} {pie}"
            if (res or {}).get('error') or rc == 3 or (rc == 1 and salida == 'timeout'):
                raise g.GastoError(detalle)
            raise g.GastoTecnico(detalle, publico=f"No pude dar de alta el CUIT/RUT {cuit}. {pie}")    # salida cruda del toolbelt
        creada = any(isinstance(a, dict) and a.get('tipo') == 'empresa' for a in res.get('creados') or [])
        copia = json.loads(json.dumps(filas[0]))
        await g.fiscal.proveedor(ctx, c, copia, g)
        if not copia['partner_id'] or (creada and copia['partner_id'] != ident):
            raise g.GastoError(f"El CUIT/RUT {cuit} quedó en Odoo (id {ident}), pero no como proveedor de esta compañía: "
                               'revisalo. No cargué ninguna factura.')
        for f in filas:
            f.update(partner_id=copia['partner_id'], proveedor=copia['proveedor'])
            if copia.get('proveedor_aviso'):
                f['proveedor_aviso'] = copia['proveedor_aviso']
        if creada:
            altas.append({'id_fiscal': cuit, 'id': ident, 'nombre': copia['proveedor']})
            avisos += [str(a) for a in res.get('avisos') or []]
    if avisos:
        raise g.GastoError('Di de alta al proveedor, pero no cargué ninguna factura: ' + ' '.join(avisos)
                           + ' Prepará de nuevo el lote: la propuesta va a mostrar la razón social del padrón.')
    return altas
