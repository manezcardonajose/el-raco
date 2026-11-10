import openai
import pandas as pd
import json
import os
import traceback
from ia.alergenos import detectar_alergenos

class ImportadorIA:
    """Motor de IA para importar recetas"""
    
    def __init__(self, api_key=None):
        print("🔄 Inicializando ImportadorIA...")
        try:
            from config import OPENAI_API_KEY
            self.api_key = api_key or OPENAI_API_KEY
            print(f"🔑 API Key cargada: {self.api_key[:10] if self.api_key else 'NO HAY'}...")
            
            # NUEVA SINTAXIS PARA OPENAI >= 1.0.0
            self.client = openai.OpenAI(api_key=self.api_key)
            print("✅ OpenAI configurado correctamente")
        except Exception as e:
            print(f"❌ Error cargando API key: {e}")
            self.client = None
    
    def analizar_estructura_excel(self, ruta_archivo):
        """Lee un Excel y sugiere cómo mapear las columnas"""
        print(f"📊 Analizando Excel: {ruta_archivo}")
        
        try:
            # Leer el Excel
            print("📖 Leyendo archivo Excel...")
            df = pd.read_excel(ruta_archivo, nrows=5)
            columnas = list(df.columns)
            print(f"📋 Columnas encontradas: {columnas}")
            
            # Crear muestras
            muestras = {}
            for col in columnas[:5]:
                valores = df[col].dropna().head(3).tolist()
                muestras[col] = [str(v) for v in valores]
            
            print(f"📝 Muestras: {json.dumps(muestras, indent=2, ensure_ascii=False)}")
            
            # Prompt para GPT
            prompt = f"""
            Eres un asistente experto en análisis de archivos Excel de recetas para restaurantes.
            
            El archivo tiene estas columnas: {columnas}
            
            Ejemplos de datos:
            {json.dumps(muestras, indent=2, ensure_ascii=False)}
            
            Responde SOLO con un JSON válido con esta estructura:
            {{
                "nombre_receta": "nombre_columna",
                "categoria": "nombre_columna o null",
                "alergenos": "nombre_columna o null",
                "formato_ingredientes": "unica" o "separadas",
                "columna_ingredientes": "si es 'unica', aquí el nombre",
                "columnas_ingredientes": {{
                    "nombre": "columna_nombre",
                    "cantidad": "columna_cantidad",
                    "unidad": "columna_unidad"
                }},
                "confianza": "alta", "media" o "baja"
            }}
            """
            
            print("🤖 Llamando a OpenAI...")
            
            # Verificar que el cliente existe
            if not self.client:
                print("❌ ERROR: No hay cliente OpenAI configurado")
                return self._sugerencia_por_defecto(columnas)
            
            # NUEVA SINTAXIS PARA OPENAI >= 1.0.0
            respuesta = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500
            )
            
            print("✅ Respuesta recibida de OpenAI")
            
            contenido = respuesta.choices[0].message.content
            print(f"📨 Respuesta cruda: {contenido[:200]}...")
            
            # Limpiar la respuesta
            if "```json" in contenido:
                contenido = contenido.split("```json")[1].split("```")[0]
            elif "```" in contenido:
                contenido = contenido.split("```")[1].split("```")[0]
            
            resultado = json.loads(contenido)
            print(f"✅ Análisis completado: {resultado}")
            return resultado
            
        except pd.errors.EmptyDataError:
            print("❌ El archivo Excel está vacío")
            return self._sugerencia_por_defecto([])
            
        except Exception as e:
            print(f"❌ Error en analizar_estructura_excel: {e}")
            traceback.print_exc()
            return self._sugerencia_por_defecto(columnas if 'columnas' in locals() else [])
    
    def extraer_recetas(self, ruta_archivo, mapeo):
        """Extrae todas las recetas del Excel usando el mapeo proporcionado"""
        df = pd.read_excel(ruta_archivo)
        recetas = []
        errores = []
        
        for idx, fila in df.iterrows():
            try:
                receta = {}
                
                # Nombre de la receta
                if mapeo.get('nombre_receta') and mapeo['nombre_receta'] in fila:
                    receta['nombre'] = str(fila[mapeo['nombre_receta']])
                else:
                    receta['nombre'] = f"Receta {idx+1}"
                
                # Categoría
                if mapeo.get('categoria') and mapeo['categoria'] in fila:
                    receta['categoria'] = str(fila[mapeo['categoria']])
                
                # Alérgenos
                if mapeo.get('alergenos') and mapeo['alergenos'] in fila:
                    alergenos = str(fila[mapeo['alergenos']])
                    receta['alergenos'] = self._procesar_alergenos(alergenos)
                
                # Ingredientes
                receta['ingredientes'] = []
                
                if mapeo['formato_ingredientes'] == 'unica':
                    if mapeo.get('columna_ingredientes') and mapeo['columna_ingredientes'] in fila:
                        texto = str(fila[mapeo['columna_ingredientes']])
                        receta['ingredientes'] = self._parsear_ingredientes(texto)
                else:
                    # Columnas separadas
                    if (mapeo['columnas_ingredientes'].get('nombre') and 
                        mapeo['columnas_ingredientes']['nombre'] in fila and
                        mapeo['columnas_ingredientes'].get('cantidad') and 
                        mapeo['columnas_ingredientes']['cantidad'] in fila):
                        
                        nombre = str(fila[mapeo['columnas_ingredientes']['nombre']])
                        cantidad = fila[mapeo['columnas_ingredientes']['cantidad']]
                        unidad = mapeo['columnas_ingredientes'].get('unidad')
                        
                        if unidad and unidad in fila:
                            unidad_val = str(fila[unidad])
                        else:
                            unidad_val = 'ud'
                        
                        if nombre and cantidad:
                            receta['ingredientes'].append({
                                'nombre': nombre,
                                'cantidad': float(cantidad) if cantidad else 0,
                                'unidad': unidad_val
                            })
                
                recetas.append(receta)
                
            except Exception as e:
                errores.append(f"Fila {idx+2}: {str(e)}")
        
        return recetas, errores
    
    def _procesar_alergenos(self, texto):
        """Convierte texto de alérgenos a lista"""
        return ', '.join(detectar_alergenos(texto))
        texto = texto.lower()
        alergenos = []
        
        keywords = {
            'gluten': ['gluten', 'trigo', 'cebada'],
            'lacteos': ['lácteos', 'leche', 'lactosa'],
            'huevo': ['huevo'],
            'pescado': ['pescado'],
            'crustaceos': ['crustáceos', 'gamba', 'langostino'],
            'frutos_secos': ['frutos secos', 'almendra', 'nuez']
        }
        
        for alergeno, palabras in keywords.items():
            if any(p in texto for p in palabras):
                alergenos.append(alergeno)
        
        return ','.join(alergenos)
    
    def _parsear_ingredientes(self, texto):
        """Intenta parsear un texto como 'Harina 500g, Huevos 2ud'"""
        import re
        ingredientes = []
        
        # Separar por comas
        partes = texto.split(',')
        
        for parte in partes:
            parte = parte.strip()
            if not parte:
                continue
            
            # Patrón: nombre cantidad unidad
            patron = r'([a-zA-ZáéíóúñÁÉÍÓÚÑ\s]+?)\s*(\d+[.,]?\d*)\s*([a-zA-Z]+)'
            match = re.search(patron, parte)
            
            if match:
                nombre = match.group(1).strip()
                cantidad = float(match.group(2).replace(',', '.'))
                unidad = match.group(3)
                
                ingredientes.append({
                    'nombre': nombre,
                    'cantidad': cantidad,
                    'unidad': unidad
                })
            else:
                # Si no coincide con el patrón, guardar todo como texto
                ingredientes.append({
                    'nombre': parte,
                    'cantidad': 1,
                    'unidad': 'ud'
                })
        
        return ingredientes
    
    def _sugerencia_por_defecto(self, columnas):
        """Devuelve una sugerencia por defecto cuando falla la IA"""
        print("⚠️ Usando sugerencia por defecto")
        
        # Intentar adivinar columnas por nombre
        nombre_col = None
        ingredientes_col = None
        alergenos_col = None
        
        for col in columnas:
            col_lower = col.lower()
            if 'nombre' in col_lower or 'plato' in col_lower or 'receta' in col_lower:
                nombre_col = col
            elif 'ingrediente' in col_lower:
                ingredientes_col = col
            elif 'alergeno' in col_lower:
                alergenos_col = col
        
        return {
            "nombre_receta": nombre_col or (columnas[0] if columnas else None),
            "categoria": None,
            "alergenos": alergenos_col,
            "formato_ingredientes": "unica",
            "columna_ingredientes": ingredientes_col or (columnas[1] if len(columnas) > 1 else None),
            "columnas_ingredientes": {},
            "confianza": "baja",
            "error": "Usando modo manual"
        }
