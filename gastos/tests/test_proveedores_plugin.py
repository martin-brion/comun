"""El alta del proveedor por el toolbelt de la casa, sin ERP: lo que el plugin le pide y cómo lee lo que devuelve."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('proveedores_test', Path(os.environ['GASTOS_PLUGIN_DIR']) / 'gastos/proveedores.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

ALTA = {'repo': 'herramientas', 'script': 'contactos/alta.py'}
GLOBAL = {'db': 'casa', 'secreto_login': 'ODOO_LOGIN', 'secreto_api_key': 'ODOO_API_KEY'}


class Error(Exception):
    pass


def salida(**res):
    return 'línea legible\nJSON ' + json.dumps(res, ensure_ascii=False)


class Tecnico(Error):
    def __init__(self, detalle, publico=''):
        super().__init__(detalle)
        self.publico = publico


class DisponibleTests(unittest.TestCase):
    def test_sin_declarar_no_hay_alta(self):
        self.assertIsNone(p.disponible({'endpoint': GLOBAL}))

    def test_solo_en_el_erp_global_del_bot(self):
        with patch.object(p.config, 'ODOO_DB', 'casa'):
            self.assertEqual(p.disponible({'alta_proveedor': ALTA, 'endpoint': GLOBAL}), ALTA)
            self.assertEqual(p.disponible({'alta_proveedor': ALTA, 'endpoint': None}), ALTA)
            for ajeno in ({**GLOBAL, 'db': 'otra'}, {**GLOBAL, 'secreto_login': 'ENSAYO_LOGIN'},
                          {**GLOBAL, 'secreto_api_key': 'ENSAYO_KEY'}):
                self.assertIsNone(p.disponible({'alta_proveedor': ALTA, 'endpoint': ajeno}), ajeno)


class SalidaTests(unittest.TestCase):
    def test_lee_la_ultima_linea_json(self):
        self.assertEqual(p._resultado('JSON {"a": 1}\nJSON {"a": 2}\nCarpeta de trabajo: x'), {'a': 2})

    def test_sin_json_valido_es_none(self):
        for texto in ('', 'nada', 'JSON no es json', 'JSON [1]'):
            self.assertIsNone(p._resultado(texto), texto)

    def test_el_nombre_leido_no_parece_una_ruta(self):
        from bot import broker
        leido = p._leido(r'- Pérez S/A: Hijos\Cía  ')
        self.assertEqual(leido, 'Pérez S A Hijos Cía')
        self.assertFalse(broker._es_ruta(leido))

    def test_sin_nombre_leido_no_lleva_el_flag(self):
        self.assertEqual(p._argumentos({'id_fiscal': '1', 'pais': 'AR', 'leido': ''}), ['--cuit', '1', '--pais', 'AR'])

    def test_motivos(self):
        self.assertIn('no sé si llegó', p._falla(1, 'timeout', None, '1'))
        self.assertIn('No la repitas', p._falla(3, salida(), {}, '1'))
        self.assertIn('no la validó', p._falla(1, salida(), {'error': 'el padrón no la validó'}, '1'))
        self.assertIn('Traceback final', p._falla(1, 'x\nTraceback final\n', None, '1'))
        self.assertIn('código 7', p._falla(7, '', None, '1'))


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    def armar(self, *cuits):
        comprobantes = [{'fiscal': {'cuit': v, 'partner_id': 0, 'proveedor': '',
                                    'alta_proveedor': {'id_fiscal': v, 'pais': 'AR', 'leido': 'Leído'}}} for v in cuits]
        return {'comprobantes': comprobantes + [{'fiscal': None}, {}]}

    def setUp(self):
        self.c = {'alta_proveedor': ALTA, 'endpoint': None, 'erp': ['a'], 'company_id': 1, 'padron_proveedores': 'AR'}
        self.corridas = []
        self.responde = {}

        async def proveedor(ctx, c, f, g):
            f.update(partner_id=self.responde.get('partner', 50), proveedor='Razón del Padrón')
        self.g = NS(GastoError=Error, GastoTecnico=Tecnico, fiscal=NS(proveedor=AsyncMock(side_effect=proveedor)))

    def puerta(self, rc=0, **res):
        res = {'empresa_id': 50, 'empresa': 'Razón del Padrón', 'creados': [{'tipo': 'empresa', 'id': 50}], 'avisos': [],
               'error': None, **res}

        async def correr(ctx, repo, rel, argv, aplicar, al_correr=None):
            self.corridas.append((repo, rel, argv, aplicar))
            al_correr(rc, salida(**res))
            return {'content': [{'type': 'text', 'text': ''}]}
        return patch('bot.broker.ejecutar_toolbelt', side_effect=correr)

    async def test_un_alta_por_cuit_y_el_proveedor_en_todas_sus_facturas(self):
        plan = self.armar('11', '11', '22')
        with self.puerta():
            altas = await p.resolver(NS(), self.c, plan, self.g)
        self.assertEqual([c[2][1] for c in self.corridas], ['11', '22'])
        self.assertTrue(all(c[3] is True for c in self.corridas))
        self.assertEqual([a['id_fiscal'] for a in altas], ['11', '22'])
        for x in plan['comprobantes'][:3]:
            self.assertEqual((x['fiscal']['partner_id'], x['fiscal']['proveedor']), (50, 'Razón del Padrón'))

    async def test_sin_altas_pedidas_no_corre_nada(self):
        with self.puerta():
            self.assertEqual(await p.resolver(NS(), {}, {'comprobantes': [{'fiscal': {}}, {}]}, self.g), [])
        self.assertEqual(self.corridas, [])

    async def test_ya_existia_no_es_alta_nueva_ni_frena_por_avisos(self):
        plan = self.armar('11')
        with self.puerta(creados=[], avisos=['Hay duplicados']):
            self.assertEqual(await p.resolver(NS(), self.c, plan, self.g), [])
        self.assertEqual(plan['comprobantes'][0]['fiscal']['partner_id'], 50)

    async def test_aviso_de_un_alta_nueva_frena_despues_de_todas(self):
        with self.puerta(avisos=['No se parece']), self.assertRaisesRegex(Error, 'No se parece'):
            await p.resolver(NS(), self.c, self.armar('11', '22'), self.g)
        self.assertEqual(len(self.corridas), 2)

    async def test_lo_creado_tiene_que_ser_el_proveedor_de_la_compania(self):
        self.responde['partner'] = 99
        with self.puerta(), self.assertRaisesRegex(Error, 'no como proveedor'):
            await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.responde['partner'] = 0
        with self.puerta(creados=[]), self.assertRaisesRegex(Error, 'no como proveedor'):
            await p.resolver(NS(), self.c, self.armar('11'), self.g)

    async def test_cualquier_falla_frena_la_carga(self):
        for rc, res in ((1, {'empresa_id': None, 'error': 'no validó'}), (3, {}), (0, {'empresa_id': None}),
                        (0, {'empresa_id': '50'}), (2, {})):
            with self.puerta(rc, **res), self.assertRaisesRegex(Error, 'No cargué ninguna factura'):
                await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.g.fiscal.proveedor.assert_not_awaited()

    async def test_la_salida_cruda_del_toolbelt_no_llega_a_la_persona(self):
        with self.puerta(1, empresa_id=None, error=None), self.assertRaises(Tecnico) as caso:
            await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.assertIn('No pude dar de alta el CUIT/RUT 11.', caso.exception.publico)
        self.assertIn('No cargué ninguna factura', caso.exception.publico)
        with self.puerta(1, empresa_id=None, error='no validó'), self.assertRaisesRegex(Error, 'no validó') as caso:
            await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.assertNotIsInstance(caso.exception, Tecnico)

    async def test_si_el_toolbelt_no_corre_su_respuesta_no_llega_a_la_persona(self):
        async def no_corre(ctx, repo, rel, argv, aplicar, al_correr=None):
            return {'content': [{'type': 'text', 'text': 'Revisalos con kb_status: contactos/alta.py'}]}
        with patch('bot.broker.ejecutar_toolbelt', side_effect=no_corre), self.assertRaises(Tecnico) as caso:
            await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.assertIn('kb_status', str(caso.exception))
        self.assertNotIn('kb_status', caso.exception.publico)
        self.assertIn('No pude correr el alta del proveedor.', caso.exception.publico)

    async def test_sin_el_toolbelt_declarado_no_se_carga_una_propuesta_que_lo_pedia(self):
        self.c.pop('alta_proveedor')
        with self.puerta(), self.assertRaisesRegex(Error, 'prepará otra'):
            await p.resolver(NS(), self.c, self.armar('11'), self.g)
        self.assertEqual(self.corridas, [])
