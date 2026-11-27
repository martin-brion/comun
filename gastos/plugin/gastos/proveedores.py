"""Alta del proveedor que falta, por el toolbelt que declara la casa. El plugin no consulta padrones ni crea contactos.

Un solo programa da de alta las razones sociales de todos los circuitos: corre desde la copia de la persona, con su
credencial, por la puerta pública del motor (broker.ejecutar_toolbelt). Esa puerta usa siempre el ERP y la credencial
globales del bot, así que el alta se ofrece solo en el destino que es ese ERP: en otro escribiría en el equivocado.

Contrato del toolbelt: --cuit <número> --pais <AR|UY> [--razon-social=<lo leído>] [--conservar-pendiente], ensayo por
defecto y --apply para escribir, que agrega el motor. La última línea de su salida es `JSON {"empresa_id", "empresa",
"creados", "avisos", "error", "pendiente_id"}`. Sale con 0 si está bien, 1 si no quedó nada, 2 por mal uso y 3 si escribió
y la relectura no coincide. Con --conservar-pendiente, si el padrón rechaza el alta la provisoria queda activa con el
prefijo PENDIENTE y su id en `pendiente_id` (rc sigue siendo 1). Repetir el alta con el mismo número la retoma.

Con ante_error=borrador (0.1.0-dev.19) un alta que no queda no frena la carga: la factura va en borrador, marcada, con la
provisoria o con el proveedor a regularizar de la configuración (proponer_borrador, resolver_borrador). Un alta se corre
una sola vez por lote y CUIT/RUT: lo que resolvió queda en `<erp>.altas.json`, junto al recibo, y un reintento lo reusa.
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


def disponible(c, ctx=None):
    """El toolbelt de alta si este destino es el ERP global del bot; si no, None. Desde una terminal (bot.terminal), None:
    ahí no corren los toolbelts."""
    if getattr(ctx, 'terminal', False):
        return None
    alta = c.get('alta_proveedor')
    if not alta:
        return None
    d = c.get('endpoint') or {}
    if d and ((d.get('secreto_login'), d.get('secreto_api_key')) != SECRETOS
              or (config.ODOO_DB and d.get('db') != config.ODOO_DB)
              or (getattr(config, 'ODOO_URL', '') and d.get('url')
                  and _url(d['url']) != _url(config.ODOO_URL))):
        return None
    return alta


def _url(valor):
    return str(valor or '').strip().rstrip('/').lower()


def _leido(texto):
    """El nombre leído, solo para cotejar con el padrón. El motor toma por una ruta un valor con «/», «\\» o «:»."""
    return re.sub(r'\s+', ' ', re.sub(r'[/\\:]+', ' ', str(texto or ''))).strip(' -')[:120]


def _argumentos(pedido, conservar=False):
    argv = ['--cuit', pedido['id_fiscal'], '--pais', pedido['pais']]
    return (argv + ([f"--razon-social={pedido['leido']}"] if pedido['leido'] else [])
            + (['--conservar-pendiente'] if conservar else []))


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


async def _correr(ctx, alta, pedido, aplicar, g, conservar=False):
    from bot import broker
    corrida = {}

    def al_correr(rc, salida):
        corrida.update(rc=rc, salida=salida)

    respuesta = await broker.ejecutar_toolbelt(ctx, alta['repo'], alta['script'], _argumentos(pedido, conservar), aplicar,
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
    alta = disponible(c, ctx)
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
    alta = disponible(c, ctx)
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


# ------------------------------------------------------------------------------------- ante_error=borrador (0.1.0-dev.19)
A_REGULARIZAR = 'va con el proveedor a regularizar'


def _limpio(texto, g, tope=200):
    """Un texto del toolbelt, corto y en una línea, si no trae nada técnico (rutas, excepciones); si trae, al registro."""
    texto = ' '.join(str(texto or '').split())[:tope]
    if texto and g._TEXTO_TECNICO.search(texto):
        g.log.warning('gastos: el alta devolvió un texto técnico: %s', texto)
        return ''
    return texto


def _detalle(res, g):
    """El motivo que dio el toolbelt, entre paréntesis; la salida cruda va al registro, no a la nota."""
    texto = _limpio((res or {}).get('error'), g)
    return f' ({texto})' if texto else ''


async def a_regularizar(ctx, c, f, g):
    """El proveedor a regularizar de la configuración, para la factura que no tiene uno: tiene que ser un contacto activo
    de la compañía o compartido. Si no, es un error de configuración y no se carga."""
    ident = c['proveedor_a_regularizar']
    if '_a_regularizar' not in c:
        filas = await g._odoo(ctx, 'res.partner', 'read', [ident], fields=['name', 'company_id', 'active'])
        if (len(filas) != 1 or filas[0].get('id') != ident or filas[0].get('active') is False
                or g._rel(filas[0].get('company_id')) not in (0, c['company_id'])):
            raise g.GastoError(f"El proveedor a regularizar {ident} de la configuración del bot (gastos.json) no es un "
                               f"contacto activo de la compañía {c['company_id']}: corregí el ID en gastos.json.")
        c['_a_regularizar'] = g.fiscal._texto(filas[0].get('name') or '', 120)
    f.update(partner_id=ident, proveedor=c['_a_regularizar'], a_regularizar=True)
    f.pop('pendiente', None)
    f.pop('proveedor_aviso', None)


def _marcar(f, motivo, clave='revision'):
    f.setdefault(clave, [])
    if motivo not in f[clave]:
        f[clave].append(motivo)


async def proponer_borrador(ctx, c, f, comercio, g):
    """La propuesta de un comprobante sin proveedor válido, con ante_error=borrador: nunca frena por los datos. Pide el alta
    (que corre con el OK) si se puede; si no, la factura va con el contacto provisorio del CUIT/RUT o con el proveedor a
    regularizar, y queda marcada con el motivo. Solo lee: el ensayo del alta no escribe."""
    cuit = f.get('cuit')
    if not cuit:
        await a_regularizar(ctx, c, f, g)
        return _marcar(f, f'No se leyó el CUIT/RUT del emisor: {A_REGULARIZAR}.')
    provisoria = await g.fiscal.pendiente(ctx, c, cuit, g)
    alta = disponible(c, ctx)
    pedido = {'id_fiscal': cuit, 'pais': c['padron_proveedores'], 'leido': _leido(comercio)}
    if provisoria and alta:
        f['alta_proveedor'] = pedido        # el alta con el OK la retoma: si el padrón ahora la valida, queda válida
        return
    if provisoria:
        f.update(partner_id=provisoria['id'], proveedor=provisoria['name'], pendiente=True)
        return _marcar(f, f'El proveedor CUIT/RUT {cuit} tiene el alta fiscal pendiente: va con su contacto provisorio.')
    if not alta:
        await a_regularizar(ctx, c, f, g)
        return _marcar(f, f'El proveedor CUIT/RUT {cuit} no está en Odoo y en este destino el bot no da altas: '
                          f'{A_REGULARIZAR}.')
    try:
        rc, salida, res = await _correr(ctx, alta, pedido, False, g)
    except g.GastoTecnico as exc:
        g.log.warning('gastos: no corrió el ensayo del alta: %s', str(exc)[:300])
        await a_regularizar(ctx, c, f, g)
        return _marcar(f, f'No pude consultar el alta del proveedor CUIT/RUT {cuit}: {A_REGULARIZAR}.')
    if rc != 0 or res is None:
        g.log.warning('gastos: el ensayo del alta terminó con %s: %s', rc, salida[-300:])
        await a_regularizar(ctx, c, f, g)
        return _marcar(f, f'El alta del proveedor CUIT/RUT {cuit} no pasó el ensayo{_detalle(res, g)}: {A_REGULARIZAR}.')
    if res.get('empresa_id'):
        await a_regularizar(ctx, c, f, g)
        return _marcar(f, f"El CUIT/RUT {cuit} ya está en Odoo como {g.fiscal._texto(str(res.get('empresa') or ''), 120)} "
                          f"(id {res['empresa_id']}), pero no sirve como proveedor de esta compañía: {A_REGULARIZAR}.")
    f['alta_proveedor'] = pedido


def _ruta_altas(lote_id, c):
    from . import estado
    p = estado.recibo_path(lote_id, c)
    return estado.seguro(p.with_name(p.name.replace('.recibo.json', '.altas.json')))


async def _sirve(ctx, c, ident, cuit, g, provisoria=False):
    """El nombre del contacto si sigue sirviendo para una factura de ese CUIT/RUT (activo, de la compañía o compartido, con
    ese número y, si es una provisoria, con su prefijo); si no, vacío."""
    filas = await g._odoo(ctx, 'res.partner', 'read', [ident], fields=['name', 'vat', 'company_id', 'active'])
    if (len(filas) != 1 or filas[0].get('id') != ident or filas[0].get('active') is False
            or g.fiscal.digitos(filas[0].get('vat')) != cuit or g._rel(filas[0].get('company_id')) not in (0, c['company_id'])
            or (provisoria and not (filas[0].get('name') or '').startswith(PENDIENTE))):
        return ''
    return g.fiscal._texto(filas[0].get('name') or '', 120) or str(ident)


async def _buscar(ctx, c, fila, g):
    """Después de un alta incierta, sin repetirla: la razón social válida del CUIT/RUT, o la provisoria, o None."""
    copia = json.loads(json.dumps(fila))
    await g.fiscal.proveedor(ctx, c, copia, g)
    if copia['partner_id']:
        return {'estado': 'validado', 'id': copia['partner_id'], 'nombre': copia['proveedor']}
    provisoria = await g.fiscal.pendiente(ctx, c, fila['cuit'], g)
    if provisoria:
        return {'estado': 'pendiente', 'id': provisoria['id'], 'nombre': provisoria['name']}
    return None


async def _correr_alta(ctx, c, alta, fila, g):
    """Corre el alta con --conservar-pendiente y dice qué quedó: {estado, id, nombre, motivo, creada}. estado es validado,
    pendiente o generico (sin id: va el proveedor a regularizar). Un alta incierta (timeout, relectura distinta, el
    toolbelt que no corre) no se repite: se busca por número."""
    cuit = fila['alta_proveedor']['id_fiscal']
    try:
        rc, salida, res = await _correr(ctx, alta, fila['alta_proveedor'], True, g, conservar=True)
    except g.GastoTecnico as exc:
        g.log.warning('gastos: no corrió el alta del CUIT/RUT %s: %s', cuit, str(exc)[:300])
        rc, salida, res = None, '', None
    ident = (res or {}).get('empresa_id')
    if rc == 0 and type(ident) is int and ident > 0:
        creada = any(isinstance(a, dict) and a.get('tipo') == 'empresa' for a in res.get('creados') or [])
        copia = json.loads(json.dumps(fila))
        await g.fiscal.proveedor(ctx, c, copia, g)
        if copia['partner_id'] and not (creada and copia['partner_id'] != ident):
            # Un aviso marca la factura también si el alta retomó un contacto que ya estaba (una provisoria).
            avisos = _limpio(' '.join(str(a) for a in res.get('avisos') or []), g, 300)
            return {'estado': 'validado', 'id': copia['partner_id'], 'nombre': copia['proveedor'], 'creada': creada,
                    'motivo': (f'El padrón dio de alta al proveedor CUIT/RUT {cuit} con un aviso: {avisos}' if avisos
                               else '')}
        rc = 'otro'         # quedó algo que no es el proveedor de la compañía: se resuelve como un alta incierta
    pendiente_id = (res or {}).get('pendiente_id')
    if rc == 1 and salida != 'timeout' and type(pendiente_id) is int and pendiente_id > 0:
        sirve = await _sirve(ctx, c, pendiente_id, cuit, g, provisoria=True)
        if sirve:
            return {'estado': 'pendiente', 'id': pendiente_id, 'nombre': sirve,
                    'motivo': (f'El padrón rechazó el alta del proveedor CUIT/RUT {cuit}{_detalle(res, g)}: va con su '
                               'contacto provisorio, marcado «alta fiscal pendiente».')}
    if rc is None:
        que = f'No pude correr el alta del proveedor CUIT/RUT {cuit}'
    elif rc == 1 and salida == 'timeout':
        que = f'El alta del proveedor CUIT/RUT {cuit} no terminó a tiempo'
    elif rc == 3:
        que = f'El alta del proveedor CUIT/RUT {cuit} escribió en Odoo pero la relectura no coincide: revisá ese contacto'
    elif rc == 1 and (res or {}).get('error'):
        que = f'El padrón rechazó el alta del proveedor CUIT/RUT {cuit}{_detalle(res, g)}'
    else:
        g.log.warning('gastos: el alta del CUIT/RUT %s terminó con %s: %s', cuit, rc, (salida or '')[-300:])
        que = f'No pude dar de alta al proveedor CUIT/RUT {cuit}{_detalle(res, g)}'
    hallado = await _buscar(ctx, c, fila, g)
    if hallado and hallado['estado'] == 'validado' and rc == 1 and salida == 'timeout':
        return {**hallado, 'motivo': ''}            # el alta llegó a Odoo: es el proveedor bueno
    if hallado:
        con = 'el proveedor que quedó en Odoo' if hallado['estado'] == 'validado' else 'su contacto provisorio'
        return {**hallado, 'motivo': f'{que}: va con {con}.'}
    return {'estado': 'generico', 'id': 0, 'nombre': '', 'motivo': f'{que}: {A_REGULARIZAR}.'}


async def resolver_borrador(ctx, c, plan, g):
    """resolver() con ante_error=borrador: un alta que no queda no frena nada. Cada CUIT/RUT pedido se da de alta una sola
    vez por lote (lo resuelto queda en <erp>.altas.json antes y después de correr el alta: un corte en el medio cuenta como
    alta incierta y no se repite). Cada factura de ese CUIT/RUT lleva el proveedor válido, la provisoria o el proveedor a
    regularizar, y el motivo en revision_carga (en memoria: lo firmado no cambia). Devuelve las altas nuevas."""
    from . import estado
    pendientes = {}
    for x in plan['comprobantes']:
        f = x.get('fiscal') or {}
        if f.get('alta_proveedor'):
            pendientes.setdefault(f['alta_proveedor']['id_fiscal'], []).append(f)
    if not pendientes:
        return []
    ruta = _ruta_altas(plan['lote_id'], c)
    registro = estado.leer(ruta) or {'version': 1, 'altas': {}}
    alta = disponible(c, ctx)
    altas = []
    locks = LOCKS.setdefault(asyncio.get_running_loop(), {})
    for cuit, filas in pendientes.items():
        g._vigencia(ctx, c)
        previo = registro['altas'].get(cuit)
        if previo and previo.get('estado') in ('validado', 'pendiente') and not await _sirve(
                ctx, c, previo.get('id'), cuit, g, provisoria=previo['estado'] == 'pendiente'):
            # Entre un intento y otro, ese contacto cambió (otro número, archivado): no se usa, y el alta no se repite.
            previo = {'estado': 'incierto'}
        if previo and previo.get('estado') in ('validado', 'pendiente', 'generico'):
            resuelto = previo                           # un reintento usa lo mismo que la primera vez
        elif previo or not alta:
            # Se cortó con el alta en curso, o ya no hay alta desde el bot: no se corre (otra vez).
            hallado = await _buscar(ctx, c, filas[0], g)
            que = (f'El contacto que usó el primer intento para el CUIT/RUT {cuit} cambió en Odoo'
                   if (previo or {}).get('estado') == 'incierto' else
                   f'El alta del proveedor CUIT/RUT {cuit} quedó a medias' if previo else
                   f'Cambió el alta de proveedores desde la propuesta y no di de alta al CUIT/RUT {cuit}')
            resuelto = ({**hallado, 'motivo': f"{que}: va con "
                         f"{'el proveedor que quedó en Odoo' if hallado['estado'] == 'validado' else 'su contacto provisorio'}."}
                        if hallado else {'estado': 'generico', 'id': 0, 'nombre': '', 'motivo': f'{que}: {A_REGULARIZAR}.'})
        else:
            registro['altas'][cuit] = {'estado': 'en_curso'}
            estado.guardar(ruta, registro)
            async with locks.setdefault(f"{c['erp']}|{c['company_id']}|{cuit}", asyncio.Lock()):
                resuelto = await _correr_alta(ctx, c, alta, filas[0], g)
            if resuelto.get('creada'):
                altas.append({'id_fiscal': cuit, 'id': resuelto['id'], 'nombre': resuelto['nombre']})
        registro['altas'][cuit] = {k: resuelto.get(k, '') for k in ('estado', 'id', 'nombre', 'motivo')}
        estado.guardar(ruta, registro)
        for f in filas:
            if resuelto['estado'] == 'generico':
                await a_regularizar(ctx, c, f, g)
            else:
                f.update(partner_id=resuelto['id'], proveedor=resuelto['nombre'])
                f.pop('a_regularizar', None)
                if resuelto['estado'] == 'pendiente':
                    f['pendiente'] = True
            if resuelto.get('motivo'):
                _marcar(f, resuelto['motivo'], 'revision_carga')
    return altas
