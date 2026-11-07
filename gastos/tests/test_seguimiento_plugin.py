"""Una factura recuperada mantiene su seguimiento y un alta fiscal fallida no se usa."""
import copy
import importlib
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from tests import test_fiscal_plugin as base
from bot import plugins

g = base.gastos
proveedores = importlib.import_module(g.__name__ + '.proveedores')


class SeguimientoTests(base.FiscalTests):
    async def test_proveedor_ausente_exige_alta_sin_facturas(self):
        self.raiz['empresas']['taller']['padron_proveedores']='AR'
        self.escribir()
        self.erps['principal']['res.partner']={}
        with self.assertRaisesRegex(g.GastoError,'Falta el proveedor'):
            await self.preparar(destino='principal')
        self.assertFalse(self.erps['principal']['account.move'])

    async def test_contacto_provisional_no_es_validacion_fiscal(self):
        from plugins_instancia.gastos.proveedores import PENDIENTE
        self.raiz['empresas']['taller']['padron_proveedores']='AR'
        self.escribir()
        self.erps['principal']['res.partner'][9001]['name']=PENDIENTE+base.CUIT
        with self.assertRaisesRegex(g.GastoError,'Falta el proveedor'):
            await self.preparar(destino='principal')

    def con_alta(self):
        c=self.raiz['empresas']['taller']
        c.update(padron_proveedores='AR',alta_proveedor={'repo':'herramientas','script':'contactos/alta.py'})
        self.escribir()
        self.erps['principal']['res.partner']={}
        self.corridas=[]
        secretos=patch.object(proveedores,'SECRETOS',('PRINCIPAL_LOGIN','PRINCIPAL_KEY'))
        secretos.start()
        self.addCleanup(secretos.stop)

    def toolbelt(self,rc=0,salida=None,crea=True,**extra):
        """La puerta del motor con un toolbelt falso: con --apply crea la razón social, como el de verdad."""
        async def correr(ctx,repo,rel,argv,aplicar,al_correr=None):
            self.corridas.append((repo,rel,list(argv),aplicar))
            res={'empresa_id':None,'empresa':None,'creados':[],'avisos':[],'error':None,**extra}
            if aplicar and rc==0 and crea:
                self.socio(9100,'Razón del Padrón')
                res.update(empresa_id=9100,empresa='Razón del Padrón',
                           creados=[{'modelo':'res.partner','id':9100,'tipo':'empresa'}])
            texto=salida if salida is not None else 'línea legible\nJSON '+json.dumps(res,ensure_ascii=False)
            al_correr(rc,texto)
            return {'content':[{'type':'text','text':texto}]}
        return patch('bot.broker.ejecutar_toolbelt',side_effect=correr)

    def sin_facturas(self):
        self.assertFalse(any(x[1:3]==('account.move','create') for x in self.rpc_llamadas))

    async def test_la_propuesta_suma_el_alta_sin_escribir(self):
        self.con_alta()
        with self.toolbelt():
            p=await self.preparar(destino='principal')
        self.assertEqual(len(self.corridas),1)
        repo,rel,argv,aplicar=self.corridas[0]
        self.assertEqual((repo,rel,argv[:4],aplicar),('herramientas','contactos/alta.py',['--cuit',base.CUIT,'--pais','AR'],False))
        factura=p['propuesta']['comprobantes'][0]['factura']
        self.assertIn('proveedor nuevo',factura['proveedor'])
        self.assertFalse(any('Dar de alta' in t for t in factura['completa_administracion']))
        titulo,cuerpo=await g._resumen_cargar(self.ctx,self.args('principal'),False)
        self.assertIn('proveedor nuevo',cuerpo)
        self.assertIn('Antes de cargar doy de alta',cuerpo)
        self.sin_facturas()

    async def test_con_el_ok_da_de_alta_y_carga_con_ese_proveedor(self):
        self.con_alta()
        with self.toolbelt():
            await self.preparar(destino='principal')
            with g._contexto(self.ctx,self.args('principal')):
                aprobada=g.huella('L000123')
            r=await self.aprobar(destino='principal')
        self.assertNotIn('is_error',r,r)
        self.assertIs(self.corridas[-1][3],True)
        una,*_=self.facturas()
        self.assertEqual(una['partner_id'][0],9100)
        with g._contexto(self.ctx,self.args('principal')):
            self.assertEqual(g.huella('L000123'),aprobada)
        texto=r['content'][0]['text']
        self.assertIn('Razón del Padrón',texto)
        self.assertIn('Proveedor nuevo: Razón del Padrón',texto)
        with self.assertRaisesRegex(g.GastoError,'ya está cargado'):
            await self.preparar(destino='principal')

    async def test_retomar_una_carga_cortada_no_cambia_lo_firmado(self):
        self.con_alta()
        with self.toolbelt():
            await self.preparar(destino='principal')
        with g._contexto(self.ctx,self.args('principal')):
            plan=g._leer('L000123')
        x=plan['comprobantes'][0]
        firma=g.estandar._payload(plan,x)
        x['fiscal'].update(partner_id=9100,proveedor='Razón del Padrón',proveedor_aviso='Había dos')
        self.assertEqual(g.estandar._payload(plan,x),firma)
        x['fiscal']['iva']='1.00'
        self.assertNotEqual(g.estandar._payload(plan,x),firma)

    async def test_si_el_padron_no_la_valida_no_hay_facturas_y_se_prepara_de_nuevo(self):
        self.con_alta()
        with self.toolbelt():
            await self.preparar(destino='principal')
        with self.toolbelt(rc=1,crea=False,error='ARCA no la validó'):
            r=await self.aprobar(destino='principal')
        self.assertTrue(r.get('is_error'))
        self.assertIn('ARCA no la validó',r['content'][0]['text'])
        self.assertIn('No cargué ninguna factura',r['content'][0]['text'])
        self.sin_facturas()
        with self.toolbelt():
            await self.preparar(destino='principal')

    async def test_un_aviso_del_padron_frena_y_la_nueva_propuesta_muestra_la_razon_social(self):
        self.con_alta()
        with self.toolbelt(avisos=['Lo leído no se parece al padrón.']):
            await self.preparar(destino='principal')
            r=await self.aprobar(destino='principal')
        self.assertTrue(r.get('is_error'))
        self.assertIn('Lo leído no se parece al padrón.',r['content'][0]['text'])
        self.sin_facturas()
        corridas=len(self.corridas)
        with self.toolbelt():
            p=await self.preparar(destino='principal')
            r=await self.aprobar(destino='principal')
        self.assertEqual(p['propuesta']['comprobantes'][0]['factura']['proveedor'],'Razón del Padrón')
        self.assertEqual(len(self.corridas),corridas)
        self.assertNotIn('is_error',r,r)

    async def test_otro_cargo_el_proveedor_entre_la_propuesta_y_el_ok(self):
        self.con_alta()
        with self.toolbelt():
            await self.preparar(destino='principal')
        self.socio(9100,'Razón del Padrón')
        with self.toolbelt(crea=False,empresa_id=9100,empresa='Razón del Padrón'):
            r=await self.aprobar(destino='principal')
        self.assertNotIn('is_error',r,r)
        self.assertEqual(self.facturas()[0]['partner_id'][0],9100)

    async def test_el_toolbelt_cortado_no_declara_nada(self):
        self.con_alta()
        with self.toolbelt():
            await self.preparar(destino='principal')
        with self.toolbelt(rc=1,salida='timeout'):
            r=await self.aprobar(destino='principal')
        self.assertIn('no sé si llegó',r['content'][0]['text'])
        self.sin_facturas()

    async def test_ensayo_que_no_pudo_correr_o_que_falla_frena_la_propuesta(self):
        self.con_alta()
        sin_acceso={'content':[{'type':'text','text':'sin acceso al repositorio'}],'is_error':True}
        with patch('bot.broker.ejecutar_toolbelt',AsyncMock(return_value=sin_acceso)), \
                self.assertRaisesRegex(g.GastoError,'sin acceso al repositorio'):
            await self.preparar(destino='principal')
        with self.toolbelt(rc=1,error='no cierra el dígito verificador'), \
                self.assertRaisesRegex(g.GastoError,'dígito verificador'):
            await self.preparar(destino='principal')

    async def test_cuit_que_existe_pero_no_sirve_en_esta_compania(self):
        self.con_alta()
        with self.toolbelt(empresa_id=55,empresa='De otra compañía'), self.assertRaisesRegex(g.GastoError,'De otra compañía'):
            await self.preparar(destino='principal')

    async def test_otro_destino_no_ofrece_alta(self):
        self.con_alta()
        with patch.object(proveedores,'SECRETOS',('ODOO_LOGIN','ODOO_API_KEY')), self.toolbelt(), \
                self.assertRaisesRegex(g.GastoError,'no hay alta desde el bot'):
            await self.preparar(destino='principal')
        self.assertEqual(self.corridas,[])

    async def test_alta_proveedor_mal_declarada(self):
        c=self.raiz['empresas']['taller']
        c['padron_proveedores']='AR'
        for mala in ({'repo':'a'},{'repo':'a','script':'../fuera.py'},{'repo':'a','script':'/abs.py'},
                     {'repo':'a b','script':'x.py'},{'repo':'a','script':'x.sh'},{'repo':'a','script':'x.py','extra':1},'a'):
            c['alta_proveedor']=mala
            self.escribir()
            with self.assertRaisesRegex(g.GastoError,'alta_proveedor'):
                await self.preparar(destino='principal')
        del c['padron_proveedores']
        c['alta_proveedor']={'repo':'a','script':'x.py'}
        self.escribir()
        with self.assertRaisesRegex(g.GastoError,'padron_proveedores'):
            await self.preparar(destino='principal')

    async def test_con_una_pendiente_y_una_valida_usa_la_valida(self):
        self.raiz['empresas']['taller']['padron_proveedores']='AR'
        self.escribir()
        self.erps['principal']['res.partner']={}
        self.socio(9000,proveedores.PENDIENTE+'Leída')
        self.socio(9001,'Razón del Padrón')
        p=await self.preparar(destino='principal')
        self.assertEqual(p['propuesta']['comprobantes'][0]['factura']['proveedor'],'Razón del Padrón')

    async def test_recuperacion_repite_seguimiento_sin_efectos(self):
        hook=NS(seguimiento=AsyncMock(return_value={'estado':'pendiente'}))
        plan={'odoo_uid':27,'comprobantes':[]}
        c={'seguimiento':'posterior','empresa':'taller','destino':'principal','company_id':3}
        with patch.object(plugins,'resolver_extension',return_value=hook), patch.object(g,'_marco',return_value=NS()), \
                patch.object(g,'texto_carga',return_value='enlaces'):
            for result in ({'facturas':{'1':321}}, {'estado':'cargado','facturas':{'1':321}}):
                r=await g._con_seguimiento(self.ctx,c,plan,result)
                self.assertEqual(r['seguimiento']['estado'],'pendiente')
            self.assertEqual(hook.seguimiento.await_count,2)

    async def test_falla_posterior_conserva_resultado_fiscal(self):
        c={'seguimiento':'posterior'}
        with patch.object(plugins,'resolver_extension',side_effect=ValueError('no disponible')), \
                patch.object(g,'texto_carga',return_value='enlaces'):
            r=await g._con_seguimiento(self.ctx,c,{}, {'facturas':{'1':321}})
        self.assertEqual(r['facturas'],{'1':321})
        self.assertEqual(r['seguimiento']['estado'],'pendiente')

    async def test_estado_recupera_recibo_y_no_crea_otra_factura(self):
        import json
        await self.preparar(destino='principal')
        cargado=await self.aprobar(destino='principal')
        self.assertFalse(cargado.get('is_error'))
        esperadas={f['id'] for f in self.facturas()}
        antes=len(self.erps['principal']['account.move'])
        self.raiz['empresas']['taller']['seguimiento']='posterior'
        self.escribir()
        hook=NS(seguimiento=AsyncMock(return_value={'estado':'pendiente'}))
        with patch.object(plugins,'resolver_extension',return_value=hook):
            r=await g._tool_estado(self.ctx,{'lote_id':self.lote['lote_id'],'empresa':'taller','destino':'principal'})
        self.assertFalse(r.get('is_error'))
        data=json.loads(r['content'][0]['text'])
        self.assertEqual(set(data['facturas'].values()),esperadas)
        self.assertEqual(data['seguimiento']['estado'],'pendiente')
        self.assertEqual(len(self.erps['principal']['account.move']),antes)
