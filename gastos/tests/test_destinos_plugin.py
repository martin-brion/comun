"""Destinos sintéticos mixtos: addon y ERP estándar sin extensiones contables."""
import copy
import time
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tests import test_facturas_plugin as facturas
from tests import test_empresas_plugin as empresas
from tests import test_gastos_plugin as banco
from bot import config, odoo, plugins

gastos, estado = facturas.gastos, facturas.estado


class DestinosTests(banco._BancoOdoo):
    crear_lote = empresas.EmpresasTests.crear_lote
    escribir = facturas.FacturasTests.escribir
    args = facturas.FacturasTests.args
    preparar = facturas.FacturasTests.preparar
    aprobar = facturas.FacturasTests.aprobar
    seleccion = facturas.FacturasTests.seleccion
    sin_escrituras = facturas.FacturasTests.sin_escrituras
    politica = facturas.FacturasTests.politica

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

        for c in self.raiz['empresas'].values():
            c['rendicion'] = True
            c['destinos']['ensayo']['capacidades'] = {'factura': 'addon', 'rendicion': 'vinculada',
                'control_sin_contabilizar_confirmado': False}
            c['destinos']['principal']['capacidades'] = {'factura': 'estandar', 'rendicion': 'revision_hr',
                'control_sin_contabilizar_confirmado': True}
        self.inyectar = None
        self.create_error = None
        self.adjunto_confirmado_error = False
        self.escribir()
    async def rpc(self, ctx, modelo, metodo, args, kw, *, destino=None):
        alias = 'ensayo' if destino['url'] == 'https://ensayo.example.test' else 'principal'
        if alias == 'ensayo':
            return await facturas.FacturasTests.rpc(self, ctx, modelo, metodo, args, kw, destino=destino)
        erp = self.erps[alias]
        if metodo == 'check_access_rights':
            self.rpc_llamadas.append((alias, modelo, metodo, copy.deepcopy(args)))
            return True
        if metodo == 'fields_get':
            self.rpc_llamadas.append((alias, modelo, metodo, copy.deepcopy(args)))
            tipos = {
                'hr.expense': {'name':'char','description':'html','company_id':'many2one','employee_id':'many2one',
                    'currency_id':'many2one','date':'date','payment_mode':'selection','total_amount_currency':'monetary',
                    'analytic_distribution':'json','product_id':'many2one','state':'selection','quantity':'float','tax_ids':'many2many'},
                'hr.expense.sheet': {'name':'char','state':'selection','company_id':'many2one','employee_id':'many2one',
                    'employee_journal_id':'many2one','expense_line_ids':'one2many'},
                'account.move': {'state':'selection','move_type':'selection','company_id':'many2one',
                    'currency_id':'many2one','journal_id':'many2one','partner_id':'many2one','create_uid':'many2one','invoice_date':'date',
                    'amount_total':'monetary','invoice_origin':'char','ref':'char','auto_post':'selection',
                    'invoice_line_ids':'one2many'},
                'account.move.line': {'account_id':'many2one','product_id':'many2one','analytic_distribution':'json',
                    'quantity':'float','price_unit':'float','discount':'float','tax_ids':'many2many','display_type':'selection'},
                'ir.attachment': {'name':'char','checksum':'char','res_model':'char','type':'selection',
                    'res_id':'many2one_reference','datas':'binary','public':'boolean','access_token':'char'},
            }
            self.assertIn(modelo, tipos, 'ERP estándar no expone campos de addon')
            return {k:{'type':v} for k,v in tipos[modelo].items()}
        if modelo in ('account.move', 'account.move.line'):
            self.rpc_llamadas.append((alias, modelo, metodo, copy.deepcopy(args)))
            self.assertFalse(any('receipt_bill' in str(x) for x in (args, kw)), 'Campos de addon enviados a ERP estándar')
            tabla = erp[modelo]
            if metodo == 'search_read':
                return copy.deepcopy([r for r in tabla.values() if empresas.cumple(r,args[0])][:kw.get('limit',999)])
            if metodo == 'read': return copy.deepcopy([tabla[i] for i in args[0] if i in tabla])
            self.assertEqual((modelo,metodo),('account.move','create'))
            if self.create_error == 'sin_efectos': raise odoo.OdooError('Resultado incierto sintético')
            v = copy.deepcopy(args[0]); ident = 40000 + len(tabla); lid=ident+1000
            linea=v.pop('invoice_line_ids')[0][2]
            erp['account.move.line'][lid]={'id':lid,'product_id':False,'discount':0,'display_type':'product',
                **linea,'tax_ids':[],'account_id':[linea['account_id'],'Cuenta'],
                'product_id':[linea['product_id'],'Producto'] if linea.get('product_id') else False}
            fila={'state':'draft','partner_id':False,'ref':False,'invoice_origin':False,'auto_post':'no',
                **v,'id':ident,'create_uid':[erp['uid'],'Persona'],'invoice_line_ids':[lid],'amount_total':linea['price_unit']}
            for k in ('company_id','currency_id','journal_id'): fila[k]=[fila[k],'Prueba']
            if self.inyectar:
                campo,valor=self.inyectar
                if campo.startswith('linea.'):
                    erp['account.move.line'][lid][campo[6:]]=valor
                else: fila[campo]=valor
            tabla[ident]=fila
            if self.create_error == 'confirmado':
                self.create_error=None
                raise odoo.OdooError('Resultado incierto después de confirmar')
            return ident
        if modelo.startswith('hr.'):
            self.assertFalse(any(t in str((args,kw)) for t in ('receipt_bill_', 'invoice_id')))
        resultado=await facturas.FacturasTests.rpc(self,ctx,modelo,metodo,args,kw,destino=destino)
        if modelo=='ir.attachment' and metodo=='create':
            erp[modelo][resultado].update(type='binary',public=False,access_token=False)
            if self.adjunto_confirmado_error:
                self.adjunto_confirmado_error=False
                raise odoo.OdooError('Adjunto confirmado con resultado incierto')
        return resultado

    async def test_mismo_lote_addon_a_estandar_con_ok_y_recibos_separados(self):
        p=await self.preparar(); self.assertNotIn('is_error',await self.aprobar())
        recibo=estado.recibo_path('L000123',self.seleccion()); antes=recibo.read_bytes()
        await self.preparar(destino='principal')
        self.assertTrue((await self.aprobar(destino='principal',huella=p['huella']))['is_error'])
        self.assertEqual(self.erps['principal']['account.move'],{})
        r=await self.aprobar(destino='principal'); self.assertNotIn('is_error',r,r)
        self.assertEqual(recibo.read_bytes(),antes)
        self.assertEqual(len(self.erps['principal']['account.move']),2)
        self.assertFalse(any(m.startswith('hr.') for a,m,*_ in self.rpc_llamadas if a=='principal'))

    async def test_estandar_no_necesita_empleados_ni_addon(self):
        c=self.raiz['empresas']['taller']; c['rendicion']=False
        for d in c['destinos'].values(): d['capacidades']['rendicion']='ninguna'
        self.escribir()
        self.user.odoo_ids_extra.pop('empleados_gastos'); self.user.odoo_ids_extra.pop('empleados_gastos_destinos')
        await self.preparar(destino='principal')
        r=await self.aprobar(destino='principal'); self.assertNotIn('is_error',r,r)
        self.assertFalse(any(m.startswith('hr.') for a,m,*_ in self.rpc_llamadas if a=='principal'))

    async def test_defaults_fiscales_y_analiticos_alterados_fallan_cerrado(self):
        for indice,(campo,valor) in enumerate([('partner_id',[99,'Proveedor']),('ref','Referencia ajena'),
                ('currency_id',[999,'Otra']),('move_type','entry'),('auto_post','at_date'),
                ('create_uid',[999,'Otra persona']),('linea.tax_ids',[99]),('linea.product_id',[99,'Producto']),('linea.discount',10),
                ('linea.account_id',[999,'Otra']),('linea.analytic_distribution',{'99':100})]):
            with self.subTest(campo=campo):
                self.inyectar=(campo,valor)
                lote_id=f'L{124+indice:06d}'; self.crear_lote(lote_id)
                await self.preparar(destino='principal',lote_id=lote_id)
                self.assertTrue((await self.aprobar(destino='principal',lote_id=lote_id))['is_error'])
                self.assertEqual(len(self.erps['principal']['account.move']),indice+1)

    async def test_create_confirmado_error_recupera_sin_duplicar(self):
        await self.preparar(destino='principal'); self.create_error='confirmado'
        await self.aprobar(destino='principal')
        r=await self.aprobar(destino='principal'); self.assertNotIn('is_error',r,r)
        self.assertEqual(len(self.erps['principal']['account.move']),2)

    async def test_create_incierto_sin_evidencias_no_repite_creacion(self):
        await self.preparar(destino='principal'); self.create_error='sin_efectos'
        self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.create_error=None
        self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.assertEqual(self.erps['principal']['account.move'],{})
        llamadas=[x for x in self.rpc_llamadas if x[1:3]==('account.move','create')]
        self.assertEqual(len(llamadas),1)

    async def test_id_conocido_marcador_editado_no_crea_otra_factura(self):
        await self.preparar(destino='principal'); self.assertNotIn('is_error',await self.aprobar(destino='principal'))
        filas=self.erps['principal']['account.move']; first=next(iter(filas.values()))
        first['invoice_origin']='Editado por operador'
        await self.aprobar(destino='principal')
        self.assertEqual(len(filas),2)

    async def test_adjunto_parcial_reintenta_sin_duplicar_factura(self):
        await self.preparar(destino='principal'); self.adjunto_confirmado_error=True
        self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        r=await self.aprobar(destino='principal'); self.assertNotIn('is_error',r,r)
        self.assertEqual(len(self.erps['principal']['account.move']),2)
        self.assertEqual(len(self.erps['principal']['ir.attachment']),2)

    async def test_cambio_modo_del_destino_invalida_ok(self):
        p=await self.preparar(destino='principal')
        self.raiz['empresas']['taller']['destinos']['principal']['capacidades']['factura']='addon'; self.escribir()
        self.assertTrue((await self.aprobar(destino='principal',huella=p['huella']))['is_error'])
        self.sin_escrituras()

    async def test_cambio_otro_destino_no_invalida_ok(self):
        p=await self.preparar(destino='principal')
        self.raiz['empresas']['taller']['destinos']['ensayo']['capacidades']['rendicion']='ninguna'; self.escribir()
        self.assertNotIn('is_error',await self.aprobar(destino='principal',huella=p['huella']))

    async def test_capacidades_invalidas_no_se_autodetectan(self):
        original=copy.deepcopy(self.raiz)
        for cap in ({'factura':'estandar','rendicion':'vinculada'},
                {'factura':'desconocida','rendicion':'ninguna'},
                {'factura':'estandar'},
                {'factura':'estandar','rendicion':'revision_hr','control_sin_contabilizar_confirmado':'si'}):
            self.raiz=copy.deepcopy(original)
            self.raiz['empresas']['taller']['destinos']['principal']['capacidades']=cap
            self.escribir()
            with self.assertRaises(gastos.GastoError): await self.preparar(destino='principal')
        self.sin_escrituras()

    async def fiscal_principal(self):
        await self.preparar(destino='principal')
        fiscal=await gastos._huella_cargar(self.ctx,self.args(destino='principal'))
        r=await self.aprobar(destino='principal'); self.assertNotIn('is_error',r,r)
        for fila in self.erps['principal']['account.move'].values(): fila['state']='posted'
        return fiscal

    async def test_revision_hr_solo_borrador_y_ok_propio_sin_campos_de_addon(self):
        fiscal=await self.fiscal_principal()
        args=self.args(destino='principal')
        p=await gastos.facturas.operar_rendicion(self.ctx,args,gastos)
        self.assertIn(gastos.facturas.AVISO_CONTROL,p['aviso'])
        titulo,cuerpo=await gastos._resumen_rendir(self.ctx,args,False)
        self.assertIn(gastos.facturas.AVISO_CONTROL,cuerpo)
        with self.assertRaises(gastos.GastoError):
            await gastos.facturas.operar_rendicion(self.ctx,args,gastos,cargar=True,aprobacion=fiscal)
        before=copy.deepcopy(self.erps['principal']['account.move'])
        for _ in range(2):
            await gastos.facturas.operar_rendicion(self.ctx,args,gastos,cargar=True,aprobacion=p['huella'])
        self.assertEqual(self.erps['principal']['account.move'],before)
        hojas=list(self.erps['principal']['hr.expense.sheet'].values())
        self.assertEqual(len(hojas),2); self.assertTrue(all(h['state']=='draft' for h in hojas))
        self.assertEqual(len(self.erps['principal']['hr.expense']),2)
        self.assertTrue(all(x['description']==gastos.facturas.AVISO_CONTROL
            for x in self.erps['principal']['hr.expense'].values()))
        self.assertFalse(any(metodo not in ('read','search_read','fields_get','check_access_rights','create','context_get')
            for _,_,metodo,*_ in self.rpc_llamadas))

    async def test_politica_rechaza_revision_hr_y_conserva_facturas(self):
        await self.fiscal_principal(); self.politica(bloquear=True)
        before=copy.deepcopy(self.erps['principal']['account.move'])
        p=await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos)
        self.assertEqual(p['estado'],'bloqueada')
        with self.assertRaises(gastos.GastoError):
            await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos,
                cargar=True,aprobacion=p['huella'])
        self.assertEqual(self.erps['principal']['account.move'],before)
        self.assertEqual(self.erps['principal']['hr.expense'],{})

    async def test_sin_confirmacion_admin_factura_funciona_pero_hr_no(self):
        self.raiz['empresas']['taller']['destinos']['principal']['capacidades']['control_sin_contabilizar_confirmado']=False
        self.escribir(); await self.fiscal_principal()
        with self.assertRaises(gastos.GastoError):
            await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos)
        self.assertEqual(len(self.erps['principal']['account.move']),2)
        self.assertEqual(self.erps['principal']['hr.expense'],{})

    async def test_candado_entre_procesos_impide_operacion_y_se_libera(self):
        import asyncio
        import sys
        from importlib import import_module
        custodia=import_module(gastos.__name__+'.custodia')
        c=self.seleccion(destino='principal'); path=custodia.ruta('L000123',c)
        codigo=('import fcntl,sys; f=open(sys.argv[1],"a+"); '
            'fcntl.flock(f,fcntl.LOCK_EX); print("LISTO",flush=True); sys.stdin.readline()')
        hijo=await asyncio.create_subprocess_exec(sys.executable,'-I','-B','-c',codigo,str(path),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE)
        try:
            self.assertEqual(await asyncio.wait_for(hijo.stdout.readline(),3),b'LISTO\n')
            with patch.object(custodia,'TOPE_ESPERA_S',0.1):
                with self.assertRaises(gastos.GastoError): await self.preparar(destino='principal')
            self.sin_escrituras()
        finally:
            hijo.stdin.write(b'fin\n'); await hijo.stdin.drain()
            await asyncio.wait_for(hijo.wait(),3)
        await self.preparar(destino='principal')

    async def test_adjunto_incierto_sin_efectos_no_repite_creacion(self):
        await self.preparar(destino='principal'); self.fallar_adjunto=True
        self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.assertEqual(len(self.erps['principal']['account.move']),1)
        self.assertEqual(self.erps['principal']['ir.attachment'],{})
        llamadas=[x for x in self.rpc_llamadas if x[1:3]==('ir.attachment','create')]
        self.assertEqual(len(llamadas),1)

    async def test_marcador_ambiguo_no_adopta_ni_crea_factura(self):
        await self.preparar(destino='principal')
        original=self.rpc
        async def ambiguo(ctx,modelo,metodo,args,kw,*,destino=None):
            if destino['url']=='https://principal.example.test' and modelo=='account.move' and metodo=='search_read':
                return [{'id':80001},{'id':80002}]
            return await original(ctx,modelo,metodo,args,kw,destino=destino)
        with patch.object(odoo,'llamar',side_effect=ambiguo):
            self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.sin_escrituras()

    async def test_metadatos_faltantes_no_habilitan_fallback(self):
        await self.preparar(destino='principal')
        original=self.rpc
        async def incompleto(ctx,modelo,metodo,args,kw,*,destino=None):
            r=await original(ctx,modelo,metodo,args,kw,destino=destino)
            if destino['url']=='https://principal.example.test' and modelo=='account.move' and metodo=='fields_get':
                r.pop('auto_post')
            return r
        with patch.object(odoo,'llamar',side_effect=incompleto):
            self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.sin_escrituras()

    async def test_producto_sin_gastos_permite_factura_y_bloquea_solo_hr(self):
        for p in self.erps['principal']['product.product'].values(): p.pop('can_be_expensed')
        await self.fiscal_principal()
        before=copy.deepcopy(self.erps['principal']['account.move'])
        with self.assertRaises(gastos.GastoError):
            await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos)
        self.assertEqual(self.erps['principal']['account.move'],before)
        self.assertEqual(self.erps['principal']['hr.expense'],{})

    async def test_acl_contable_denegada_no_inicia_escritura(self):
        await self.preparar(destino='principal')
        original=self.rpc
        async def denegar(ctx,modelo,metodo,args,kw,*,destino=None):
            if destino['url']=='https://principal.example.test' and modelo=='account.move' and metodo=='check_access_rights':
                return False
            return await original(ctx,modelo,metodo,args,kw,destino=destino)
        with patch.object(odoo,'llamar',side_effect=denegar):
            self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.sin_escrituras()

    async def test_acl_hr_denegada_no_altera_facturas(self):
        await self.fiscal_principal()
        original=self.rpc; before=copy.deepcopy(self.erps['principal']['account.move'])
        async def denegar(ctx,modelo,metodo,args,kw,*,destino=None):
            if modelo=='hr.expense.sheet' and metodo=='check_access_rights': return False
            return await original(ctx,modelo,metodo,args,kw,destino=destino)
        with patch.object(odoo,'llamar',side_effect=denegar):
            with self.assertRaises(gastos.GastoError):
                await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos)
        self.assertEqual(self.erps['principal']['account.move'],before)
        self.assertEqual(self.erps['principal']['hr.expense'],{})

    async def test_campos_hr_faltantes_no_dejan_control_parcial(self):
        await self.fiscal_principal(); original=self.rpc
        async def incompleto(ctx,modelo,metodo,args,kw,*,destino=None):
            r=await original(ctx,modelo,metodo,args,kw,destino=destino)
            if modelo=='hr.expense.sheet' and metodo=='fields_get': r.pop('expense_line_ids')
            return r
        with patch.object(odoo,'llamar',side_effect=incompleto):
            with self.assertRaises(gastos.GastoError):
                await gastos.facturas.operar_rendicion(self.ctx,self.args(destino='principal'),gastos)
        self.assertEqual(self.erps['principal']['hr.expense'],{})
        self.assertEqual(self.erps['principal']['hr.expense.sheet'],{})

    async def test_default_latam_no_emite_comprobante_fiscal_completo(self):
        await self.preparar(destino='principal'); original=self.rpc
        async def latam(ctx,modelo,metodo,args,kw,*,destino=None):
            r=await original(ctx,modelo,metodo,args,kw,destino=destino)
            if destino['url']=='https://principal.example.test' and modelo=='account.move':
                if metodo=='fields_get':
                    r.update(l10n_latam_document_type_id={'type':'many2one'},
                        l10n_latam_document_number={'type':'char'})
                if metodo=='create':
                    self.erps['principal']['account.move'][r].update(
                        l10n_latam_document_type_id=[11,'Tipo sintético'],l10n_latam_document_number='0001-00000001')
            return r
        with patch.object(odoo,'llamar',side_effect=latam):
            self.assertTrue((await self.aprobar(destino='principal'))['is_error'])
        self.assertEqual(len(self.erps['principal']['account.move']),1)
        self.assertEqual(self.erps['principal']['ir.attachment'],{})
