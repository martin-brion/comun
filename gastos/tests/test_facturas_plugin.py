"""Facturas sintéticas independientes de la evaluación de rendiciones."""
import copy
import time
import json
from bot import config, odoo
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from tests import test_empresas_plugin as empresas
from tests import test_gastos_plugin as banco
from bot import plugins

gastos, estado = empresas.gastos, empresas.estado


class FacturasTests(banco._BancoOdoo):
    escribir = empresas.EmpresasTests.escribir
    args = empresas.EmpresasTests.args
    preparar = empresas.EmpresasTests.preparar
    aprobar = empresas.EmpresasTests.aprobar
    seleccion = empresas.EmpresasTests.seleccion
    sin_escrituras = empresas.EmpresasTests.sin_escrituras
    politica = empresas.EmpresasTests.politica

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for i in (0, 1, 2, 3): self.patches[i].stop()
        self.raiz = empresas.configuracion()
        self.escribir()
        self.user.odoo_uid = 999
        self.user.puede = lambda servicio: servicio == "odoo"
        self.user.odoo_ids_extra = {
            "usuarios_erp": {"ensayo": 17, "principal": 27},
            "empleados_gastos": {"taller": 81, "oficina": 91},
            "empleados_gastos_destinos": {"taller": {"ensayo": 181}, "oficina": {"ensayo": 191}}}
        self.secretos = {"ENSAYO_LOGIN": "persona_prueba", "ENSAYO_KEY": "falso_ensayo",
                         "PRINCIPAL_LOGIN": "persona_prueba", "PRINCIPAL_KEY": "falso_principal"}
        self.ctx.secreto = self.secretos.get
        self.lote["creado"] = time.time() - 40 * 86400
        self.lotes = {self.lote["lote_id"]: self.lote}
        self.rows[0]["categoria"] = self.rows[1]["categoria"] = "Traslados"
        self.erps = {}
        for alias, off, uid in (("ensayo", 100, 17), ("principal", 0, 27)):
            base_id = 401 if alias == "ensayo" else 501
            self.erps[alias] = {
                "uid": uid, "off": off,
                "product.product": {
                    base_id: {"id": base_id, "name": "Duplicado", "company_id": False,
                              "active": True, "can_be_expensed": True, "default_code": False},
                    base_id + 1: {"id": base_id + 1, "name": "Duplicado", "company_id": False,
                                  "active": True, "can_be_expensed": True, "default_code": "GASTO_MAT"}},
                "hr.employee": {
                    ident + off: {"id": ident + off, "name": "Persona de Prueba", "active": True,
                                  "company_id": [company + (10 if off else 0), "Casa"], "user_id": [uid, "Persona"]}
                    for ident, company in ((81, 3), (91, 7))},
                "account.journal": {
                    ident + off: {"id": ident + off, "name": "Compras", "company_id": [company + (10 if off else 0), "Casa"], "type": "purchase",
                                  "active": True}
                    for ident, company in ((61, 3), (71, 7))},
                "hr.expense": {}, "hr.expense.sheet": {}, "ir.attachment": {}}
        self.rpc_llamadas = []
        self.fallar_adjunto = False
        for p in (patch("bot.lotes.lote_ver", side_effect=lambda ident: self.lotes[ident]),
                  patch.object(odoo, "llamar", side_effect=self.rpc)):
            p.start(); self.addCleanup(p.stop)
        for c in self.raiz['empresas'].values():
            c['rendicion'] = False
            c['imputador'] = 'analitica_factura'
            c['cuentas'] = {n: {'ids': {'ensayo': 601, 'principal': 701}} for n in c['categorias']}
        async def proponer(ctx, marco, lineas):
            return {'lineas': {x['n']: {'distribucion': {41: 100}, 'grupo': 'obra',
                'rotulo': 'Obra de Prueba', 'detalle': '', 'texto': 'Obra de Prueba', 'estado': {}} for x in lineas}}
        async def revalidar(*args): return None
        plugins.registrar(plugins.Plugin(nombre='analitica_factura', grupo='analitica_factura', extensiones=(
            plugins.Extension('gastos.imputador', 1, 'analitica_factura',
                SimpleNamespace(proponer=proponer, revalidar=revalidar, nota='Analítica configurada')),)))
        self.addCleanup(plugins.quitar, 'analitica_factura')
        self.campos_contrato = {'receipt_bill_contract', 'receipt_bill_lot', 'receipt_bill_photo',
            'receipt_bill_payment', 'receipt_bill_payload_hash', 'receipt_bill_source_uid',
            'receipt_bill_original_total', 'receipt_bill_original_analytic'}
        for alias, erp in self.erps.items():
            erp['account.move'] = {}; erp['account.move.line'] = {}
            erp['account.account'] = {601 if alias == 'ensayo' else 701: {
                'id': 601 if alias == 'ensayo' else 701, 'company_ids': [13, 17] if alias == 'ensayo' else [3, 7],
                'deprecated': False, 'account_type': 'expense'}}
        self.escribir()

    async def rpc(self, ctx, modelo, metodo, args, kw, *, destino=None):
        alias = 'ensayo' if destino['url'] == 'https://ensayo.example.test' else 'principal'
        erp = self.erps[alias]
        if modelo not in ('account.move', 'account.move.line', 'account.account', 'res.company') and not (modelo == 'hr.expense' and metodo == 'fields_get'):
            result = await empresas.EmpresasTests.rpc(self, ctx, modelo, metodo, args, kw, destino=destino)
            if modelo == 'hr.expense' and metodo == 'create':
                erp[modelo][result]['receipt_bill_contract'] = 2
                for k in ('receipt_bill_source_id', 'invoice_id'):
                    if k in erp[modelo][result]: erp[modelo][result][k] = [erp[modelo][result][k], 'Factura']
            return result
        self.rpc_llamadas.append((alias, modelo, metodo, copy.deepcopy(args)))
        if metodo == 'fields_get':
            return {k: {'type': 'integer'} for k in (self.campos_contrato if modelo == 'account.move' else {'receipt_bill_source_id', 'receipt_bill_contract', 'invoice_id'})}
        if modelo == 'res.company': return [{'id': args[0][0], 'name': 'Casa de Prueba'}]
        tabla = erp[modelo]
        if metodo == 'read': return copy.deepcopy([tabla[i] for i in args[0] if i in tabla])
        if metodo == 'search_read':
            return copy.deepcopy([r for r in tabla.values() if empresas.cumple(r, args[0])][:kw.get('limit', 999)])
        assert modelo == 'account.move' and metodo == 'create', (modelo, metodo)
        v = copy.deepcopy(args[0]); ident = 30000 + len(tabla)
        lid = ident + 1000; linea = v.pop('invoice_line_ids')[0][2]
        erp['account.move.line'][lid] = {**linea, 'id': lid, 'tax_ids': []}
        fila = {**v, 'id': ident, 'state': 'draft', 'receipt_bill_contract': 2,
            'receipt_bill_source_uid': erp['uid'], 'receipt_bill_original_total': linea['price_unit'],
            'receipt_bill_original_analytic': linea['analytic_distribution'],
            'invoice_line_ids': [lid], 'amount_total': linea['price_unit']}
        for k in ('company_id', 'currency_id', 'journal_id', 'receipt_bill_source_uid'):
            fila[k] = [fila[k], 'Prueba']
        erp['account.move.line'][lid]['account_id'] = [linea['account_id'], 'Cuenta']
        tabla[ident] = fila
        return ident

    async def test_factura_sin_empleados_ni_rrhh_con_analitica_fotos_e_idempotencia(self):
        self.user.odoo_ids_extra.pop('empleados_gastos')
        self.user.odoo_ids_extra.pop('empleados_gastos_destinos')
        for e in self.erps.values():
            for p in e['product.product'].values(): p.pop('can_be_expensed')
        await self.preparar()
        r = await self.aprobar(); self.assertNotIn('is_error', r, r)
        e = self.erps['ensayo']; self.assertEqual(len(e['account.move']), 2)
        self.assertEqual(len(e['ir.attachment']), 2)
        self.assertTrue(all(l['analytic_distribution'] == {'41': 100} for l in e['account.move.line'].values()))
        await self.aprobar()
        self.assertEqual(len(e['account.move']), 2); self.assertEqual(len(e['ir.attachment']), 2)
        self.assertFalse(any(m.startswith('hr.') for _, m, *_ in self.rpc_llamadas))

    async def test_politica_bloqueante_no_interviene_en_registro_fiscal(self):
        proveedor = self.politica(bloquear=True)
        async def prohibido(*args): raise AssertionError('No evaluar conducta al registrar factura')
        proveedor.evaluar = prohibido
        await self.preparar(); self.assertNotIn('is_error', await self.aprobar())
        self.assertEqual(self.marcos, [])

    async def test_mismo_lote_destinos_recibos_y_ok_separados(self):
        ensayo = await self.preparar(); self.assertNotIn('is_error', await self.aprobar())
        p = estado.recibo_path('L000123', self.seleccion()); antes = p.read_bytes()
        await self.preparar(destino='principal')
        self.assertTrue((await self.aprobar(destino='principal', huella=ensayo['huella']))['is_error'])
        self.assertEqual(self.erps['principal']['account.move'], {})
        self.assertNotIn('is_error', await self.aprobar(destino='principal'))
        self.assertEqual(p.read_bytes(), antes)

    async def test_campos_addon_faltantes_cierran_antes_de_crear(self):
        await self.preparar(); self.campos_contrato.remove('receipt_bill_payload_hash')
        self.assertTrue((await self.aprobar())['is_error']); self.sin_escrituras()

    async def test_cuenta_de_otra_compania_falla_antes_de_escribir(self):
        self.erps['ensayo']['account.account'][601]['company_ids'] = [999]
        with self.assertRaises(gastos.GastoError): await self.preparar()
        self.sin_escrituras()

    async def test_sin_imputador_no_registra_factura(self):
        self.raiz['empresas']['taller']['imputador'] = ''; self.escribir()
        with self.assertRaisesRegex(gastos.GastoError, 'analítica'): await self.preparar()
        self.sin_escrituras()

    async def test_cambios_de_empleado_politica_rendicion_no_invalidan_ok_fiscal(self):
        p = await self.preparar()
        self.user.odoo_ids_extra['empleados_gastos']['taller'] = 999
        self.raiz['empresas']['taller']['rendicion'] = True
        self.raiz['empresas']['taller']['politica'] = 'otra_politica'; self.escribir()
        self.assertNotIn('is_error', await self.aprobar(huella=p['huella']))
        self.assertFalse(any(m.startswith('hr.') for _, m, *_ in self.rpc_llamadas))

    async def test_rendicion_habilitada_no_crea_rrhh_al_registrar_factura(self):
        self.raiz['empresas']['taller']['rendicion'] = True; self.escribir()
        await self.preparar(); self.assertNotIn('is_error', await self.aprobar())
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})
        self.assertEqual(self.erps['ensayo']['hr.expense.sheet'], {})

    async def test_factura_borrador_no_impide_registro_pero_no_se_rinde_aun(self):
        self.raiz['empresas']['taller']['rendicion'] = True; self.escribir()
        await self.preparar(); self.assertNotIn('is_error', await self.aprobar())
        with self.assertRaisesRegex(gastos.GastoError, 'contabilizar'):
            await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos)
        self.assertEqual(len(self.erps['ensayo']['account.move']), 2)
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})

    async def facturas_contabilizadas(self):
        self.raiz['empresas']['taller']['rendicion'] = True; self.escribir()
        await self.preparar(); fiscal = await gastos._huella_cargar(self.ctx, self.args())
        self.assertNotIn('is_error', await self.aprobar())
        for b in self.erps['ensayo']['account.move'].values(): b['state'] = 'posted'
        return fiscal

    async def test_ok_fiscal_no_autoriza_rendicion(self):
        fiscal = await self.facturas_contabilizadas()
        p = await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos)
        self.assertNotEqual(fiscal, p['huella'])
        with self.assertRaisesRegex(gastos.GastoError, 'propio OK'):
            await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos, cargar=True, aprobacion=fiscal)
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})

    async def test_politica_rechaza_rendicion_y_conserva_facturas(self):
        await self.facturas_contabilizadas(); self.politica(bloquear=True)
        before = copy.deepcopy(self.erps['ensayo']['account.move'])
        p = await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos)
        self.assertEqual(p['estado'], 'bloqueada')
        with self.assertRaises(gastos.GastoError):
            await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos, cargar=True, aprobacion=p['huella'])
        self.assertEqual(self.erps['ensayo']['account.move'], before)
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})

    async def test_rendicion_enlaza_factura_existente_sin_duplicar_coste(self):
        await self.facturas_contabilizadas()
        p = await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos)
        before = copy.deepcopy(self.erps['ensayo']['account.move'])
        for _ in range(2):
            await gastos.facturas.operar_rendicion(self.ctx, self.args(), gastos, cargar=True, aprobacion=p['huella'])
        e = self.erps['ensayo']; self.assertEqual(e['account.move'], before)
        self.assertEqual(len(e['hr.expense']), 2)
        self.assertEqual({r['invoice_id'][0] for r in e['hr.expense'].values()}, set(before))
        self.assertEqual(len(e['hr.expense.sheet']), 2)

    async def test_reintento_adjunto_recupera_factura_sin_duplicarla(self):
        await self.preparar(); self.fallar_adjunto = True
        self.assertTrue((await self.aprobar())['is_error'])
        self.assertEqual(len(self.erps['ensayo']['account.move']), 1)
        self.assertNotIn('is_error', await self.aprobar())
        self.assertEqual(len(self.erps['ensayo']['account.move']), 2)
        self.assertEqual(len(self.erps['ensayo']['ir.attachment']), 2)

    async def test_cuentas_deben_cubrir_catalogo_y_destinos(self):
        del self.raiz['empresas']['taller']['cuentas']['Traslados']['ids']['principal']
        self.escribir()
        with self.assertRaises(gastos.GastoError): await self.preparar()
        self.sin_escrituras()

    async def test_excepcion_politica_impide_rendir_sin_alterar_facturas(self):
        await self.facturas_contabilizadas(); proveedor = self.politica()
        before = copy.deepcopy(self.erps['ensayo']['account.move'])
        async def falla(*args): raise RuntimeError('Fallo sintético de política')
        proveedor.evaluar = falla
        resultado = await gastos._tool_rendicion_preparar(self.ctx, self.args())
        self.assertTrue(resultado['is_error'])
        self.assertEqual(self.erps['ensayo']['account.move'], before)
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})

    async def test_tools_de_rendicion_muestran_resumen_y_consumen_ok_propio(self):
        await self.facturas_contabilizadas()
        r = await gastos._tool_rendicion_preparar(self.ctx, self.args())
        self.assertNotIn('is_error', r, r)
        firma = await gastos._huella_rendir(self.ctx, self.args())
        titulo, texto = await gastos._resumen_rendir(self.ctx, self.args(), False)
        self.assertIn('independiente', titulo)
        self.assertIn('facturas', texto)
        self.ctx.consumir_aprobacion = lambda herramienta, args: firma if herramienta == 'gasto_cargar' else None
        rechazo = await gastos._tool_rendir(self.ctx, self.args())
        self.assertTrue(rechazo['is_error'])
        self.assertIn('propio OK', rechazo['publico'])
        self.assertEqual(self.erps['ensayo']['hr.expense'], {})
        self.ctx.consumir_aprobacion = lambda herramienta, args: firma if herramienta == 'gasto_rendir' else None
        rendida = await gastos._tool_rendir(self.ctx, self.args())
        self.assertNotIn('is_error', rendida)
        self.assertIn('Informe de gastos', rendida['publico'])
        self.assertNotIn('{', rendida['publico'])
        self.assertEqual(len(self.erps['ensayo']['account.move']), 2)

    async def test_rendicion_deshabilitada_no_consulta_rrhh_ni_exige_vinculos_hr_validos(self):
        self.user.odoo_ids_extra['empleados_gastos'] = 'no requerido'
        self.user.odoo_ids_extra['empleados_gastos_destinos'] = None
        await self.preparar(); self.assertNotIn('is_error', await self.aprobar())
        self.assertTrue((await gastos._tool_rendicion_preparar(self.ctx, self.args()))['is_error'])
        self.assertFalse(any(m.startswith('hr.') for _,m,*_ in self.rpc_llamadas))

    async def test_todas_las_llamadas_nuevas_fijan_contexto_de_compania(self):
        original=self.rpc
        async def verificar(ctx,modelo,metodo,args,kw,*,destino=None):
            if (modelo,metodo)==('res.users','context_get'):
                self.assertEqual(kw,{})
            else:
                self.assertEqual(kw['context']['allowed_company_ids'],[13])
            return await original(ctx,modelo,metodo,args,kw,destino=destino)
        with patch.object(odoo,'llamar',side_effect=verificar):
            await self.preparar(); self.assertNotIn('is_error',await self.aprobar())
